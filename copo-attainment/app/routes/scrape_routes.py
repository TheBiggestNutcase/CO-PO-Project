"""
VTU results scraper: an alternative to typing the External (SEE) marks in
by hand on the /marks/EXTERNAL page - select which roster USNs to look
up, VTU's public results portal is scraped for each (see app/vtu_scraper
and app/scrape_jobs for how), and the result lands on a review screen you
approve before anything is written to ExternalResult.

VTU only publishes Internal/External *totals* per subject, not
question-wise or CO-wise marks - so this can only ever populate
ExternalResult, never the per-question Mark rows IA1-3/ASQM already use.

Same access rule as the rest of marks entry: coordinator (any course), or
a teacher assigned to this course - entering scraped results is no
different from entering them by hand, so it isn't locked down any
further.
"""
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, abort, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Course, ExternalResult
from app.scrape_jobs import STATUS_RUNNING, get_job, start_job
from app.vtu_scraper.parser import SubjectNotFound, get_subject_row

scrape_bp = Blueprint("scrape", __name__, url_prefix="/courses/<int:course_id>/scrape")

# Same shape VTU itself expects: https://results.vtu.ac.in/<cycle-slug>/index.php
_RESULTS_URL_RE = re.compile(r"^https://results\.vtu\.ac\.in/[a-zA-Z0-9]+/index\.php$")


@scrape_bp.before_request
def _require_course_access():
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


def _get_job_for_course_or_404(job_id, course_id):
    """Job lookups are scoped to the course_id in the URL - job_ids are
    random enough that guessing one is impractical, but there's no reason
    to rely on that alone."""
    job = get_job(job_id)
    if job is None or job["course_id"] != course_id:
        abort(404)
    return job


@scrape_bp.route("/")
def setup(course_id):
    course = _get_course_or_404(course_id)
    return render_template("scrape/setup.html", course=course)


@scrape_bp.route("/start", methods=["POST"])
def start(course_id):
    course = _get_course_or_404(course_id)

    student_ids = {int(v) for v in request.form.getlist("student_id")}
    students = [s for s in course.students if s.id in student_ids]

    results_url = request.form.get("results_url", "").strip()
    headless = request.form.get("headless") == "on"

    errors = []
    if not students:
        errors.append("Select at least one student to scrape.")
    if not _RESULTS_URL_RE.match(results_url):
        errors.append(
            "Enter a valid VTU results URL - it should look like "
            "https://results.vtu.ac.in/<cycle>/index.php (find the current one on "
            "the VTU results page for your exam cycle)."
        )
    if not (course.subject_code or "").strip():
        errors.append("This course has no subject code set - set one on the course overview first.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("scrape.setup", course_id=course.id))

    usns = [s.usn.strip().upper() for s in students]
    app_obj = current_app._get_current_object()
    job_id = start_job(app_obj, course.id, usns, results_url, headless=headless)

    return redirect(url_for("scrape.progress", course_id=course.id, job_id=job_id))


@scrape_bp.route("/progress/<job_id>")
def progress(course_id, job_id):
    course = _get_course_or_404(course_id)
    job = _get_job_for_course_or_404(job_id, course.id)
    return render_template("scrape/progress.html", course=course, job=job)


@scrape_bp.route("/progress/<job_id>/status.json")
def progress_json(course_id, job_id):
    job = _get_job_for_course_or_404(job_id, course_id)
    return {
        "status": job["status"],
        "processed": job["processed"],
        "total": job["total"],
        "log": job["log"][-50:],
        "error": job["error"],
    }


@scrape_bp.route("/review/<job_id>")
def review(course_id, job_id):
    course = _get_course_or_404(course_id)
    job = _get_job_for_course_or_404(job_id, course.id)

    if job["status"] == STATUS_RUNNING:
        return redirect(url_for("scrape.progress", course_id=course.id, job_id=job_id))

    students_by_usn = {s.usn.strip().upper(): s for s in course.students}
    existing_by_student_id = {r.student_id: r for r in ExternalResult.query.filter(
        ExternalResult.student_id.in_([s.id for s in course.students])
    ).all()} if course.students else {}

    rows = []
    if job["result"] is not None:
        for outcome in job["result"].outcomes:
            student = students_by_usn.get(outcome.usn.strip().upper())
            row = {
                "usn": outcome.usn,
                "student": student,
                "status": outcome.status,
                "confirmed_name": outcome.confirmed_name,
                "error": outcome.error,
                "subject": None,
                "existing": existing_by_student_id.get(student.id) if student else None,
            }
            if outcome.status == "ok":
                try:
                    row["subject"] = get_subject_row(outcome.subjects, course.subject_code)
                except SubjectNotFound as e:
                    row["error"] = str(e)
                    row["seen_codes"] = e.seen_codes
            rows.append(row)

    return render_template("scrape/review.html", course=course, job=job, job_id=job_id, rows=rows)


@scrape_bp.route("/review/<job_id>/save", methods=["POST"])
def save(course_id, job_id):
    course = _get_course_or_404(course_id)
    _get_job_for_course_or_404(job_id, course.id)  # 404s a stale/foreign job_id before we touch the DB

    saved = 0
    for key in request.form:
        if not key.startswith("include_"):
            continue
        try:
            student_id = int(key[len("include_"):])
        except ValueError:
            continue
        student = next((s for s in course.students if s.id == student_id), None)
        if student is None:
            continue

        internal_raw = request.form.get(f"internal_{student_id}", "").strip()
        external_raw = request.form.get(f"external_{student_id}", "").strip()
        result_raw = request.form.get(f"result_{student_id}", "").strip().upper()

        existing = ExternalResult.query.filter_by(student_id=student_id).first()
        if existing is None:
            existing = ExternalResult(student_id=student_id)
            db.session.add(existing)

        existing.internal_total = float(internal_raw) if internal_raw else None
        existing.external_marks = float(external_raw) if external_raw else None
        existing.result = result_raw or None
        saved += 1

    db.session.commit()
    flash(f"Saved scraped results for {saved} student(s).", "success")
    return redirect(url_for("marks.enter_component_marks", course_id=course.id, comp_type="EXTERNAL"))
