"""
Route-level smoke tests, run through Flask's test client (no live server
needed). These exercise the actual HTTP flow a faculty member would use -
create course -> COs -> POs -> mapping -> structure -> roster -> marks ->
results - and catch integration bugs the unit tests can't (wrong redirect,
missing auto-created rows, template errors).
"""
import pytest

from app import create_app
from app.models import Course, CourseOutcome, ProgramOutcome, ExternalResult


@pytest.fixture()
def client():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        yield app.test_client()


def test_external_marks_entry_works_without_visiting_structure_first(client):
    """Regression test: EXTERNAL never has an item structure, so entering
    its marks should work on the very first visit - it must not silently
    bounce the request to the (irrelevant) Structure page because the
    component row hadn't been created yet."""
    client.post("/courses/new", data={"subject_code": "T1", "subject_name": "Test"}, follow_redirects=True)
    course_id = Course.query.first().id
    client.post(f"/courses/{course_id}/marks/roster",
                data={"action": "add_one", "usn": "U1", "name": "Alice"}, follow_redirects=True)
    from app.models import Student
    student_id = Student.query.filter_by(course_id=course_id).first().id

    resp = client.post(
        f"/courses/{course_id}/marks/EXTERNAL",
        data={f"external_{student_id}": "70", f"internal_{student_id}": "30", f"result_{student_id}": "P"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    saved = ExternalResult.query.filter_by(student_id=student_id).first()
    assert saved is not None, "External result was not saved - the auto-create-on-first-use fix regressed"
    assert saved.external_marks == 70.0
    assert saved.result == "P"


def test_full_setup_flow_smoke(client):
    """One course through every screen, checking nothing 500s and results
    render once everything's in place."""
    client.post("/courses/new", data={
        "subject_code": "21PHY22", "subject_name": "Engineering Physics",
        "target_level1_pct": "50", "target_level2_pct": "60", "target_level3_pct": "70",
        "internal_marks_cutoff_pct": "60",
    }, follow_redirects=True)
    course_id = Course.query.first().id

    for i in range(1, 3):
        client.post(f"/courses/{course_id}/outcomes", data={"code": f"CO{i}"}, follow_redirects=True)
    client.post(f"/courses/{course_id}/program-outcomes", data={"action": "add_standard"}, follow_redirects=True)

    cos = CourseOutcome.query.filter_by(course_id=course_id).order_by(CourseOutcome.seq).all()
    pos = ProgramOutcome.query.filter_by(course_id=course_id).order_by(ProgramOutcome.seq).all()

    client.post(f"/courses/{course_id}/mapping", data={
        f"corr_{cos[0].id}_{pos[0].id}": "3",
        f"corr_{cos[1].id}_{pos[0].id}": "2",
    }, follow_redirects=True)

    client.post(f"/courses/{course_id}/structure/IA1",
                data={"label": "1A", "max_marks": "10", "co_ids": [str(cos[0].id)]}, follow_redirects=True)
    client.post(f"/courses/{course_id}/structure/EXIT_SURVEY",
                data={"label": "Q1", "co_ids": [str(cos[0].id)]}, follow_redirects=True)

    client.post(f"/courses/{course_id}/marks/roster",
                data={"action": "add_bulk", "bulk_text": "U1, Alice\nU2, Bob"}, follow_redirects=True)

    resp = client.get(f"/courses/{course_id}/results/")
    assert resp.status_code == 200
    assert b"CO1" in resp.data
    assert b"PO1" in resp.data
