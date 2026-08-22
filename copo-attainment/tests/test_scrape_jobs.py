"""
Tests for app/scrape_jobs.py's background-job registry, in isolation from
the Flask routes and from the real VTU scraper - app.scrape_jobs.scrape_usns
is monkeypatched to a fast fake so these run instantly and without Chrome,
Tesseract, or network access.
"""
import time

import pytest

from app import create_app
from app.vtu_scraper.parser import ScrapedSubjectRow
from app.vtu_scraper.scraper import ScrapeOutcome, ScrapeRunResult


@pytest.fixture()
def app():
    return create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})


def _wait_until_done(job_id, timeout=5):
    from app.scrape_jobs import STATUS_RUNNING, get_job

    deadline = time.time() + timeout
    job = get_job(job_id)
    while job["status"] == STATUS_RUNNING and time.time() < deadline:
        time.sleep(0.02)
        job = get_job(job_id)
    return job


def test_start_job_runs_to_completion_and_records_the_result(app, monkeypatch):
    def fake_scrape_usns(usns, results_url, headless=True, on_progress=None, **kwargs):
        for i, usn in enumerate(usns, start=1):
            if on_progress:
                on_progress(i, len(usns), usn, "ok")
        outcomes = [ScrapeOutcome(usn=u, status="ok", confirmed_usn=u, confirmed_name="X", subjects=[]) for u in usns]
        return ScrapeRunResult(outcomes=outcomes)

    monkeypatch.setattr("app.scrape_jobs.scrape_usns", fake_scrape_usns)

    from app.scrape_jobs import STATUS_DONE, start_job

    job_id = start_job(app, course_id=1, usns=["1AA1"], results_url="https://results.vtu.ac.in/x/index.php")
    job = _wait_until_done(job_id)

    assert job["status"] == STATUS_DONE
    assert job["processed"] == 1
    assert job["total"] == 1
    assert job["result"].outcomes[0].usn == "1AA1"
    assert any("1AA1" in line for line in job["log"])


def test_start_job_records_abort_status(app, monkeypatch):
    def fake_scrape_usns(usns, results_url, headless=True, on_progress=None, **kwargs):
        return ScrapeRunResult(outcomes=[], aborted=True, abort_reason="pretend IP block")

    monkeypatch.setattr("app.scrape_jobs.scrape_usns", fake_scrape_usns)

    from app.scrape_jobs import STATUS_ABORTED, start_job

    job_id = start_job(app, course_id=1, usns=["1AA1"], results_url="https://results.vtu.ac.in/x/index.php")
    job = _wait_until_done(job_id)

    assert job["status"] == STATUS_ABORTED
    assert job["result"].abort_reason == "pretend IP block"


def test_start_job_records_error_status_on_a_hard_crash(app, monkeypatch):
    def fake_scrape_usns(*args, **kwargs):
        raise RuntimeError("selenium blew up")

    monkeypatch.setattr("app.scrape_jobs.scrape_usns", fake_scrape_usns)

    from app.scrape_jobs import STATUS_ERROR, start_job

    job_id = start_job(app, course_id=1, usns=["1AA1"], results_url="https://results.vtu.ac.in/x/index.php")
    job = _wait_until_done(job_id)

    assert job["status"] == STATUS_ERROR
    assert "selenium blew up" in job["error"]


def test_get_job_returns_none_for_an_unknown_id():
    from app.scrape_jobs import get_job

    assert get_job("does-not-exist") is None


def test_jobs_are_scoped_by_the_course_id_they_were_started_with(app, monkeypatch):
    def fake_scrape_usns(usns, results_url, headless=True, on_progress=None, **kwargs):
        return ScrapeRunResult(outcomes=[])

    monkeypatch.setattr("app.scrape_jobs.scrape_usns", fake_scrape_usns)

    from app.scrape_jobs import get_job, start_job

    job_id = start_job(app, course_id=42, usns=["1AA1"], results_url="https://results.vtu.ac.in/x/index.php")
    job = _wait_until_done(job_id)

    assert job["course_id"] == 42
