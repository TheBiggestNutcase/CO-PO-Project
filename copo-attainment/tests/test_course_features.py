"""
Tests for the newer course-setup features that don't fit naturally into
the other test files: the IA main-question grouping/20-mark cap at
structure-entry time, faculty display sourced from Teacher accounts, the
downloadable exit-survey question template, and the multi-section course
model (Course.sections_list).
"""
import io

import pytest
from openpyxl import load_workbook

from app import create_app
from app.extensions import db
from app.models import Course, CourseOutcome, CourseTeacher, User, ROLE_COORDINATOR, ROLE_TEACHER


@pytest.fixture()
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    yield app


@pytest.fixture()
def client(app):
    """A logged-in Unit Coordinator's test client, with the coordinator
    account kept around (as `client.coordinator`) so tests can check
    Course.coordinator_id was set from it."""
    with app.app_context():
        coordinator = User(username="Test Coordinator", display_name="Test Coordinator", role=ROLE_COORDINATOR)
        coordinator.set_password("testpass123")
        db.session.add(coordinator)
        db.session.commit()
        coordinator_id = coordinator.id

        test_client = app.test_client()
        test_client.post("/login", data={
            "name": "Test Coordinator", "password": "testpass123",
        }, follow_redirects=True)
        test_client.coordinator_id = coordinator_id
        yield test_client


def _new_course(client, **overrides):
    data = {"subject_code": "T1", "subject_name": "Test Subject"}
    data.update(overrides)
    client.post("/courses/new", data=data, follow_redirects=True)
    return Course.query.filter_by(subject_code=data["subject_code"]).first().id


# ----------------------------------------------------- IA structure/cap

def test_ia_item_requires_a_main_question(client):
    course_id = _new_course(client)
    resp = client.post(f"/courses/{course_id}/structure/IA1", data={
        "label": "1a", "max_marks": "10", "co_ids": [],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Pick which main question" in resp.data
    from app.models import AssessmentItem
    assert AssessmentItem.query.count() == 0


def test_ia_main_question_marks_are_capped_at_20(client):
    course_id = _new_course(client)
    client.post(f"/courses/{course_id}/structure/IA1", data={
        "label": "1a", "max_marks": "12", "main_question": "1", "co_ids": [],
    }, follow_redirects=True)
    resp = client.post(f"/courses/{course_id}/structure/IA1", data={
        "label": "1b", "max_marks": "10", "main_question": "1", "co_ids": [],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"cap per main question" in resp.data

    from app.models import AssessmentItem
    items = AssessmentItem.query.all()
    assert len(items) == 1  # the second (over-cap) item was rejected
    assert items[0].label == "1a"


def test_ia_main_question_accepts_up_to_exactly_20(client):
    course_id = _new_course(client)
    client.post(f"/courses/{course_id}/structure/IA1", data={
        "label": "1a", "max_marks": "12", "main_question": "1", "co_ids": [],
    }, follow_redirects=True)
    resp = client.post(f"/courses/{course_id}/structure/IA1", data={
        "label": "1b", "max_marks": "8", "main_question": "1", "co_ids": [],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"cap per main question" not in resp.data

    from app.models import AssessmentItem
    assert AssessmentItem.query.count() == 2


def test_asqm_items_are_not_subject_to_main_question_grouping(client):
    """The main-question/20-cap rule is an IA-specific thing (a real IA
    paper's structure) - ASQM (self-study) items keep working exactly as
    before, with no main_question field required."""
    course_id = _new_course(client)
    resp = client.post(f"/courses/{course_id}/structure/ASQM", data={
        "label": "A1", "max_marks": "25", "co_ids": [],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Pick which main question" not in resp.data
    from app.models import AssessmentItem
    assert AssessmentItem.query.count() == 1


# ------------------------------------------------------- faculty display

def test_faculty_display_falls_back_to_free_text_for_a_legacy_course(client):
    """A course from before this feature existed has coordinator_id=None
    and no Teacher assignments - faculty_display should fall back to the
    old free-text faculty_name field rather than showing nothing."""
    course_id = _new_course(client, faculty_name="Manual Faculty Name")
    course = db.session.get(Course, course_id)
    course.coordinator_id = None  # simulate a pre-existing course
    db.session.commit()
    assert course.faculty_display == "Manual Faculty Name"


def test_faculty_display_shows_coordinator_even_with_no_teachers_assigned(client):
    course_id = _new_course(client, faculty_name="ignored once a coordinator is set")
    course = db.session.get(Course, course_id)
    assert course.faculty_display == "Test Coordinator"


def test_faculty_display_lists_coordinator_and_assigned_teachers(client):
    course_id = _new_course(client, faculty_name="ignored once teachers exist")
    course = db.session.get(Course, course_id)
    assert course.coordinator_id == client.coordinator_id

    teacher = User(username="Priya Rao", display_name="Priya Rao", role=ROLE_TEACHER)
    teacher.set_password("")
    db.session.add(teacher)
    db.session.commit()
    db.session.add(CourseTeacher(course_id=course.id, teacher_id=teacher.id))
    db.session.commit()

    db.session.refresh(course)
    assert course.faculty_display == "Test Coordinator, Priya Rao"


# --------------------------------------------------- exit survey template

def test_exit_survey_template_download_matches_course_cos(client):
    course_id = _new_course(client)
    client.post(f"/courses/{course_id}/outcomes", data={"code": "CO1", "description": "Do the thing"}, follow_redirects=True)

    resp = client.get(f"/courses/{course_id}/structure/EXIT_SURVEY/template.xlsx")
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    wb = load_workbook(io.BytesIO(resp.data))
    assert "Instructions" in wb.sheetnames
    assert "Suggested Questions" in wb.sheetnames
    questions_ws = wb["Suggested Questions"]
    assert questions_ws.cell(row=2, column=1).value == "CO1"
    assert "CO1" in questions_ws.cell(row=2, column=2).value


def test_exit_survey_template_has_generic_questions_when_no_cos_defined(client):
    course_id = _new_course(client)
    resp = client.get(f"/courses/{course_id}/structure/EXIT_SURVEY/template.xlsx")
    wb = load_workbook(io.BytesIO(resp.data))
    questions_ws = wb["Suggested Questions"]
    assert questions_ws.cell(row=2, column=2).value  # some generic question text is present


def test_structure_page_shows_download_link_when_exit_survey_is_empty(client):
    course_id = _new_course(client)
    resp = client.get(f"/courses/{course_id}/structure/EXIT_SURVEY")
    assert resp.status_code == 200
    assert b"Download question template" in resp.data


# ------------------------------------------------------- multi-section

def test_sections_list_parses_comma_separated_section_field(client):
    course_id = _new_course(client, section="A, B, C")
    course = db.session.get(Course, course_id)
    assert course.sections_list == ["A", "B", "C"]


def test_sections_list_is_empty_for_a_single_or_blank_section(client):
    course_id = _new_course(client, section="")
    course = db.session.get(Course, course_id)
    assert course.sections_list == []


def test_bulk_roster_add_accepts_optional_section_field(client):
    course_id = _new_course(client, section="A, B")
    client.post(f"/courses/{course_id}/marks/roster", data={
        "action": "add_bulk", "bulk_text": "U1, Alice, A\nU2, Bob, B\nU3, Carol",
    }, follow_redirects=True)
    from app.models import Student
    by_usn = {s.usn: s for s in Student.query.filter_by(course_id=course_id).all()}
    assert by_usn["U1"].section == "A"
    assert by_usn["U2"].section == "B"
    assert by_usn["U3"].section == ""  # section is optional per-row
