"""
Marks entry: the student roster, and per-component score/response entry.

IA1/IA2/IA3/ASQM: a student x item grid of marks (blank = absent, matching
the template's "if absent, don't enter anything" rule).
EXIT_SURVEY: a student x question grid of A-E ratings.
EXTERNAL: a student x (internal total, external marks, result) table,
entered as totals - matches the template's manual "External" sheet.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.extensions import db
from app.models import (
    Course, Student, AssessmentComponent, Mark, ExternalResult, ExitSurveyResponse,
    EXIT_SURVEY_RATINGS,
)
from app.routes.structure_routes import COMPONENT_LABELS
from app.models import COMPONENT_TYPES
from app.roster_import import parse_roster_docx

marks_bp = Blueprint("marks", __name__, url_prefix="/courses/<int:course_id>/marks")


def _get_course_or_404(course_id):
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    return course


@marks_bp.route("/")
def list_components(course_id):
    course = _get_course_or_404(course_id)
    existing_by_type = {c.type: c for c in course.components}
    rows = []
    for comp_type in COMPONENT_TYPES:
        component = existing_by_type.get(comp_type)
        ready = comp_type == "EXTERNAL" or bool(component and component.items)
        rows.append({"type": comp_type, "label": COMPONENT_LABELS[comp_type], "ready": ready})
    return render_template("marks/list.html", course=course, rows=rows)


# ------------------------------------------------------------------ roster

@marks_bp.route("/roster", methods=["GET", "POST"])
def manage_roster(course_id):
    course = _get_course_or_404(course_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_one":
            usn = request.form.get("usn", "").strip()
            name = request.form.get("name", "").strip()
            if usn and name:
                if Student.query.filter_by(course_id=course.id, usn=usn).first():
                    flash(f"A student with USN {usn} already exists.", "error")
                else:
                    next_seq = (max((s.seq for s in course.students), default=0)) + 1
                    db.session.add(Student(course_id=course.id, seq=next_seq, usn=usn, name=name))
                    db.session.commit()
                    flash(f"Added {name}.", "success")
        elif action == "add_bulk":
            bulk_text = request.form.get("bulk_text", "")
            existing_usns = {s.usn for s in course.students}
            next_seq = (max((s.seq for s in course.students), default=0))
            added = 0
            skipped = 0
            for line in bulk_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split(",", 1)]
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    skipped += 1
                    continue
                usn, name = parts
                if usn in existing_usns:
                    skipped += 1
                    continue
                next_seq += 1
                db.session.add(Student(course_id=course.id, seq=next_seq, usn=usn, name=name))
                existing_usns.add(usn)
                added += 1
            db.session.commit()
            flash(f"Added {added} student(s)." + (f" Skipped {skipped} (duplicate USN or bad format)." if skipped else ""), "success")
        return redirect(url_for("marks.manage_roster", course_id=course.id))
    return render_template("marks/roster.html", course=course)


@marks_bp.route("/roster/<int:student_id>/delete", methods=["POST"])
def delete_student(course_id, student_id):
    student = db.session.get(Student, student_id)
    if student and student.course_id == course_id:
        db.session.delete(student)
        db.session.commit()
        flash(f"Removed {student.name} and all their marks/responses.", "success")
    return redirect(url_for("marks.manage_roster", course_id=course_id))


# ----------------------------------------------------------- roster import

@marks_bp.route("/roster/import", methods=["GET"])
def roster_import_form(course_id):
    course = _get_course_or_404(course_id)
    return render_template("marks/roster_import.html", course=course)


@marks_bp.route("/roster/import/parse", methods=["POST"])
def roster_import_parse(course_id):
    course = _get_course_or_404(course_id)
    uploaded = request.files.get("roster")
    if not uploaded or uploaded.filename == "":
        flash("Choose a file first.", "error")
        return redirect(url_for("marks.roster_import_form", course_id=course.id))
    if not uploaded.filename.lower().endswith(".docx"):
        flash(
            "That doesn't look like a Word document. PDF rosters aren't supported yet - "
            "please upload a .docx file, or add students by hand below.",
            "error",
        )
        return redirect(url_for("marks.roster_import_form", course_id=course.id))

    try:
        parsed = parse_roster_docx(uploaded.stream)
    except ImportError:
        flash(
            "The 'python-docx' library isn't installed. Run 'pip install -r requirements.txt' "
            "(with your virtual environment activated), restart the app, and try again.",
            "error",
        )
        return redirect(url_for("marks.roster_import_form", course_id=course.id))
    except Exception:
        flash(
            "Couldn't read that file - it may not be a real Word document, or it's corrupted. "
            "You can still add students by hand.",
            "error",
        )
        return redirect(url_for("marks.manage_roster", course_id=course.id))

    existing_usns = {s.usn for s in course.students}

    return render_template(
        "marks/roster_import_review.html",
        course=course, parsed=parsed, existing_usns=existing_usns,
    )


@marks_bp.route("/roster/import/confirm", methods=["POST"])
def roster_import_confirm(course_id):
    course = _get_course_or_404(course_id)

    existing_usns = {s.usn for s in course.students}
    next_seq = max((s.seq for s in course.students), default=0)
    added = 0
    skipped = 0

    usns = request.form.getlist("usn")
    names = request.form.getlist("name")
    for usn, name in zip(usns, names):
        usn = usn.strip()
        name = name.strip()
        if not usn or not name:
            continue  # a spare/blank row
        if usn in existing_usns:
            skipped += 1
            continue
        next_seq += 1
        db.session.add(Student(course_id=course.id, seq=next_seq, usn=usn, name=name))
        existing_usns.add(usn)
        added += 1

    db.session.commit()
    section_label = request.form.get("section_label", "").strip()
    flash(
        f"Imported {added} student(s) from Section {section_label}."
        + (f" Skipped {skipped} (already on the roster)." if skipped else ""),
        "success",
    )
    return redirect(url_for("marks.manage_roster", course_id=course.id))


# ------------------------------------------------------------ item-based

@marks_bp.route("/<comp_type>", methods=["GET", "POST"])
def enter_component_marks(course_id, comp_type):
    course = _get_course_or_404(course_id)
    if comp_type not in COMPONENT_TYPES:
        abort(404)

    component = next((c for c in course.components if c.type == comp_type), None)
    if component is None:
        if comp_type == "EXTERNAL":
            # EXTERNAL never has an item structure to set up - just create
            # the component record on first use so marks entry isn't
            # blocked behind an unnecessary trip to the Structure page.
            component = AssessmentComponent(course_id=course.id, type=comp_type, name=COMPONENT_LABELS[comp_type])
            db.session.add(component)
            db.session.commit()
        else:
            flash(f"Set up the item structure for {COMPONENT_LABELS[comp_type]} first.", "error")
            return redirect(url_for("structure.manage_component", course_id=course.id, comp_type=comp_type))

    if comp_type == "EXTERNAL":
        return _enter_external(course, component)
    if comp_type == "EXIT_SURVEY":
        return _enter_exit_survey(course, component)
    return _enter_item_marks(course, component, comp_type)


def _enter_item_marks(course, component, comp_type):
    items = component.items
    students = course.students

    if request.method == "POST":
        for student in students:
            for item in items:
                field = f"mark_{student.id}_{item.id}"
                raw = request.form.get(field, "").strip()
                existing = Mark.query.filter_by(student_id=student.id, item_id=item.id).first()
                if raw == "":
                    if existing:
                        db.session.delete(existing)
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if existing:
                    existing.marks_obtained = value
                else:
                    db.session.add(Mark(student_id=student.id, item_id=item.id, marks_obtained=value))
        db.session.commit()
        flash("Marks saved.", "success")
        return redirect(url_for("marks.enter_component_marks", course_id=course.id, comp_type=comp_type))

    marks_lookup = {}
    if students and items:
        student_ids = [s.id for s in students]
        item_ids = [i.id for i in items]
        for m in Mark.query.filter(Mark.student_id.in_(student_ids), Mark.item_id.in_(item_ids)).all():
            marks_lookup[(m.student_id, m.item_id)] = m.marks_obtained

    return render_template(
        "marks/item_grid.html", course=course, component=component, comp_type=comp_type,
        label=COMPONENT_LABELS[comp_type], items=items, students=students, marks_lookup=marks_lookup,
    )


def _enter_exit_survey(course, component):
    items = component.items
    students = course.students

    if request.method == "POST":
        for student in students:
            for item in items:
                field = f"rating_{student.id}_{item.id}"
                raw = request.form.get(field, "").strip()
                existing = ExitSurveyResponse.query.filter_by(student_id=student.id, item_id=item.id).first()
                if raw == "" or raw not in EXIT_SURVEY_RATINGS:
                    if existing:
                        db.session.delete(existing)
                    continue
                if existing:
                    existing.rating = raw
                else:
                    db.session.add(ExitSurveyResponse(student_id=student.id, item_id=item.id, rating=raw))
        db.session.commit()
        flash("Exit survey responses saved.", "success")
        return redirect(url_for("marks.enter_component_marks", course_id=course.id, comp_type="EXIT_SURVEY"))

    responses_lookup = {}
    if students and items:
        student_ids = [s.id for s in students]
        item_ids = [i.id for i in items]
        for r in ExitSurveyResponse.query.filter(
            ExitSurveyResponse.student_id.in_(student_ids), ExitSurveyResponse.item_id.in_(item_ids)
        ).all():
            responses_lookup[(r.student_id, r.item_id)] = r.rating

    return render_template(
        "marks/exit_survey_grid.html", course=course, component=component,
        items=items, students=students, responses_lookup=responses_lookup, ratings=EXIT_SURVEY_RATINGS,
    )


def _enter_external(course, component):
    students = course.students

    if request.method == "POST":
        for student in students:
            internal_raw = request.form.get(f"internal_{student.id}", "").strip()
            external_raw = request.form.get(f"external_{student.id}", "").strip()
            result_raw = request.form.get(f"result_{student.id}", "").strip().upper()

            existing = ExternalResult.query.filter_by(student_id=student.id).first()
            has_any = internal_raw or external_raw or result_raw
            if not has_any:
                if existing:
                    db.session.delete(existing)
                continue
            if existing is None:
                existing = ExternalResult(student_id=student.id)
                db.session.add(existing)
            existing.internal_total = float(internal_raw) if internal_raw else None
            existing.external_marks = float(external_raw) if external_raw else None
            existing.result = result_raw if result_raw in ("P", "F") else None
        db.session.commit()
        flash("External results saved.", "success")
        return redirect(url_for("marks.enter_component_marks", course_id=course.id, comp_type="EXTERNAL"))

    results_lookup = {r.student_id: r for r in ExternalResult.query.filter(
        ExternalResult.student_id.in_([s.id for s in students])
    ).all()} if students else {}

    return render_template(
        "marks/external_grid.html", course=course, component=component,
        students=students, results_lookup=results_lookup,
    )
