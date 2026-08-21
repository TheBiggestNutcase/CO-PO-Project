"""Results: computed CO attainment and PO/PSO attainment, driven by
app.engine.compute_course_attainment - the web equivalent of the
"CO attainment" and "Course PO attainment" sheets."""
from flask import Blueprint, render_template, abort

from app.extensions import db
from app.models import Course
from app.engine import compute_course_attainment

results_bp = Blueprint("results", __name__, url_prefix="/courses/<int:course_id>/results")


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
