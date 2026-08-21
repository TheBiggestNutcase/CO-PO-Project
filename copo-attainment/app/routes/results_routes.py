"""Results: computed CO attainment and PO/PSO attainment, driven by
app.engine.compute_course_attainment - the web equivalent of the
"CO attainment" and "Course PO attainment" sheets."""
from flask import Blueprint, current_app, render_template, request, abort
from flask_login import current_user

from app.extensions import db
from app.models import Course
from app.engine import compute_course_attainment

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


@results_bp.route("/")
def view_results(course_id):
    course = _get_course_or_404(course_id)
    result = compute_course_attainment(course)
    return render_template("results/view.html", course=course, result=result)
