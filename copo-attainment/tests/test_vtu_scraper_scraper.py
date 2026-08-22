"""
Tests for app/vtu_scraper/scraper.py's orchestration loop - retry, skip,
and abort behavior - driven against a FakeSession instead of a real
Selenium browser, so these run without Chrome or a live VTU connection.

FakeSession implements just enough of VtuSession's interface (open,
enter_usn, solve_and_submit_captcha, is_stuck_on_entry_page, refresh,
reload, sleep, page_soup, confirmed_usn_and_name, wait_for_alert, close)
to drive scrape_usns() through each of its branches on demand.
"""
from bs4 import BeautifulSoup

from app.vtu_scraper.browser import AlertPresentException
from app.vtu_scraper.scraper import INVALID_USN_ALERT_TEXT, scrape_usns


class FakeAlert:
    def __init__(self, text):
        self.text = text
        self.accepted = False

    def accept(self):
        self.accepted = True


ONE_SUBJECT_HTML = (
    '<div style="text-align:center;padding:5px;">Semester : 3</div>'
    '<div class="divTable">'
    '<div class="divTableRow">'
    '<div class="divTableCell">Subject Code</div><div class="divTableCell">Subject Name</div>'
    '<div class="divTableCell">Internal Marks</div><div class="divTableCell">External Marks</div>'
    '<div class="divTableCell">Total</div><div class="divTableCell">Result</div>'
    "</div>"
    '<div class="divTableRow">'
    '<div class="divTableCell">21PHY22</div><div class="divTableCell">Engineering Physics</div>'
    '<div class="divTableCell">45</div><div class="divTableCell">62</div>'
    '<div class="divTableCell">107</div><div class="divTableCell">P</div>'
    "</div></div>"
)


class FakeSession:
    """Scripted per-USN behavior: `scripts[usn]` is a list of "steps" that
    solve_and_submit_captcha()/is_stuck_on_entry_page() consult in order,
    letting a test simulate "bad captcha, then success" or "raises the
    invalid-USN alert" without any real browser."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.opened_url = None
        self.closed = False
        self.reload_calls = 0
        self.sleep_calls = []
        self._current_usn = None
        self._step_index = {}  # usn -> next step to consume, across retries

    def open(self, url):
        self.opened_url = url

    def reload(self, url):
        self.reload_calls += 1

    def refresh(self):
        pass

    def close(self):
        self.closed = True

    def enter_usn(self, usn):
        # Entering a USN into the form happens on every retry attempt for
        # the *same* USN too (the page gets refreshed between attempts,
        # clearing the field) - only initialize a fresh step counter the
        # first time we ever see this usn, so scripted multi-step
        # behaviors (e.g. "bad captcha, then ok") survive across retries.
        self._current_usn = usn
        self._step_index.setdefault(usn, 0)

    def _next_step(self):
        steps = self.scripts[self._current_usn]
        idx = self._step_index[self._current_usn]
        step = steps[min(idx, len(steps) - 1)]
        self._step_index[self._current_usn] = idx + 1
        return step

    def solve_and_submit_captcha(self):
        step = self._next_step()
        if step[0] == "alert":
            raise AlertPresentException()
        if step[0] == "bad_captcha":
            return "abc"  # not 6 chars
        return "AB12CD"  # a valid-looking 6 char read

    def is_stuck_on_entry_page(self):
        # The step consumed by solve_and_submit_captcha already told us
        # whether this attempt is the "ok" one.
        last_idx = self._step_index[self._current_usn] - 1
        return self.scripts[self._current_usn][last_idx][0] == "stuck"

    def wait_for_alert(self):
        last_idx = self._step_index[self._current_usn] - 1
        step = self.scripts[self._current_usn][last_idx]
        return FakeAlert(step[1])

    def page_soup(self):
        return BeautifulSoup(f"<html><body>{ONE_SUBJECT_HTML}</body></html>", "lxml")

    def confirmed_usn_and_name(self, soup):
        return self._current_usn, "TEST STUDENT"

    def sleep(self, secs):
        self.sleep_calls.append(secs)


def test_happy_path_scrapes_every_usn():
    session = FakeSession({"U1": [("ok",)], "U2": [("ok",)]})

    result = scrape_usns(["U1", "U2"], "https://results.vtu.ac.in/x/index.php", session=session, inter_usn_sleep_secs=0)

    assert not result.aborted
    assert [o.status for o in result.outcomes] == ["ok", "ok"]
    assert result.outcomes[0].subjects[0].subject_code == "21PHY22"
    assert session.closed  # scrape_usns closes whatever session it used, ours included


def test_bad_captcha_read_is_retried_then_succeeds():
    session = FakeSession({"U1": [("bad_captcha",), ("ok",)]})

    result = scrape_usns(["U1"], "https://x/index.php", session=session, inter_usn_sleep_secs=0, retries=3)

    assert result.outcomes[0].status == "ok"


def test_invalid_usn_alert_is_skipped_not_fatal():
    session = FakeSession({
        "BAD": [("alert", INVALID_USN_ALERT_TEXT)],
        "GOOD": [("ok",)],
    })

    result = scrape_usns(["BAD", "GOOD"], "https://x/index.php", session=session, inter_usn_sleep_secs=0)

    assert not result.aborted
    assert result.outcomes[0].status == "invalid"
    assert result.outcomes[1].status == "ok"


def test_empty_alert_text_aborts_the_whole_run_as_ip_block():
    session = FakeSession({
        "U1": [("alert", "")],
        "U2": [("ok",)],  # should never be reached
    })

    result = scrape_usns(["U1", "U2"], "https://x/index.php", session=session, inter_usn_sleep_secs=0)

    assert result.aborted
    assert "block" in result.abort_reason.lower()
    assert len(result.outcomes) == 0  # aborted before U1 even got recorded


def test_exhausting_retries_records_an_error_outcome_and_continues():
    session = FakeSession({
        "U1": [("stuck",)],  # never succeeds, every attempt is "stuck"
        "U2": [("ok",)],
    })

    result = scrape_usns(["U1", "U2"], "https://x/index.php", session=session, inter_usn_sleep_secs=0, retries=2)

    assert not result.aborted
    assert result.outcomes[0].status == "error"
    assert result.outcomes[1].status == "ok"


def test_progress_callback_is_invoked_with_1_based_index():
    calls = []
    session = FakeSession({"U1": [("ok",)], "U2": [("ok",)]})

    scrape_usns(
        ["U1", "U2"], "https://x/index.php", session=session, inter_usn_sleep_secs=0,
        on_progress=lambda index, total, usn, message: calls.append((index, total, usn)),
    )

    assert calls[0][:2] == (1, 2)
    assert calls[-1][:2] == (2, 2)
