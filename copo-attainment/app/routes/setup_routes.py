"""
Setup: courses, course outcomes (COs), program outcomes (POs/PSOs), and
the CO-PO/PSO correlation matrix. This is the web equivalent of the
template's "set target form" sheet plus the mapping table at the top of
"Course PO attainment".
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user

from app.extensions import db
from app.models import Course, CourseOutcome, ProgramOutcome, COPOMapping
from app.constants import STANDARD_PO_DESCRIPTIONS, STANDARD_POS
from app.auth import coordinator_required

setup_bp = Blueprint("setup", __name__, url_prefix="/courses")


def _get_course_or_404(course_id):
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    return course


# ---------------------------------------------------------------- courses

@setup_bp.route("/")
def list_courses():
    if current_user.is_admin:
        # Admin doesn't manage course data at all - its home is the user
        # management page, not an always-empty course list.
        return redirect(url_for("auth.manage_users"))
    query = Course.query.order_by(Course.academic_year.desc(), Course.subject_code)
    if current_user.is_coordinator:
        courses = query.all()
    else:
        # Teachers only ever see the course(s) they've been assigned to.
        assigned_ids = {a.course_id for a in current_user.course_assignments}
        courses = [c for c in query.all() if c.id in assigned_ids]
    return render_template("courses/list.html", courses=courses)


@setup_bp.route("/new", methods=["GET", "POST"])
@coordinator_required
def new_course():
    if request.method == "POST":
        course = Course()
        course.coordinator_id = current_user.id
        _apply_course_form(course, request.form)
        db.session.add(course)
        db.session.commit()
        flash(f"Created {course.subject_code}. Next, add its Course Outcomes.", "success")
        return redirect(url_for("setup.course_detail", course_id=course.id))
    return render_template("courses/form.html", course=None)


@setup_bp.route("/<int:course_id>")
@coordinator_required
def course_detail(course_id):
    course = _get_course_or_404(course_id)
    return render_template("courses/detail.html", course=course)


@setup_bp.route("/<int:course_id>/edit", methods=["GET", "POST"])
@coordinator_required
def edit_course(course_id):
    course = _get_course_or_404(course_id)
    if request.method == "POST":
        _apply_course_form(course, request.form)
        db.session.commit()
        flash("Course details saved.", "success")
        return redirect(url_for("setup.course_detail", course_id=course.id))
    return render_template("courses/form.html", course=course)


def _apply_course_form(course, form):
    course.institution_name = form.get("institution_name", "").strip()
    course.department = form.get("department", "").strip()
    course.subject_code = form.get("subject_code", "").strip()
    course.subject_name = form.get("subject_name", "").strip()
    course.faculty_name = form.get("faculty_name", "").strip()
    course.academic_year = form.get("academic_year", "").strip()
    course.semester = form.get("semester", "").strip()
    course.section = form.get("section", "").strip()
    course.target_level1_pct = float(form.get("target_level1_pct") or 50)
    course.target_level2_pct = float(form.get("target_level2_pct") or 60)
    course.target_level3_pct = float(form.get("target_level3_pct") or 70)
    course.internal_marks_cutoff_pct = float(form.get("internal_marks_cutoff_pct") or 60)


# ------------------------------------------------------------- outcomes

@setup_bp.route("/<int:course_id>/outcomes", methods=["GET", "POST"])
@coordinator_required
def manage_outcomes(course_id):
    course = _get_course_or_404(course_id)
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        description = request.form.get("description", "").strip()
        if code:
            next_seq = (max((co.seq for co in course.outcomes), default=0)) + 1
            db.session.add(CourseOutcome(course_id=course.id, seq=next_seq, code=code, description=description or None))
            db.session.commit()
            flash(f"Added {code}.", "success")
        return redirect(url_for("setup.manage_outcomes", course_id=course.id))
    return render_template("courses/outcomes.html", course=course)


@setup_bp.route("/<int:course_id>/outcomes/<int:co_id>/delete", methods=["POST"])
@coordinator_required
def delete_outcome(course_id, co_id):
    co = db.session.get(CourseOutcome, co_id)
    if co and co.course_id == course_id:
        db.session.delete(co)
        db.session.commit()
        flash(f"Removed {co.code}.", "success")
    return redirect(url_for("setup.manage_outcomes", course_id=course_id))


# ------------------------------------------------------- program outcomes

@setup_bp.route("/<int:course_id>/program-outcomes", methods=["GET", "POST"])
@coordinator_required
def manage_program_outcomes(course_id):
    course = _get_course_or_404(course_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_standard":
            existing_codes = {po.code for po in course.program_outcomes}
            next_seq = (max((po.seq for po in course.program_outcomes), default=0))
            added = 0
            for code in STANDARD_POS:
                if code not in existing_codes:
                    next_seq += 1
                    db.session.add(ProgramOutcome(
                        course_id=course.id, seq=next_seq, code=code, is_pso=False,
                        description=STANDARD_PO_DESCRIPTIONS.get(code),
                    ))
                    added += 1
            db.session.commit()
            flash(f"Added {added} standard POs (PO1-PO12), with their standard descriptions." if added else "PO1-PO12 already present.", "success")
        else:
            code = request.form.get("code", "").strip()
            description = request.form.get("description", "").strip()
            is_pso = request.form.get("is_pso") == "on"
            if code:
                next_seq = (max((po.seq for po in course.program_outcomes), default=0)) + 1
                db.session.add(ProgramOutcome(
                    course_id=course.id, seq=next_seq, code=code, is_pso=is_pso,
                    description=description or None,
                ))
                db.session.commit()
                flash(f"Added {code}.", "success")
        return redirect(url_for("setup.manage_program_outcomes", course_id=course.id))
    return render_template("courses/program_outcomes.html", course=course)


@setup_bp.route("/<int:course_id>/program-outcomes/<int:po_id>/delete", methods=["POST"])
@coordinator_required
def delete_program_outcome(course_id, po_id):
    po = db.session.get(ProgramOutcome, po_id)
    if po and po.course_id == course_id:
        db.session.delete(po)
        db.session.commit()
        flash(f"Removed {po.code}.", "success")
    return redirect(url_for("setup.manage_program_outcomes", course_id=course_id))


# -------------------------------------------------------------- mapping

@setup_bp.route("/<int:course_id>/mapping", methods=["GET", "POST"])
@coordinator_required
def manage_mapping(course_id):
    course = _get_course_or_404(course_id)

    if request.method == "POST":
        existing = {(m.co_id, m.po_id): m for m in COPOMapping.query.join(CourseOutcome).filter(CourseOutcome.course_id == course.id)}
        for co in course.outcomes:
            for po in course.program_outcomes:
                field_name = f"corr_{co.id}_{po.id}"
                raw = request.form.get(field_name, "").strip()
                key = (co.id, po.id)
                if raw in ("", "0", "-"):
                    if key in existing:
                        db.session.delete(existing[key])
                    continue
                try:
                    level = int(raw)
                except ValueError:
                    continue
                if level < 1:
                    if key in existing:
                        db.session.delete(existing[key])
                    continue
                level = min(level, 3)
                if key in existing:
                    existing[key].correlation_level = level
                else:
                    db.session.add(COPOMapping(co_id=co.id, po_id=po.id, correlation_level=level))
        db.session.commit()
        flash("CO-PO mapping saved.", "success")
        return redirect(url_for("setup.manage_mapping", course_id=course.id))

    mapping_lookup = {}
    for co in course.outcomes:
        for m in COPOMapping.query.filter_by(co_id=co.id).all():
            mapping_lookup[(co.id, m.po_id)] = m.correlation_level

    return render_template("courses/mapping.html", course=course, mapping_lookup=mapping_lookup)
