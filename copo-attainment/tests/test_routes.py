"""
Route-level smoke tests, run through Flask's test client (no live server
needed). These exercise the actual HTTP flow a faculty member would use -
create course -> COs -> POs -> mapping -> structure -> roster -> marks ->
results - and catch integration bugs the unit tests can't (wrong redirect,
missing auto-created rows, template errors).
"""
import io
import os

import pytest

from app import create_app
from app.models import Course, CourseOutcome, ProgramOutcome, ExternalResult

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture()
def client():
    """A logged-in Unit Coordinator's test client. Every route in this file
    was written/tested before login existed, so we bootstrap the one-time
    coordinator account and log in before handing back the client - without
    this, the app's global before_request would bounce every request here
    to the setup/login screen instead of the route under test."""
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        test_client = app.test_client()
        test_client.post("/setup-coordinator", data={
            "name": "Test Coordinator", "password": "testpass123", "confirm_password": "testpass123",
        }, follow_redirects=True)
        yield test_client


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


def test_syllabus_import_full_flow_creates_course_and_cos(client):
    """Upload -> review (pre-filled, editable) -> confirm should create the
    course with the parsed COs, and nothing should be written to the
    database before "confirm" is submitted."""
    with open(os.path.join(FIXTURES, "sample_syllabus_che.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parse_resp = client.post(
        "/courses/import-syllabus/parse",
        data={"syllabus": (io.BytesIO(pdf_bytes), "sample_syllabus_che.pdf")},
        content_type="multipart/form-data",
    )
    assert parse_resp.status_code == 200
    assert b"21CHE12/22" in parse_resp.data
    assert b"ENGINEERING CHEMISTRY" in parse_resp.data
    # Nothing should be saved just from parsing/reviewing.
    assert Course.query.count() == 0

    confirm_resp = client.post(
        "/courses/import-syllabus/confirm",
        data={
            "subject_code": "21CHE12/22", "subject_name": "Engineering Chemistry",
            "institution_name": "AMC Engineering College", "department": "Chemistry",
            "faculty_name": "Test Faculty", "academic_year": "2025-26", "semester": "I",
            "target_level1_pct": "50", "target_level2_pct": "60", "target_level3_pct": "70",
            "internal_marks_cutoff_pct": "60",
            "co_code": ["CO1", "CO2", "CO3", "CO4", "CO5", ""],
            "co_description": [
                "Discuss the electrochemical energy systems such as electrodes and batteries.",
                "Explain the fundamental concepts of corrosion, its control and surface modification methods namely electroplating and electroless plating",
                "Enumerate the importance, synthesis and applications of polymers.",
                "Describe the principles of green chemistry.",
                "Illustrate the fundamental principles of water chemistry.",
                "",  # a spare, unused row
            ],
        },
        follow_redirects=True,
    )
    assert confirm_resp.status_code == 200

    course = Course.query.filter_by(subject_code="21CHE12/22").first()
    assert course is not None
    assert course.subject_name == "Engineering Chemistry"
    cos = CourseOutcome.query.filter_by(course_id=course.id).order_by(CourseOutcome.seq).all()
    assert [co.code for co in cos] == ["CO1", "CO2", "CO3", "CO4", "CO5"]
    assert "electroplating" in cos[1].description


def test_syllabus_import_rejects_non_pdf_upload(client):
    resp = client.post(
        "/courses/import-syllabus/parse",
        data={"syllabus": (io.BytesIO(b"not a pdf"), "notes.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"doesn&#39;t look like a PDF" in resp.data or b"doesn't look like a PDF" in resp.data
    assert Course.query.count() == 0


def test_roster_import_full_flow_imports_one_section_only(client):
    """Upload -> review (one form per detected section) -> confirm just
    Section B should add only Section B's students to this course's
    roster, leaving the other three sections out entirely."""
    from app.models import Student

    client.post("/courses/new", data={
        "subject_code": "21CS33", "subject_name": "Analog and Digital Electronics", "section": "B",
    }, follow_redirects=True)
    course_id = Course.query.first().id

    with open(os.path.join(FIXTURES, "sample_roster.docx"), "rb") as f:
        docx_bytes = f.read()

    parse_resp = client.post(
        f"/courses/{course_id}/marks/roster/import/parse",
        data={"roster": (io.BytesIO(docx_bytes), "sample_roster.docx")},
        content_type="multipart/form-data",
    )
    assert parse_resp.status_code == 200
    assert b"Section A" in parse_resp.data
    assert b"Section B" in parse_resp.data
    assert b"Section D" in parse_resp.data
    assert b"Matches this course" in parse_resp.data  # course.section == "B"
    assert Student.query.count() == 0  # nothing saved just from parsing/reviewing

    # Simulate submitting only Section B's form: its USNs/names, taken
    # straight from the parsed data (39th-38th student onward included).
    from app.roster_import import parse_roster_docx
    with open(os.path.join(FIXTURES, "sample_roster.docx"), "rb") as f:
        parsed = parse_roster_docx(f)
    section_b = next(s for s in parsed.sections if s.label == "B")

    confirm_resp = client.post(
        f"/courses/{course_id}/marks/roster/import/confirm",
        data={
            "section_label": "B",
            "usn": [s.usn for s in section_b.students],
            "name": [s.name for s in section_b.students],
        },
        follow_redirects=True,
    )
    assert confirm_resp.status_code == 200

    students = Student.query.filter_by(course_id=course_id).all()
    assert len(students) == len(section_b.students) == 69
    assert Student.query.filter_by(course_id=course_id, usn="1AM23CS064").first() is not None
    # Section A's students must not have been imported.
    assert Student.query.filter_by(course_id=course_id, usn="1AM23CS040").first() is None


def test_roster_import_skips_duplicate_usns_already_on_roster(client):
    from app.models import Student

    client.post("/courses/new", data={"subject_code": "T2", "subject_name": "Test"}, follow_redirects=True)
    course_id = Course.query.first().id
    client.post(f"/courses/{course_id}/marks/roster",
                data={"action": "add_one", "usn": "1AM23CS064", "name": "Already Here"}, follow_redirects=True)

    resp = client.post(
        f"/courses/{course_id}/marks/roster/import/confirm",
        data={
            "section_label": "B",
            "usn": ["1AM23CS064", "1AM23CS999"],
            "name": ["Duplicate Student", "New Student"],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    students = Student.query.filter_by(course_id=course_id).all()
    assert len(students) == 2  # the original + the one genuinely new row
    assert Student.query.filter_by(course_id=course_id, usn="1AM23CS064").first().name == "Already Here"


def test_roster_import_rejects_non_docx_upload(client):
    client.post("/courses/new", data={"subject_code": "T3", "subject_name": "Test"}, follow_redirects=True)
    course_id = Course.query.first().id

    resp = client.post(
        f"/courses/{course_id}/marks/roster/import/parse",
        data={"roster": (io.BytesIO(b"%PDF-1.4 not really"), "roster.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"PDF rosters aren&#39;t supported yet" in resp.data or b"PDF rosters aren't supported yet" in resp.data
