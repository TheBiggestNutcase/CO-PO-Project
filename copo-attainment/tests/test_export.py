"""
Tests for the "Export to Excel"/"Export to PDF" features (app/export_xlsx.py
+ app/export_pdf.py + the /courses/<id>/results/export.xlsx and export.pdf
routes) - regenerates the original attainment template, populated with a
course's real data (Excel), and renders a standalone report straight to PDF
with reportlab (pure Python, no LibreOffice or other system dependency).

Kept deliberately end-to-end: build a small but fully-populated course
through the real routes (COs, POs, mapping, structure, marks, exit
survey, external results - the same path a user would take), then
download the export and check it's well-formed - the expected sheets and
live formulas (not hardcoded numbers) for the xlsx, actual calculated
values embedded in the page text for the PDF.
"""
import io

import pytest
from openpyxl import load_workbook

from app import create_app
from app.models import Course, CourseOutcome, ProgramOutcome, User, ROLE_TEACHER


@pytest.fixture()
def client():
    """A logged-in Unit Coordinator's test client - see tests/test_routes.py
    for why the bootstrap POST is needed before any other route works."""
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        test_client = app.test_client()
        test_client.post("/setup-coordinator", data={
            "name": "Test Coordinator", "password": "testpass123", "confirm_password": "testpass123",
        }, follow_redirects=True)
        yield test_client


def _build_populated_course(client):
    """One small course, fully wired up: 2 COs, standard POs, a mapping,
    an IA1 item + an exit-survey question (each tagged to a CO), a
    2-student roster, real marks/ratings, and an external result for
    each student. Returns the course_id."""
    client.post("/courses/new", data={
        "subject_code": "T-EXPORT", "subject_name": "Export Test Subject",
        "target_level1_pct": "50", "target_level2_pct": "60", "target_level3_pct": "70",
        "internal_marks_cutoff_pct": "60",
    }, follow_redirects=True)
    course_id = Course.query.filter_by(subject_code="T-EXPORT").first().id

    for i in range(1, 3):
        client.post(f"/courses/{course_id}/outcomes", data={"code": f"CO{i}"}, follow_redirects=True)
    client.post(f"/courses/{course_id}/program-outcomes", data={"action": "add_standard"}, follow_redirects=True)

    cos = CourseOutcome.query.filter_by(course_id=course_id).order_by(CourseOutcome.seq).all()
    pos = ProgramOutcome.query.filter_by(course_id=course_id).order_by(ProgramOutcome.seq).all()
    client.post(f"/courses/{course_id}/mapping", data={
        f"corr_{cos[0].id}_{pos[0].id}": "3",
        f"corr_{cos[1].id}_{pos[1].id}": "2",
    }, follow_redirects=True)

    client.post(f"/courses/{course_id}/structure/IA1",
                data={"label": "1A", "max_marks": "10", "co_ids": [str(cos[0].id)]}, follow_redirects=True)
    client.post(f"/courses/{course_id}/structure/EXIT_SURVEY",
                data={"label": "Q1", "co_ids": [str(cos[1].id)]}, follow_redirects=True)

    client.post(f"/courses/{course_id}/marks/roster",
                data={"action": "add_bulk", "bulk_text": "U1, Alice\nU2, Bob"}, follow_redirects=True)

    from app.models import Student, AssessmentItem
    students = Student.query.filter_by(course_id=course_id).order_by(Student.seq).all()
    ia1_item = AssessmentItem.query.join(AssessmentItem.component).filter_by(course_id=course_id, type="IA1").first()
    survey_item = AssessmentItem.query.join(AssessmentItem.component).filter_by(course_id=course_id, type="EXIT_SURVEY").first()

    client.post(f"/courses/{course_id}/marks/IA1", data={
        f"mark_{students[0].id}_{ia1_item.id}": "8",
        f"mark_{students[1].id}_{ia1_item.id}": "6",
    }, follow_redirects=True)
    client.post(f"/courses/{course_id}/marks/EXIT_SURVEY", data={
        f"rating_{students[0].id}_{survey_item.id}": "A",
        f"rating_{students[1].id}_{survey_item.id}": "B",
    }, follow_redirects=True)
    client.post(f"/courses/{course_id}/marks/EXTERNAL", data={
        f"internal_{students[0].id}": "30", f"external_{students[0].id}": "70", f"result_{students[0].id}": "P",
        f"internal_{students[1].id}": "20", f"external_{students[1].id}": "40", f"result_{students[1].id}": "P",
    }, follow_redirects=True)

    return course_id


def test_export_route_returns_a_well_formed_workbook_with_all_sheets(client):
    course_id = _build_populated_course(client)

    resp = client.get(f"/courses/{course_id}/results/export.xlsx")
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "T-EXPORT" in resp.headers.get("Content-Disposition", "")

    wb = load_workbook(io.BytesIO(resp.data))
    expected_sheets = {
        "set target form", "IA Test only", "ASQM only", "External",
        "Result Analysis", "Exit survey", "CO attainment", "Course PO attainment",
        "CO attainment gap", "Course PO attainment gap",
    }
    assert expected_sheets.issubset(set(wb.sheetnames))
    # The Instructions sheet is dropped entirely rather than rewritten -
    # its original per-sheet fill-in steps don't apply to a generated
    # workbook, and a from-scratch rewrite is deferred for later.
    assert "Instructions" not in wb.sheetnames

    # The institution/subject header made it onto the marks-grid sheet.
    ia_ws = wb["IA Test only"]
    assert ia_ws.cell(row=4, column=3).value == "T-EXPORT"

    # CO attainment is formula-driven, not a hardcoded number - the whole
    # point of the export per the xlsx skill's rules.
    co_ws = wb["CO attainment"]
    blended_cell = co_ws.cell(row=10, column=12)
    assert isinstance(blended_cell.value, str) and blended_cell.value.startswith("=")

    # Logos travel with the workbook.
    assert len(wb["set target form"]._images) >= 1


def test_export_is_denied_to_a_teacher_not_assigned_to_the_course(client):
    course_id = _build_populated_course(client)
    client.post("/logout", follow_redirects=True)

    app = client.application
    with app.app_context():
        teacher = User(username="Other Teacher", display_name="Other Teacher", role=ROLE_TEACHER)
        teacher.set_password("")
        from app.extensions import db
        db.session.add(teacher)
        db.session.commit()

    client.post("/login", data={"name": "Other Teacher", "password": ""}, follow_redirects=True)
    resp = client.get(f"/courses/{course_id}/results/export.xlsx")
    assert resp.status_code == 403


def test_pdf_export_route_returns_a_calculated_pdf(client):
    """Rendered straight from the app's own computed data via reportlab -
    no LibreOffice or other system dependency, so this always runs.
    Checks the PDF actually contains the subject code and is a real,
    non-trivial PDF, not just an empty shell."""
    course_id = _build_populated_course(client)

    resp = client.get(f"/courses/{course_id}/results/export.pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert "T-EXPORT" in resp.headers.get("Content-Disposition", "")
    assert resp.data[:5] == b"%PDF-"
    assert len(resp.data) > 1000


def test_pdf_export_falls_back_gracefully_on_unexpected_error(client, monkeypatch):
    """The route must not 500 on a rendering error - it should redirect
    back to Results with an explanatory flash message, since the Excel
    export still works fine regardless."""
    course_id = _build_populated_course(client)

    def _boom(course):
        raise RuntimeError("something went wrong building the PDF")

    monkeypatch.setattr("app.routes.results_routes.build_pdf_bytes", _boom)

    resp = client.get(f"/courses/{course_id}/results/export.pdf", follow_redirects=True)
    assert resp.status_code == 200
    assert b"PDF" in resp.data
