"""
End-to-end tests for the /courses/<id>/scrape/* routes (setup, start,
progress, review, save) - app.scrape_jobs.scrape_usns is monkeypatched to
a fast fake so these exercise the whole request flow (job creation,
background-thread execution, polling, review-page rendering, ExternalResult
writes) without Chrome, Tesseract, or a live VTU connection.
"""
import time

import pytest

from app import create_app
from app.models import Course, ExternalResult, Student, User, ROLE_TEACHER
from app.vtu_scraper.parser import ScrapedSubjectRow
from app.vtu_scraper.scraper import ScrapeOutcome, ScrapeRunResult


@pytest.fixture()
def client():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        test_client = app.test_client()
        test_client.post("/setup-coordinator", data={
            "name": "Test Coordinator", "password": "testpass123", "confirm_password": "testpass123",
        }, follow_redirects=True)
        yield test_client


def _build_course_with_roster(client, subject_code="T-SCRAPE"):
    client.post("/courses/new", data={
        "subject_code": subject_code, "subject_name": "Scrape Test Subject",
        "target_level1_pct": "50", "target_level2_pct": "60", "target_level3_pct": "70",
        "internal_marks_cutoff_pct": "60",
    }, follow_redirects=True)
    course_id = Course.query.filter_by(subject_code=subject_code).first().id

    client.post(f"/courses/{course_id}/marks/roster", data={
        "action": "add_bulk", "bulk_text": "1AA20CS001, Alice\n1AA20CS002, Bob",
    }, follow_redirects=True)

    return course_id


def _fake_scrape(subject_code, results_by_usn=None):
    """Builds a fake replacement for app.scrape_jobs.scrape_usns. By
    default every USN comes back with a matching subject row (internal
    30, external 55, total 85, result P) - pass results_by_usn to
    override specific USNs with a different ScrapeOutcome."""
    results_by_usn = results_by_usn or {}

    def fake(usns, results_url, headless=True, on_progress=None, **kwargs):
        outcomes = []
        for i, usn in enumerate(usns, start=1):
            if on_progress:
                on_progress(i, len(usns), usn, "done")
            if usn in results_by_usn:
                outcomes.append(results_by_usn[usn])
            else:
                outcomes.append(ScrapeOutcome(
                    usn=usn, status="ok", confirmed_usn=usn, confirmed_name="Confirmed Name",
                    subjects=[ScrapedSubjectRow(
                        semester="3", subject_code=subject_code, subject_name="Scrape Test Subject",
                        internal_marks=30, external_marks=55, total=85, result="P",
                    )],
                ))
        return ScrapeRunResult(outcomes=outcomes)

    return fake


def _wait_for_done(client, course_id, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/courses/{course_id}/scrape/progress/{job_id}/status.json")
        data = resp.get_json()
        if data["status"] != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("job never finished")


def test_setup_page_lists_the_roster(client):
    course_id = _build_course_with_roster(client)

    resp = client.get(f"/courses/{course_id}/scrape/")
    assert resp.status_code == 200
    assert b"1AA20CS001" in resp.data
    assert b"1AA20CS002" in resp.data


def test_start_rejects_a_malformed_results_url(client):
    course_id = _build_course_with_roster(client)
    students = Student.query.filter_by(course_id=course_id).all()

    resp = client.post(f"/courses/{course_id}/scrape/start", data={
        "student_id": [str(s.id) for s in students],
        "results_url": "https://example.com/not-vtu",
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert b"valid VTU results URL" in resp.data


def test_start_rejects_no_students_selected(client):
    course_id = _build_course_with_roster(client)

    resp = client.post(f"/courses/{course_id}/scrape/start", data={
        "results_url": "https://results.vtu.ac.in/MJ26cbcs/index.php",
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert b"Select at least one student" in resp.data


def test_full_flow_scrape_review_and_save(client, monkeypatch):
    course_id = _build_course_with_roster(client)
    monkeypatch.setattr("app.scrape_jobs.scrape_usns", _fake_scrape("T-SCRAPE"))

    students = Student.query.filter_by(course_id=course_id).all()
    start_resp = client.post(f"/courses/{course_id}/scrape/start", data={
        "student_id": [str(s.id) for s in students],
        "results_url": "https://results.vtu.ac.in/MJ26cbcs/index.php",
    })
    assert start_resp.status_code == 302
    job_id = start_resp.headers["Location"].rstrip("/").split("/")[-1]

    status = _wait_for_done(client, course_id, job_id)
    assert status["status"] == "done"

    review_resp = client.get(f"/courses/{course_id}/scrape/review/{job_id}")
    assert review_resp.status_code == 200
    assert b"1AA20CS001" in review_resp.data
    assert b'value="30' in review_resp.data  # internal marks prefilled
    assert b'value="55' in review_resp.data  # external marks prefilled

    save_data = {}
    for s in students:
        save_data[f"include_{s.id}"] = "on"
        save_data[f"internal_{s.id}"] = "30"
        save_data[f"external_{s.id}"] = "55"
        save_data[f"result_{s.id}"] = "P"

    save_resp = client.post(f"/courses/{course_id}/scrape/review/{job_id}/save", data=save_data, follow_redirects=True)
    assert save_resp.status_code == 200
    assert b"Saved scraped results for 2 student" in save_resp.data

    results = {r.student_id: r for r in ExternalResult.query.all()}
    for s in students:
        r = results[s.id]
        assert r.internal_total == 30.0
        assert r.external_marks == 55.0
        assert r.result == "P"


def test_review_page_handles_a_subject_not_found_gracefully(client, monkeypatch):
    """The scraped student's transcript doesn't have this course's
    subject code at all - the review page should say so, not crash, and
    that row's checkbox should not be includable."""
    course_id = _build_course_with_roster(client)
    override = {
        "1AA20CS001": ScrapeOutcome(
            usn="1AA20CS001", status="ok", confirmed_usn="1AA20CS001", confirmed_name="Alice",
            subjects=[ScrapedSubjectRow(
                semester="3", subject_code="SOME-OTHER-CODE", subject_name="A Different Subject",
                internal_marks=10, external_marks=10, total=20, result="P",
            )],
        ),
    }
    monkeypatch.setattr("app.scrape_jobs.scrape_usns", _fake_scrape("T-SCRAPE", override))

    students = Student.query.filter_by(course_id=course_id).all()
    start_resp = client.post(f"/courses/{course_id}/scrape/start", data={
        "student_id": [str(s.id) for s in students],
        "results_url": "https://results.vtu.ac.in/MJ26cbcs/index.php",
    })
    job_id = start_resp.headers["Location"].rstrip("/").split("/")[-1]
    _wait_for_done(client, course_id, job_id)

    review_resp = client.get(f"/courses/{course_id}/scrape/review/{job_id}")
    assert review_resp.status_code == 200
    assert b"no T-SCRAPE row found" in review_resp.data


def test_scrape_setup_is_denied_to_a_teacher_not_assigned_to_the_course(client):
    course_id = _build_course_with_roster(client)
    client.post("/logout", follow_redirects=True)

    app = client.application
    with app.app_context():
        teacher = User(username="Other Teacher", display_name="Other Teacher", role=ROLE_TEACHER)
        teacher.set_password("")
        from app.extensions import db
        db.session.add(teacher)
        db.session.commit()

    client.post("/login", data={"name": "Other Teacher", "password": ""}, follow_redirects=True)
    resp = client.get(f"/courses/{course_id}/scrape/")
    assert resp.status_code == 403
