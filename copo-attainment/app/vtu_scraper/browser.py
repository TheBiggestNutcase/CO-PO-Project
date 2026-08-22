"""
A thin Selenium wrapper around one browsing session against VTU's public
results portal (https://results.vtu.ac.in/<cycle-slug>/index.php) - enter
a USN, solve+submit the CAPTCHA, and hand back the resulting page as a
BeautifulSoup for app.vtu_scraper.parser to turn into structured marks.

Adapted from nithinhm/vtu-marks-scraper-analyzer's connection.py (GPL v2):
same form field names/selectors (VTU's own markup, not this project's
choice), same "stuck on the entry page = something went wrong" detection.
The one thing NOT reused as-is is get_info()'s reliance on an absolute
XPath to read back the confirmed USN/name off the results page - that
repo's own maintainer notes it has already broken and been re-patched
once before ("XPath changed 27-05-2026"), so here it's a best-effort
primary lookup with a soup-based fallback, and either failing just means
we fall back to the USN we typed in ourselves rather than aborting.

IMPORTANT - this could not be tested against the live VTU site from the
sandbox this was built in (no network route out to results.vtu.ac.in).
The field names/selectors below are current as of this repo's last
update, but VTU changes their page HTML periodically (confirmed by that
repo's own commit history) - a live first run against a real course
roster is the way to confirm this still matches, and app/vtu_scraper's
docstrings call out exactly which bits to fix if it doesn't.
"""
import os
import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, UnexpectedAlertPresentException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from app.vtu_scraper.captcha import CaptchaSolver

# Re-exported so callers (app/vtu_scraper/scraper.py) can catch this
# specific exception without importing selenium.common.exceptions
# themselves.
AlertPresentException = UnexpectedAlertPresentException


class StuckOnEntryPage(Exception):
    """The page we're looking at is still the USN-entry form, not a
    results page - i.e. the submission didn't go through (bad captcha,
    transient error, etc). Caller should retry."""


class VtuSession:
    """One Chrome session, reused across many USN lookups in a batch (VTU
    itself doesn't seem to require a fresh session per USN - only a fresh
    CAPTCHA, which means reloading the *page*, not the browser)."""

    def __init__(self, headless=True, captcha_solver=None):
        self._headless = headless
        self._captcha_solver = captcha_solver or CaptchaSolver()
        self.driver = None

    def open(self, results_url):
        options = Options()
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        options.unhandled_prompt_behavior = "ignore"
        if self._headless:
            options.add_argument("--headless=new")
            # Required for headless Chrome running as root in a container
            # (Docker deploys - see Dockerfile/DEPLOYMENT.md): without
            # these, Chrome fails to start at all rather than just being
            # slower. Harmless on a normal Mac/Windows dev machine too.
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin:
            options.binary_location = chrome_bin
        self.driver = webdriver.Chrome(options=options)
        self.driver.get(results_url)

    def reload(self, results_url):
        """Reset back to a fresh USN-entry form (and a fresh captcha) -
        called between USNs, and after a bad captcha read."""
        self.driver.get(results_url)

    def refresh(self):
        self.driver.refresh()

    def close(self):
        if self.driver is not None:
            self.driver.quit()
            self.driver = None

    def enter_usn(self, usn):
        self.driver.find_element(By.NAME, "lns").send_keys(usn)

    def solve_and_submit_captcha(self):
        """Screenshots the captcha image, OCRs it, and submits the form.
        Returns the OCR'd text (caller decides whether it looks valid -
        VTU's captchas are always 6 characters, so anything else is a
        bad read worth retrying against a reloaded page rather than
        submitting garbage)."""
        captcha_image = self.driver.find_element(By.CSS_SELECTOR, '[alt="CAPTCHA code"]').screenshot_as_png
        text = self._captcha_solver.solve(captcha_image)
        self.driver.find_element(By.NAME, "captchacode").send_keys(text)
        self.driver.find_element(By.ID, "submit").click()
        return text

    def is_stuck_on_entry_page(self):
        """True if the current page is still the USN-entry form (VTU
        marks that page with a "University Seat Number" label next to
        the input box) rather than a results page - i.e. our last
        submission didn't succeed."""
        soup = BeautifulSoup(self.driver.page_source, "lxml")
        return bool(soup.find_all("b", string="University Seat Number"))

    def wait_for_alert(self, timeout=1):
        return WebDriverWait(self.driver, timeout).until(EC.alert_is_present())

    def page_soup(self):
        return BeautifulSoup(self.driver.page_source, "lxml")

    def confirmed_usn_and_name(self, soup):
        """Best-effort read of the USN/name VTU echoes back on the
        results page, confirming we're looking at the right student's
        data. Tries the exact spot the source repo's absolute XPath
        pointed at; if that's drifted (VTU changed their layout), falls
        back to a label-based soup search. Returns (usn, name), either
        of which may be None if neither approach found it - callers
        should fall back to the USN they typed in themselves rather than
        treat that as fatal."""
        usn, name = None, None
        try:
            usn = self.driver.find_element(
                By.XPATH,
                "/html/body/div[2]/div[2]/div[2]/div/div/div[2]/div/div/div[1]/div/div/div/div[1]/div[2]",
            ).text.strip().upper()
            name = self.driver.find_element(
                By.XPATH,
                "/html/body/div[2]/div[2]/div[2]/div/div/div[2]/div/div/div[1]/div/div/div/div[2]/div[2]",
            ).text.strip().upper()
        except NoSuchElementException:
            usn, name = self._confirmed_usn_and_name_from_soup(soup)
        return usn, name

    @staticmethod
    def _confirmed_usn_and_name_from_soup(soup):
        usn, name = None, None
        usn_label = soup.find("b", string="University Seat Number")
        if usn_label is not None:
            value_div = usn_label.find_parent("div")
            if value_div is not None:
                sibling = value_div.find_next_sibling("div")
                if sibling is not None:
                    usn = sibling.get_text(strip=True).upper() or None
        name_label = soup.find("b", string="Student Name")
        if name_label is not None:
            value_div = name_label.find_parent("div")
            if value_div is not None:
                sibling = value_div.find_next_sibling("div")
                if sibling is not None:
                    name = sibling.get_text(strip=True).upper() or None
        return usn, name

    def sleep(self, seconds):
        time.sleep(seconds)
