"""
Assessment structure: for each component (IA1, IA2, IA3, ASQM, Exit
Survey), the faculty defines the question/item list - label, max marks
(not applicable for Exit Survey), and which CO(s) it's tagged to. This is
the web equivalent of the "Cijk" rows + max-marks row at the top of the
IA/ASQM/Exit-survey sheets.

EXTERNAL has no items - its component row still gets created (so the
course "has all 6 components set up"), but marks are entered directly as
totals in the Marks Entry section, matching the template's External sheet.
"""
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort, Response
from flask_login import current_user

from app.extensions import db
from app.models import Course, AssessmentComponent, AssessmentItem, ItemCOMapping, COMPONENT_TYPES
from app.exit_survey_template import build_template_bytes

structure_bp = Blueprint("structure", __name__, url_prefix="/courses/<int:course_id>/structure")


@structure_bp.before_request
def _require_coordinator():
    """Everything under /structure is coordinator-only - a Teacher never
    touches the assessment structure, only marks and results."""
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not current_user.is_coordinator:
        abort(403)

COMPONENT_LABELS = {
    "IA1": "Internal Assessment Test 1",
    "IA2": "Internal Assessment Test 2",
    "IA3": "Internal Assessment Test 3",
    "ASQM": "Assignment / Seminar / Quiz / Mini-project (self-study)",
    "EXTERNAL": "External (Semester End Exam)",
    "EXIT_SURVEY": "Course Exit Survey",
}
ITEM_BASED_TYPES = ("IA1", "IA2", "IA3", "ASQM", "EXIT_SURVEY")

# An IA paper is 4 main questions, each with sub-parts (labelled a, b, c...
# - this app just calls each sub-part an "item", same as ASQM/exit-survey
# items, but tags it with which main question (1-4) it belongs to. A main
# question's sub-parts can add up to at most this many marks - matching
# the real paper's "either/or" design (either Q1 or Q2, either Q3 or Q4,
# each worth 20, for 40 total) - see app/engine.py's
# ia_effective_marks_lookup for how the "answered extra" case is graded.
IA_TYPES = ("IA1", "IA2", "IA3")
MAIN_QUESTION_CAP = 20.0
MAIN_QUESTIONS = (1, 2, 3, 4)


def _get_course_or_404(course_id):
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    return course


def _get_or_create_component(course, comp_type):
    component = next((c for c in course.components if c.type == comp_type), None)
    if component is None:
        component = AssessmentComponent(course_id=course.id, type=comp_type, name=COMPONENT_LABELS[comp_type])
        db.session.add(component)
        db.session.commit()
    return component


@structure_bp.route("/")
def list_components(course_id):
    course = _get_course_or_404(course_id)
    existing_by_type = {c.type: c for c in course.components}
    rows = []
    for comp_type in COMPONENT_TYPES:
        component = existing_by_type.get(comp_type)
        item_count = len(component.items) if component and comp_type in ITEM_BASED_TYPES else None
        rows.append({
            "type": comp_type,
            "label": COMPONENT_LABELS[comp_type],
            "component": component,
            "item_count": item_count,
        })
    return render_template("structure/list.html", course=course, rows=rows)


@structure_bp.route("/<comp_type>", methods=["GET", "POST"])
def manage_component(course_id, comp_type):
    course = _get_course_or_404(course_id)
    if comp_type not in COMPONENT_TYPES:
        abort(404)

    component = _get_or_create_component(course, comp_type)

    if comp_type == "EXTERNAL":
        return render_template("structure/external.html", course=course, component=component)

    is_ia = comp_type in IA_TYPES

    if request.method == "POST":
        label = request.form.get("label", "").strip()
        max_marks_raw = request.form.get("max_marks", "").strip()
        co_ids = request.form.getlist("co_ids")
        main_question_raw = request.form.get("main_question", "").strip()

        main_question = None
        error = None
        if not label:
            error = "Item needs a label (e.g. '1a' or 'Q1')."
        elif comp_type != "EXIT_SURVEY" and not max_marks_raw:
            error = "Item needs max marks."
        elif is_ia:
            try:
                main_question = int(main_question_raw)
            except ValueError:
                main_question = None
            if main_question not in MAIN_QUESTIONS:
                error = "Pick which main question (1, 2, 3, or 4) this sub-part belongs to."
            else:
                max_marks_val = float(max_marks_raw)
                existing_total = sum(
                    (i.max_marks or 0.0) for i in component.items if i.main_question == main_question
                )
                if existing_total + max_marks_val > MAIN_QUESTION_CAP:
                    error = (
                        f"Main question {main_question} already has {existing_total:g} marks allotted - "
                        f"adding {max_marks_val:g} more would total {existing_total + max_marks_val:g}, "
                        f"over the {MAIN_QUESTION_CAP:g}-mark cap per main question."
                    )

        if error:
            flash(error, "error")
        else:
            next_seq = (max((i.seq for i in component.items), default=0)) + 1
            item = AssessmentItem(
                component_id=component.id,
                seq=next_seq,
                label=label,
                max_marks=float(max_marks_raw) if max_marks_raw else None,
                main_question=main_question,
            )
            db.session.add(item)
            db.session.flush()
            for co_id in co_ids:
                db.session.add(ItemCOMapping(item_id=item.id, co_id=int(co_id)))
            db.session.commit()
            flash(f"Added item {label}.", "success")
        return redirect(url_for("structure.manage_component", course_id=course.id, comp_type=comp_type))

    groups = None
    if is_ia:
        groups = {n: [i for i in component.items if i.main_question == n] for n in MAIN_QUESTIONS}
        groups["ungrouped"] = [i for i in component.items if i.main_question is None]

    return render_template(
        "structure/items.html",
        course=course, component=component, comp_type=comp_type,
        label=COMPONENT_LABELS[comp_type], is_exit_survey=(comp_type == "EXIT_SURVEY"),
        is_ia=is_ia, groups=groups, main_question_cap=MAIN_QUESTION_CAP, main_questions=MAIN_QUESTIONS,
    )


@structure_bp.route("/<comp_type>/items/<int:item_id>/delete", methods=["POST"])
def delete_item(course_id, comp_type, item_id):
    item = db.session.get(AssessmentItem, item_id)
    if item and item.component.course_id == course_id:
        label = item.label
        db.session.delete(item)
        db.session.commit()
        flash(f"Removed item {label}.", "success")
    return redirect(url_for("structure.manage_component", course_id=course_id, comp_type=comp_type))


@structure_bp.route("/EXIT_SURVEY/template.xlsx")
def exit_survey_template(course_id):
    """A downloadable list of suggested exit-survey questions (matched to
    this course's COs, if any) plus Google Form setup instructions - for
    a coordinator who hasn't set up a survey form yet. See
    app/exit_survey_template.py."""
    course = _get_course_or_404(course_id)
    data = build_template_bytes(course)
    filename = f"{course.subject_code or 'course'}_exit_survey_template.xlsx"
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
