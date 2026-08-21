"""
Import a syllabus PDF to pre-fill a new course's subject details and
Course Outcomes, instead of typing them in from scratch.

Flow: upload -> parse (best-effort, see app/syllabus_import.py) -> a
review screen with every field editable -> confirm actually creates the
Course + CourseOutcome rows. Nothing touches the database until the
faculty member confirms the review screen, since the parsing is a
heuristic and syllabus PDFs vary in layout.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.extensions import db
from app.models import Course, CourseOutcome
from app.routes.setup_routes import _apply_course_form
from app.syllabus_import import parse_syllabus_pdf
from app.auth import coordinator_required

import_bp = Blueprint("import_syllabus", __name__, url_prefix="/courses/import-syllabus")


@import_bp.route("/", methods=["GET"])
@coordinator_required
def upload_form():
    return render_template("courses/import_syllabus.html")


@import_bp.route("/parse", methods=["POST"])
@coordinator_required
def parse():
    uploaded = request.files.get("syllabus")
    if not uploaded or uploaded.filename == "":
        flash("Choose a PDF file first.", "error")
        return redirect(url_for("import_syllabus.upload_form"))
    if not uploaded.filename.lower().endswith(".pdf"):
        flash("That doesn't look like a PDF. Please upload the syllabus as a .pdf file.", "error")
        return redirect(url_for("import_syllabus.upload_form"))

    try:
        parsed = parse_syllabus_pdf(uploaded.stream)
    except ImportError:
        flash(
            "The 'pdfplumber' library isn't installed. Run 'pip install -r requirements.txt' "
            "(with your virtual environment activated), restart the app, and try again.",
            "error",
        )
        return redirect(url_for("import_syllabus.upload_form"))
    except Exception:
        flash(
            "Couldn't read that PDF - it may be a scanned image rather than real text, or corrupted. "
            "You can still create the course by hand.",
            "error",
        )
        return redirect(url_for("setup.new_course"))

    # A few spare blank CO rows so anything the parser missed can be added
    # by hand on the review screen, without needing JavaScript.
    outcome_rows = list(parsed.outcomes) + [None] * 3

    return render_template("courses/import_review.html", parsed=parsed, outcome_rows=outcome_rows)


@import_bp.route("/confirm", methods=["POST"])
@coordinator_required
def confirm():
    course = Course()
    _apply_course_form(course, request.form)
    db.session.add(course)
    db.session.flush()

    seq = 1
    codes = request.form.getlist("co_code")
    descriptions = request.form.getlist("co_description")
    for code, description in zip(codes, descriptions):
        code = code.strip()
        description = description.strip()
        if not code and not description:
            continue  # a spare row left blank
        db.session.add(CourseOutcome(
            course_id=course.id, seq=seq,
            code=code or f"CO{seq}", description=description or None,
        ))
        seq += 1

    db.session.commit()
    flash(f"Created {course.subject_code} from the syllabus. Review the COs below, then add POs and mapping.", "success")
    return redirect(url_for("setup.manage_outcomes", course_id=course.id))
