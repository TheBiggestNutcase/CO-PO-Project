"""
Marks entry: the student roster, and per-component score/response entry.

IA1/IA2/IA3/ASQM: a student x item grid of marks (blank = absent, matching
the template's "if absent, don't enter anything" rule).
EXIT_SURVEY: a student x question grid of A-E ratings.
EXTERNAL: a student x (internal total, external marks, result) table,
entered as totals - matches the template's manual "External" sheet.
"""
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user

from app.extensions import db
from app.models import (
    Course, Student, AssessmentComponent, Mark, ExternalResult, ExitSurveyResponse,
    EXIT_SURVEY_RATINGS,
)
from app.routes.structure_routes import COMPONENT_LABELS
from app.models import COMPONENT_TYPES
from app.roster_import import parse_roster_docx
from app.exit_survey_import import parse_exit_survey_file
from app.auth import coordinator_required
from app.engine import ia_dropped_item_ids

IA_TYPES = ("IA1", "IA2", "IA3")

marks_bp = Blueprint("marks", __name__, url_prefix="/courses/<int:course_id>/marks")


@marks_bp.before_request
def _require_course_access():
    """Baseline for the whole blueprint: coordinator (any course) or a
    teacher assigned to *this* course_id. Roster management routes layer
    an additional @coordinator_required on top, below."""
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
@coordinator_required
def manage_roster(course_id):
    course = _get_course_or_404(course_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_one":
            usn = request.form.get("usn", "").strip()
            name = request.form.get("name", "").strip()
            section = request.form.get("section", "").strip()
            if usn and name:
                if Student.query.filter_by(course_id=course.id, usn=usn).first():
                    flash(f"A student with USN {usn} already exists.", "error")
                else:
                    next_seq = (max((s.seq for s in course.students), default=0)) + 1
                    db.session.add(Student(course_id=course.id, seq=next_seq, usn=usn, name=name, section=section))
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
                # "USN, Name" (2 parts) or, for a multi-section course,
                # "USN, Name, Section" (3 parts) - maxsplit=2 so a name
                # itself containing a comma still only splits into at
                # most 3 pieces rather than more.
                parts = [p.strip() for p in line.split(",", 2)]
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    skipped += 1
                    continue
                usn, name = parts[0], parts[1]
                section = parts[2] if len(parts) == 3 else ""
                if usn in existing_usns:
                    skipped += 1
                    continue
                next_seq += 1
                db.session.add(Student(course_id=course.id, seq=next_seq, usn=usn, name=name, section=section))
                existing_usns.add(usn)
                added += 1
            db.session.commit()
            flash(f"Added {added} student(s)." + (f" Skipped {skipped} (duplicate USN or bad format)." if skipped else ""), "success")
        return redirect(url_for("marks.manage_roster", course_id=course.id))
    return render_template("marks/roster.html", course=course)


@marks_bp.route("/roster/<int:student_id>/delete", methods=["POST"])
@coordinator_required
def delete_student(course_id, student_id):
    student = db.session.get(Student, student_id)
    if student and student.course_id == course_id:
        db.session.delete(student)
        db.session.commit()
        flash(f"Removed {student.name} and all their marks/responses.", "success")
    return redirect(url_for("marks.manage_roster", course_id=course_id))


# ----------------------------------------------------------- roster import

@marks_bp.route("/roster/import", methods=["GET"])
@coordinator_required
def roster_import_form(course_id):
    course = _get_course_or_404(course_id)
    return render_template("marks/roster_import.html", course=course)


@marks_bp.route("/roster/import/parse", methods=["POST"])
@coordinator_required
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
@coordinator_required
def roster_import_confirm(course_id):
    """Imports every section the coordinator ticked "Import this
    section" for, in one submission - not just one section at a time -
    so a course spanning several sections (see Course.sections_list) can
    pull its whole roster from one uploaded roster document in a single
    step. Each section's rows are namespaced in the form as
    usn__<label>/name__<label> (see roster_import_review.html)."""
    course = _get_course_or_404(course_id)

    existing_usns = {s.usn for s in course.students}
    next_seq = max((s.seq for s in course.students), default=0)
    total_added = 0
    total_skipped = 0
    imported_labels = []

    included_sections = request.form.getlist("include_section")
    for label in included_sections:
        usns = request.form.getlist(f"usn__{label}")
        names = request.form.getlist(f"name__{label}")
        added_here = 0
        for usn, name in zip(usns, names):
            usn = usn.strip()
            name = name.strip()
            if not usn or not name:
                continue  # a spare/blank row
            if usn in existing_usns:
                total_skipped += 1
                continue
            next_seq += 1
            db.session.add(Student(course_id=course.id, seq=next_seq, usn=usn, name=name, section=label))
            existing_usns.add(usn)
            added_here += 1
            total_added += 1
        if added_here:
            imported_labels.append(label)

    db.session.commit()
    if imported_labels:
        flash(
            f"Imported {total_added} student(s) from Section(s) {', '.join(imported_labels)}."
            + (f" Skipped {total_skipped} (already on the roster)." if total_skipped else ""),
            "success",
        )
    else:
        flash("No sections were selected to import.", "error")
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

    # For IA1/IA2/IA3: show each student's effective total under the
    # either/or rule (higher of Q1/Q2 + higher of Q3/Q4, see
    # app/engine.py) and which main question got dropped, if any - so a
    # coordinator can see the grading rule actually applied, not just
    # trust it happened silently at Results time.
    ia_summary = None
    if comp_type in IA_TYPES and items:
        ia_summary = {}
        for student in students:
            dropped_ids = ia_dropped_item_ids(items, marks_lookup, student.id)
            kept_total = sum(
                marks_lookup.get((student.id, item.id), 0.0)
                for item in items if item.id not in dropped_ids
            )
            dropped_mqs = sorted({item.main_question for item in items if item.id in dropped_ids})
            ia_summary[student.id] = {"total": kept_total, "dropped": dropped_mqs}

    return render_template(
        "marks/item_grid.html", course=course, component=component, comp_type=comp_type,
        label=COMPONENT_LABELS[comp_type], items=items, students=students, marks_lookup=marks_lookup,
        ia_summary=ia_summary,
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


# --------------------------------------------------- exit survey import

def _get_exit_survey_component_or_redirect(course):
    """The exit-survey questions must already exist (with their CO tags)
    before import can match sheet columns to them - same "set up
    Structure first" rule as manual entry. Returns (component, None) or
    (None, redirect_response)."""
    component = next((c for c in course.components if c.type == "EXIT_SURVEY"), None)
    if component is None or not component.items:
        flash("Set up the exit-survey question list on Structure first.", "error")
        return None, redirect(url_for("structure.manage_component", course_id=course.id, comp_type="EXIT_SURVEY"))
    return component, None


@marks_bp.route("/EXIT_SURVEY/import", methods=["GET"])
@coordinator_required
def exit_survey_import_form(course_id):
    course = _get_course_or_404(course_id)
    component, redirect_response = _get_exit_survey_component_or_redirect(course)
    if redirect_response:
        return redirect_response
    return render_template("marks/exit_survey_import.html", course=course)


@marks_bp.route("/EXIT_SURVEY/import/parse", methods=["POST"])
@coordinator_required
def exit_survey_import_parse(course_id):
    course = _get_course_or_404(course_id)
    component, redirect_response = _get_exit_survey_component_or_redirect(course)
    if redirect_response:
        return redirect_response

    uploaded = request.files.get("sheet")
    if not uploaded or uploaded.filename == "":
        flash("Choose a file first.", "error")
        return redirect(url_for("marks.exit_survey_import_form", course_id=course.id))
    if not uploaded.filename.lower().endswith((".xlsx", ".csv")):
        flash("That doesn't look like a spreadsheet export - please upload the sheet as .xlsx or .csv.", "error")
        return redirect(url_for("marks.exit_survey_import_form", course_id=course.id))

    try:
        parsed = parse_exit_survey_file(uploaded.stream, uploaded.filename, course.students, component.items)
    except ImportError:
        flash(
            "The 'openpyxl' library isn't installed. Run 'pip install -r requirements.txt' "
            "(with your virtual environment activated), restart the app, and try again.",
            "error",
        )
        return redirect(url_for("marks.exit_survey_import_form", course_id=course.id))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("marks.exit_survey_import_form", course_id=course.id))
    except Exception:
        flash(
            "Couldn't read that file - it may not be a real spreadsheet export, or it's corrupted. "
            "You can still enter responses by hand.",
            "error",
        )
        return redirect(url_for("marks.enter_component_marks", course_id=course.id, comp_type="EXIT_SURVEY"))

    return render_template(
        "marks/exit_survey_import_review.html", course=course,
        parsed=parsed, students=course.students, items=component.items, ratings=EXIT_SURVEY_RATINGS,
    )


@marks_bp.route("/EXIT_SURVEY/import/confirm", methods=["POST"])
@coordinator_required
def exit_survey_import_confirm(course_id):
    course = _get_course_or_404(course_id)
    component, redirect_response = _get_exit_survey_component_or_redirect(course)
    if redirect_response:
        return redirect_response

    valid_item_ids = {i.id for i in component.items}
    valid_student_ids = {s.id for s in course.students}

    item_for_col = {}
    student_for_row = {}
    for key, value in request.form.items():
        if not value:
            continue
        if key.startswith("item_for_col_"):
            try:
                item_id = int(value)
            except ValueError:
                continue
            if item_id in valid_item_ids:
                item_for_col[key[len("item_for_col_"):]] = item_id
        elif key.startswith("student_for_row_"):
            try:
                student_id = int(value)
            except ValueError:
                continue
            if student_id in valid_student_ids:
                student_for_row[key[len("student_for_row_"):]] = student_id

    saved = 0
    for key, value in request.form.items():
        if not key.startswith("rating_") or not value:
            continue
        rating = value.strip().upper()
        if rating not in EXIT_SURVEY_RATINGS:
            continue
        _, row_num, col_idx = key.split("_", 2)  # "rating_<row_number>_<col_index>"
        student_id = student_for_row.get(row_num)
        item_id = item_for_col.get(col_idx)
        if not student_id or not item_id:
            continue  # this row's student, or this column's question, was left unmatched - skip

        existing = ExitSurveyResponse.query.filter_by(student_id=student_id, item_id=item_id).first()
        if existing:
            existing.rating = rating
        else:
            db.session.add(ExitSurveyResponse(student_id=student_id, item_id=item_id, rating=rating))
        saved += 1

    db.session.commit()
    flash(f"Saved {saved} exit survey response(s).", "success")
    return redirect(url_for("marks.enter_component_marks", course_id=course.id, comp_type="EXIT_SURVEY"))
