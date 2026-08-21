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
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.extensions import db
from app.models import Course, AssessmentComponent, AssessmentItem, ItemCOMapping, COMPONENT_TYPES

structure_bp = Blueprint("structure", __name__, url_prefix="/courses/<int:course_id>/structure")

COMPONENT_LABELS = {
    "IA1": "Internal Assessment Test 1",
    "IA2": "Internal Assessment Test 2",
    "IA3": "Internal Assessment Test 3",
    "ASQM": "Assignment / Seminar / Quiz / Mini-project (self-study)",
    "EXTERNAL": "External (Semester End Exam)",
    "EXIT_SURVEY": "Course Exit Survey",
}
ITEM_BASED_TYPES = ("IA1", "IA2", "IA3", "ASQM", "EXIT_SURVEY")


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

    if request.method == "POST":
        label = request.form.get("label", "").strip()
        max_marks_raw = request.form.get("max_marks", "").strip()
        co_ids = request.form.getlist("co_ids")
        if not label:
            flash("Item needs a label (e.g. '1A' or 'Q1').", "error")
        elif comp_type != "EXIT_SURVEY" and not max_marks_raw:
            flash("Item needs max marks.", "error")
        else:
            next_seq = (max((i.seq for i in component.items), default=0)) + 1
            item = AssessmentItem(
                component_id=component.id,
                seq=next_seq,
                label=label,
                max_marks=float(max_marks_raw) if max_marks_raw else None,
            )
            db.session.add(item)
            db.session.flush()
            for co_id in co_ids:
                db.session.add(ItemCOMapping(item_id=item.id, co_id=int(co_id)))
            db.session.commit()
            flash(f"Added item {label}.", "success")
        return redirect(url_for("structure.manage_component", course_id=course.id, comp_type=comp_type))

    return render_template(
        "structure/items.html",
        course=course, component=component, comp_type=comp_type,
        label=COMPONENT_LABELS[comp_type], is_exit_survey=(comp_type == "EXIT_SURVEY"),
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
