"""
Tests for the Google Sheets exit-survey import: app/exit_survey_import.py's
parser directly (unit-level, hand-built CSV/XLSX-shaped input - no real
Google Sheet involved) and the /courses/<id>/marks/EXIT_SURVEY/import*
routes end-to-end (upload -> review -> confirm), mirroring the roster/
syllabus import tests' style.
"""
import io
from dataclasses import dataclass

import pytest

from app import create_app
from app.exit_survey_import import parse_exit_survey_file
from app.extensions import db
from app.models import AssessmentItem, Course, CourseOutcome, ExitSurveyResponse, Student, User, ROLE_COORDINATOR


# --------------------------------------------------------------- parser


@dataclass
class _FakeStudent:
    id: int
    usn: str


@dataclass
class _FakeItem:
    id: int
    label: str
    co_links: list = None

    def __post_init__(self):
        if self.co_links is None:
            self.co_links = []


def _csv_bytes(text):
    return io.BytesIO(text.encode("utf-8"))


def test_parses_a_typical_google_forms_csv_export():
    students = [_FakeStudent(1, "1AA20CS001"), _FakeStudent(2, "1AA20CS002")]
    items = [_FakeItem(10, "Q1"), _FakeItem(11, "Q2")]
    csv_text = (
        "Timestamp,USN,How was the pace?,How was the faculty?\n"
        "2024-01-01,1AA20CS001,Excellent,Good\n"
        "2024-01-02,1AA20CS002,Very Poor,Average\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "responses.csv", students, items)

    assert parsed.usn_header == "USN"
    assert [c.header for c in parsed.columns] == ["How was the pace?", "How was the faculty?"]
    assert [c.suggested_item_id for c in parsed.columns] == [10, 11]
    assert len(parsed.rows) == 2
    row1 = parsed.rows[0]
    assert row1.matched_student_id == 1
    assert row1.cells[2].letter == "A"  # Excellent
    assert row1.cells[3].letter == "B"  # Good
    assert not parsed.warnings


def test_dash_prefixed_and_bare_letter_ratings_are_recognized():
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1"), _FakeItem(11, "Q2")]
    csv_text = (
        "USN,Q1,Q2\n"
        "1AA20CS001,A - Excellent,D\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)
    row = parsed.rows[0]
    assert row.cells[1].letter == "A"
    assert row.cells[2].letter == "D"


def test_expanded_five_point_scale_resolves_good_to_c_not_b():
    """A column using Excellent/Very Good/Good/Satisfactory/Not
    Satisfactory (a real exit-survey form's actual wording) must map
    bare "Good" to C, not B - since "Very Good" already occupies B in
    that scale. Detected per-column from the presence of "Very Good" or
    "Satisfactory" among that column's own other answers."""
    students = [_FakeStudent(1, "1AA20CS001"), _FakeStudent(2, "1AA20CS002"), _FakeStudent(3, "1AA20CS003")]
    items = [_FakeItem(10, "Q1")]
    csv_text = (
        "USN,Q1\n"
        "1AA20CS001,Excellent\n"
        "1AA20CS002,Very Good\n"
        "1AA20CS003,Good\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)

    letters = {r.usn_raw: r.cells[1].letter for r in parsed.rows}
    assert letters["1AA20CS001"] == "A"
    assert letters["1AA20CS002"] == "B"
    assert letters["1AA20CS003"] == "C"


def test_simple_five_point_scale_keeps_good_as_b():
    """The older/simpler Excellent/Good/Average/Poor/Very Poor wording,
    with no "Very Good"/"Satisfactory" marker anywhere in the column,
    should keep bare "Good" as B - the default."""
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]
    csv_text = "USN,Q1\n1AA20CS001,Good\n"

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)
    assert parsed.rows[0].cells[1].letter == "B"


def test_not_satisfactory_and_satisfactory_are_recognized():
    students = [_FakeStudent(1, "1AA20CS001"), _FakeStudent(2, "1AA20CS002")]
    items = [_FakeItem(10, "Q1")]
    csv_text = (
        "USN,Q1\n"
        "1AA20CS001,Satisfactory\n"
        "1AA20CS002,Not Satisfactory\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)
    assert parsed.rows[0].cells[1].letter == "D"
    assert parsed.rows[1].cells[1].letter == "E"


def test_email_name_and_section_columns_are_excluded_from_questions():
    """Google auto-adds "Email Address" when email collection is on, and
    institutional forms commonly add Name/Section columns too - none of
    those are rating questions and including them would throw off the
    positional question-matching for every real question after them."""
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]
    csv_text = (
        "Timestamp,Email Address,USN,Name,Section,How was the pace?\n"
        "2024-01-01,a@b.com,1AA20CS001,Alice,A,Excellent\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)

    assert [c.header for c in parsed.columns] == ["How was the pace?"]
    assert parsed.columns[0].suggested_item_id == 10
    assert any("Email Address" in w and "Name" in w and "Section" in w for w in parsed.warnings)


def test_value_counts_aggregate_per_column_for_the_bulk_fix_control():
    students = [_FakeStudent(1, "1AA20CS001"), _FakeStudent(2, "1AA20CS002"), _FakeStudent(3, "1AA20CS003")]
    items = [_FakeItem(10, "Q1")]
    csv_text = (
        "USN,Q1\n"
        "1AA20CS001,Excellent\n"
        "1AA20CS002,Excellent\n"
        "1AA20CS003,Superb\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)
    counts = {vc.raw: (vc.letter, vc.count) for vc in parsed.columns[0].value_counts}
    assert counts["Excellent"] == ("A", 2)
    assert counts["Superb"] == (None, 1)


def test_unrecognized_rating_text_is_flagged_not_guessed():
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]
    csv_text = "USN,Q1\n1AA20CS001,Superb\n"

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)
    row = parsed.rows[0]
    assert row.cells[1].letter is None
    assert row.cells[1].raw == "Superb"
    assert any("didn't match a recognized rating" in w for w in parsed.warnings)


def test_duplicate_usn_keeps_only_the_latest_submission():
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]
    csv_text = (
        "USN,Q1\n"
        "1AA20CS001,Poor\n"
        "1AA20CS001,Excellent\n"
    )

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)

    assert len(parsed.rows) == 1
    assert parsed.rows[0].cells[1].letter == "A"  # the later "Excellent", not the earlier "Poor"
    assert any("appear more than once" in w for w in parsed.warnings)


def test_missing_usn_column_falls_back_to_manual_matching_with_a_warning():
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]
    csv_text = "Name,Q1\nAlice,Good\n"

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)

    assert parsed.usn_header is None
    assert parsed.rows[0].matched_student_id is None
    assert any("Couldn't find a column" in w for w in parsed.warnings)


def test_more_sheet_columns_than_existing_items_leaves_extras_unmatched():
    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]  # only one item exists
    csv_text = "USN,Q1,Q2\n1AA20CS001,Good,Average\n"

    parsed = parse_exit_survey_file(_csv_bytes(csv_text), "r.csv", students, items)

    assert parsed.columns[0].suggested_item_id == 10
    assert parsed.columns[1].suggested_item_id is None


def test_unsupported_file_extension_raises_value_error():
    with pytest.raises(ValueError):
        parse_exit_survey_file(_csv_bytes("a,b\n1,2\n"), "responses.pdf", [], [])


def test_xlsx_export_parses_the_same_way_as_csv():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Timestamp", "USN", "Q1"])
    ws.append(["2024-01-01", "1AA20CS001", "Excellent"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    students = [_FakeStudent(1, "1AA20CS001")]
    items = [_FakeItem(10, "Q1")]
    parsed = parse_exit_survey_file(buf, "Form Responses.xlsx", students, items)

    assert parsed.rows[0].matched_student_id == 1
    assert parsed.rows[0].cells[2].letter == "A"


# --------------------------------------------------------------- routes


@pytest.fixture()
def client():
    """A logged-in Unit Coordinator's test client. There's no bootstrap
    route anymore (the one Admin account is created from env vars, not a
    web form - see ensure_admin_account in app/migrations.py), so this
    creates the Coordinator directly and logs in for real through /login."""
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        coordinator = User(username="Test Coordinator", display_name="Test Coordinator", role=ROLE_COORDINATOR)
        coordinator.set_password("testpass123")
        db.session.add(coordinator)
        db.session.commit()

        test_client = app.test_client()
        test_client.post("/login", data={
            "name": "Test Coordinator", "password": "testpass123",
        }, follow_redirects=True)
        yield test_client


def _build_course_with_roster_and_survey_items(client):
    client.post("/courses/new", data={
        "subject_code": "T-SURVEY", "subject_name": "Survey Test Subject",
        "target_level1_pct": "50", "target_level2_pct": "60", "target_level3_pct": "70",
        "internal_marks_cutoff_pct": "60",
    }, follow_redirects=True)
    course_id = Course.query.filter_by(subject_code="T-SURVEY").first().id

    for i in range(1, 3):
        client.post(f"/courses/{course_id}/outcomes", data={"code": f"CO{i}"}, follow_redirects=True)
    cos = CourseOutcome.query.filter_by(course_id=course_id).order_by(CourseOutcome.seq).all()

    client.post(f"/courses/{course_id}/marks/roster", data={
        "action": "add_bulk", "bulk_text": "1AA20CS001, Alice\n1AA20CS002, Bob",
    }, follow_redirects=True)

    client.post(f"/courses/{course_id}/structure/EXIT_SURVEY",
                data={"label": "Q1", "co_ids": [str(cos[0].id)]}, follow_redirects=True)
    client.post(f"/courses/{course_id}/structure/EXIT_SURVEY",
                data={"label": "Q2", "co_ids": [str(cos[1].id)]}, follow_redirects=True)

    return course_id


def test_import_form_redirects_when_no_survey_questions_are_set_up(client):
    client.post("/courses/new", data={
        "subject_code": "T-EMPTY", "subject_name": "Empty",
        "target_level1_pct": "50", "target_level2_pct": "60", "target_level3_pct": "70",
        "internal_marks_cutoff_pct": "60",
    }, follow_redirects=True)
    course_id = Course.query.filter_by(subject_code="T-EMPTY").first().id

    resp = client.get(f"/courses/{course_id}/marks/EXIT_SURVEY/import", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Set up the exit-survey question list" in resp.data


def test_full_flow_import_review_and_confirm(client):
    course_id = _build_course_with_roster_and_survey_items(client)

    csv_text = (
        "Timestamp,USN,How was the pace?,How was the faculty?\n"
        "2024-01-01,1AA20CS001,Excellent,Good\n"
        "2024-01-02,1AA20CS002,Very Poor,Average\n"
    )
    parse_resp = client.post(
        f"/courses/{course_id}/marks/EXIT_SURVEY/import/parse",
        data={"sheet": (io.BytesIO(csv_text.encode()), "responses.csv")},
        content_type="multipart/form-data",
    )
    assert parse_resp.status_code == 200
    assert b"How was the pace?" in parse_resp.data
    assert b"1AA20CS001" in parse_resp.data

    students = Student.query.filter_by(course_id=course_id).order_by(Student.seq).all()
    items = AssessmentItem.query.join(AssessmentItem.component).filter_by(
        course_id=course_id, type="EXIT_SURVEY"
    ).order_by(AssessmentItem.seq).all()

    confirm_data = {
        "item_for_col_2": str(items[0].id),
        "item_for_col_3": str(items[1].id),
        "student_for_row_1": str(students[0].id),
        "student_for_row_2": str(students[1].id),
        "rating_1_2": "A",
        "rating_1_3": "B",
        "rating_2_2": "E",
        "rating_2_3": "C",
    }
    confirm_resp = client.post(
        f"/courses/{course_id}/marks/EXIT_SURVEY/import/confirm", data=confirm_data, follow_redirects=True,
    )
    assert confirm_resp.status_code == 200
    assert b"Saved 4 exit survey response" in confirm_resp.data

    responses = {(r.student_id, r.item_id): r.rating for r in ExitSurveyResponse.query.all()}
    assert responses[(students[0].id, items[0].id)] == "A"
    assert responses[(students[0].id, items[1].id)] == "B"
    assert responses[(students[1].id, items[0].id)] == "E"
    assert responses[(students[1].id, items[1].id)] == "C"


def test_confirm_skips_rows_or_columns_left_unmatched(client):
    course_id = _build_course_with_roster_and_survey_items(client)
    items = AssessmentItem.query.join(AssessmentItem.component).filter_by(
        course_id=course_id, type="EXIT_SURVEY"
    ).order_by(AssessmentItem.seq).all()
    students = Student.query.filter_by(course_id=course_id).order_by(Student.seq).all()

    # Only map column 2 to a question, and only match row 1 to a student -
    # row 2 and column 3 are left as "skip".
    confirm_data = {
        "item_for_col_2": str(items[0].id),
        "student_for_row_1": str(students[0].id),
        "rating_1_2": "A",
        "rating_1_3": "B",  # column 3 has no item mapping - must be skipped
        "rating_2_2": "C",  # row 2 has no student mapping - must be skipped
    }
    resp = client.post(
        f"/courses/{course_id}/marks/EXIT_SURVEY/import/confirm", data=confirm_data, follow_redirects=True,
    )
    assert b"Saved 1 exit survey response" in resp.data
    assert ExitSurveyResponse.query.count() == 1


def test_review_page_renders_a_quick_fix_card_for_unmapped_values(client):
    course_id = _build_course_with_roster_and_survey_items(client)

    csv_text = "USN,How was the pace?\n1AA20CS001,Superb\n"
    resp = client.post(
        f"/courses/{course_id}/marks/EXIT_SURVEY/import/parse",
        data={"sheet": (io.BytesIO(csv_text.encode()), "responses.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert b"Quick-fix" in resp.data
    assert b"Superb" in resp.data


def test_rejects_a_non_spreadsheet_file(client):
    course_id = _build_course_with_roster_and_survey_items(client)

    resp = client.post(
        f"/courses/{course_id}/marks/EXIT_SURVEY/import/parse",
        data={"sheet": (io.BytesIO(b"not a spreadsheet"), "notes.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"doesn&#39;t look like a spreadsheet" in resp.data or b"spreadsheet export" in resp.data
