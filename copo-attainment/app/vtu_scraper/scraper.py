"""
Orchestrates a batch scrape over a list of USNs against VTU's results
portal: one browser session, reused across USNs (only the CAPTCHA needs
to be fresh per lookup, not the whole browser), with the same retry/
skip/abort rules as the loop this was adapted from - main.py's
`ScraperFrame.start_scraping` in nithinhm/vtu-marks-scraper-analyzer
(GPL v2). What's different here: that loop is a Tkinter GUI method that
writes CSVs and pops up messageboxes; this is a plain function that
takes an on_progress callback and returns structured, in-memory results,
so it can run inside a background thread (see app/scrape_jobs.py) with a
web UI polling its progress instead.

`session` is injectable (defaults to a real app.vtu_scraper.browser.
VtuSession) specifically so this can be unit-tested without Chrome or a
live VTU connection - see tests/test_vtu_scraper_scraper.py, which drives
this against a fake in-memory session.

Rules preserved from the source loop, because they're load-bearing for
not getting VTU's whole result-cycle URL blocked for your institution's
IP (per that repo's own README warning):
  - ~2 second sleep after each successfully scraped USN
  - an extra 3 second cooldown after 5 consecutive soft errors
  - an empty-text alert is treated as an IP ban and aborts the entire run
  - "USN invalid" / "reval not applied" alerts just skip to the next USN
"""
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from app.vtu_scraper.browser import AlertPresentException, VtuSession
from app.vtu_scraper.parser import ScrapedSubjectRow, parse_result_page

INVALID_USN_ALERT_TEXT = "University Seat Number is not available or Invalid..!"
NOT_APPLIED_RVL_ALERT_TEXT = "You have not applied for reval or reval results are awaited !!!"
SKIPPABLE_ALERT_TEXTS = (INVALID_USN_ALERT_TEXT, NOT_APPLIED_RVL_ALERT_TEXT)


@dataclass
class ScrapeOutcome:
    usn: str  # the USN as given to us
    status: str  # "ok" | "invalid" | "error"
    confirmed_usn: Optional[str] = None
    confirmed_name: Optional[str] = None
    subjects: List[ScrapedSubjectRow] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ScrapeRunResult:
    outcomes: List[ScrapeOutcome]
    aborted: bool = False
    abort_reason: Optional[str] = None


def scrape_usns(
    usns,
    results_url,
    headless=True,
    retries=3,
    retry_delay_secs=5,
    inter_usn_sleep_secs=2,
    on_progress: Optional[Callable[[int, int, str, str], None]] = None,
    session=None,
):
    """Scrapes `usns` (a list of full USN strings) against `results_url`
    (VTU's own per-exam-cycle results page, e.g.
    https://results.vtu.ac.in/MJ26cbcs/index.php).

    on_progress(index, total, usn, message), if given, is called after
    each USN is resolved (or skipped/failed) - `index` is 1-based. Use it
    to drive a progress bar/log without this function needing to know
    anything about how progress is displayed.

    Returns a ScrapeRunResult. Ownership of the browser session is
    entirely internal - by the time this returns, the session has been
    closed either way (success, per-USN failure, or an aborting IP
    block)."""
    session = session or VtuSession(headless=headless)

    outcomes = []
    aborted = False
    abort_reason = None
    total = len(usns)
    cool_counter = 0

    try:
        session.open(results_url)

        for index, usn in enumerate(usns, start=1):
            outcome = None

            for attempt in range(retries):
                try:
                    session.enter_usn(usn)
                    captcha_text = session.solve_and_submit_captcha()

                    if len(captcha_text) != 6:
                        if on_progress:
                            on_progress(index, total, usn, "Bad CAPTCHA read, retrying.")
                        session.refresh()
                        continue

                    session.sleep(0.1)

                    if session.is_stuck_on_entry_page():
                        if on_progress:
                            on_progress(index, total, usn, "Submission didn't go through, retrying.")
                        session.refresh()
                        continue

                    soup = session.page_soup()
                    confirmed_usn, confirmed_name = session.confirmed_usn_and_name(soup)
                    subjects = parse_result_page(soup)
                    outcome = ScrapeOutcome(
                        usn=usn, status="ok",
                        confirmed_usn=confirmed_usn or usn, confirmed_name=confirmed_name,
                        subjects=subjects,
                    )
                    cool_counter = 0
                    if on_progress:
                        on_progress(index, total, usn, "Scraped successfully.")
                    break

                except AlertPresentException:
                    alert = session.wait_for_alert()
                    alert_text = alert.text
                    if alert_text in SKIPPABLE_ALERT_TEXTS:
                        outcome = ScrapeOutcome(usn=usn, status="invalid", error=alert_text)
                        alert.accept()
                        cool_counter += 1
                        if on_progress:
                            on_progress(index, total, usn, f"Skipped: {alert_text}")
                        break
                    elif alert_text == "":
                        alert.accept()
                        aborted = True
                        abort_reason = (
                            "This IP address appears to have been blocked by VTU for making too "
                            "many requests. Wait a while (or use a different network) before "
                            "trying again."
                        )
                        if on_progress:
                            on_progress(index, total, usn, abort_reason)
                        break
                    else:
                        alert.accept()
                        cool_counter += 1
                        if on_progress:
                            on_progress(index, total, usn, f"Unexpected alert ({alert_text}), retrying.")

                except Exception as e:  # noqa: BLE001 - deliberately broad, mirrors the source loop's own catch-all
                    if on_progress:
                        on_progress(
                            index, total, usn,
                            f"Error, retrying in {retry_delay_secs}s (attempt {attempt + 1}/{retries}): {e}",
                        )
                    time.sleep(retry_delay_secs)
                    try:
                        session.refresh()
                    except Exception:
                        pass

                if cool_counter > 5:
                    if on_progress:
                        on_progress(index, total, usn, "Pausing briefly to avoid rate limiting.")
                    cool_counter = 0
                    session.sleep(3)

            if aborted:
                break

            if outcome is None:
                outcome = ScrapeOutcome(usn=usn, status="error", error=f"Gave up after {retries} attempts.")
                if on_progress:
                    on_progress(index, total, usn, outcome.error)

            outcomes.append(outcome)

            session.sleep(inter_usn_sleep_secs)
            if index < total:
                session.reload(results_url)

    finally:
        session.close()

    return ScrapeRunResult(outcomes=outcomes, aborted=aborted, abort_reason=abort_reason)
