"""
Scrapes a student's published VTU semester results (results.vtu.ac.in)
and turns them into structured per-subject marks this app can use to
populate ExternalResult rows - an alternative to typing them in by hand
on the /marks/EXTERNAL page.

Adapted from the scraping approach in the open-source
nithinhm/vtu-marks-scraper-analyzer (GPL v2 - see that repo's LICENSE):
same VTU form field names/selectors and the same local-OCR CAPTCHA
solve, rewritten here as plain importable functions instead of a Tkinter
GUI app, and returning structured data in memory instead of writing CSVs.

    app.vtu_scraper.captcha  - CAPTCHA image -> text, via local Tesseract OCR
    app.vtu_scraper.browser  - one Selenium session against the results portal
    app.vtu_scraper.parser   - scraped page -> structured per-subject rows
    app.vtu_scraper.scraper  - orchestrates the above over a batch of USNs

See app/scrape_jobs.py for how a batch scrape is run as a background job,
and app/routes/scrape_routes.py for the web UI on top of that.

Not yet tested against the live VTU site (the environment this was built
in has no network route out to results.vtu.ac.in) - see the docstrings in
browser.py and parser.py for exactly what to check on a first live run,
and scraper.py's docstring for what a failure there looks like.
"""
from app.vtu_scraper.scraper import ScrapeOutcome, ScrapeRunResult, scrape_usns

__all__ = ["scrape_usns", "ScrapeOutcome", "ScrapeRunResult"]
