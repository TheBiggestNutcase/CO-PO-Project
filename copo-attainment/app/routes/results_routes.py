"""Results: computed CO attainment and PO/PSO attainment, driven by
app.engine.compute_course_attainment - the web equivalent of the
"CO attainment" and "Course PO attainment" sheets."""
import io
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, abort, send_file, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Course
from app.engine import compute_course_attainment
from app.export_xlsx import build_workbook
from app.export_pdf import build_pdf_bytes

results_bp = Blueprint("results", __name__, url_prefix="/courses/<int:course_id>/results")


@results_bp.before_request
def _require_course_access():
    """Same rule as marks_bp: coordinator (any course) or a teacher
    assigned to *this* course_id - teachers are allowed to view results,
    just not set anything up."""
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    course_id = request.view_args.get("course_id") if request.view_args else None
    if course_id is None or not current_user.can_access_course(course_id):
        abort(403)


def _get_course_or_404(course_id):
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    return course


def _safe_filename_stem(course):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", course.subject_code or "course")


@results_bp.route("/")
def view_results(course_id):
    course = _get_course_or_404(course_id)
    result = compute_course_attainment(course)
    return render_template("results/view.html", course=course, result=result)


@results_bp.route("/export.xlsx")
def export_xlsx(course_id):
    """Download the full attainment workbook - same access rule as the
    Results page itself (coordinator, or a teacher assigned to this
    course), since it's exporting exactly what that page already shows.

    Built fresh on every request (no caching) - it's fast enough (a
    handful of COs/POs/students) that staleness isn't worth the
    complexity of invalidating a cached copy whenever marks change.
    """
    course = _get_course_or_404(course_id)
    wb = build_workbook(course)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"{_safe_filename_stem(course)}_attainment.xlsx"

    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@results_bp.route("/export.pdf")
def export_pdf(course_id):
    """A standalone attainment report rendered straight to PDF with
    reportlab (pure Python, no LibreOffice/system dependency) - see
    app/export_pdf.py. It's built from the same computed data as the
    Results page and the Excel export, but laid out on its own for print
    rather than mirroring the workbook's sheets. Any unexpected rendering
    error is caught so a rare bug here can't 500 the page - the Excel
    export still works as a fallback."""
    course = _get_course_or_404(course_id)

    try:
        pdf_bytes = build_pdf_bytes(course)
    except Exception:
        current_app.logger.exception("PDF export failed for course %s", course.id)
        flash("Couldn't generate the PDF report. Please try the Excel export instead.", "error")
        return redirect(url_for("results.view_results", course_id=course.id))

    filename = f"{_safe_filename_stem(course)}_attainment.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
