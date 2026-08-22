"""
Render a course's attainment report straight to PDF with reportlab - a
pure-Python library (no LibreOffice, no other system software) - instead of
converting the Excel export to PDF.

This is NOT "take the .xlsx and render it" - it's a separate report built
directly from the same data and the same rules app/engine.py already uses
for the Results page, so the numbers always agree with what's on screen.
The layout is deliberately its own, not a page-for-page copy of the
workbook: a printed page is much narrower than a spreadsheet, so the raw
per-question marks grids (one column per question, which is how the Excel
export and the original template show them) are compacted here into
per-CO/per-student summaries instead - same underlying marks, just
organized to actually fit on paper. Anyone who needs the literal per-
question columns still has the Excel export for that.

Sections, each starting on its own page:
  1. Course info (institution, subject, faculty, targets, CO list)
  2. CO attainment (IA/ASQM/CIA/External/Exit/Blended/Level)
  3. PO/PSO attainment (correlation-weighted, plus the correlation matrix)
  4. Internal assessment summary (IA pooled + ASQM: per-CO and per-student)
  5. External exam (class summary + per-student marks/result)
  6. Exit survey (per-question and per-CO summary + per-student ratings)
  7. Result analysis (pass/fail per assessment)
  8. Gap analysis (CO and PO/PSO attainment vs a default target Level)

Every figure here is computed by the same functions app/engine.py exposes
(marks_based_co_attainment, external_co_attainment, exit_survey_*,
level_for_percentage) or, where the Results page only needed a single
summary number and this report also wants the attendance/pass counts
behind it, a small local helper with the identical logic - never a
re-derivation that could silently drift from what the Results page shows.
"""
import io
import os

from openpyxl import load_workbook
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.engine import (
    compute_course_attainment, exit_survey_co_attainment, exit_survey_item_pct,
    external_co_attainment, level_for_percentage, marks_based_co_attainment,
)
from app.models import COPOMapping, ExitSurveyResponse, ExternalResult, Mark

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_assets", "attainment_template.xlsx")

PAGE_SIZE = landscape(A4)
MARGIN = 1.4 * cm

_styles = getSampleStyleSheet()
TITLE_STYLE = ParagraphStyle("ReportTitle", parent=_styles["Title"], fontName="Times-Bold", fontSize=16, alignment=TA_CENTER, spaceAfter=2)
SUBTITLE_STYLE = ParagraphStyle("ReportSubtitle", parent=_styles["Normal"], fontName="Times-Roman", fontSize=11, alignment=TA_CENTER, spaceAfter=2)
SECTION_STYLE = ParagraphStyle("SectionHeading", parent=_styles["Heading2"], fontName="Times-Bold", fontSize=13, spaceBefore=6, spaceAfter=6)
BODY_STYLE = ParagraphStyle("Body", parent=_styles["Normal"], fontName="Times-Roman", fontSize=9.5, leading=12)
NOTE_STYLE = ParagraphStyle("Note", parent=_styles["Normal"], fontName="Times-Italic", fontSize=8, textColor=colors.grey, spaceBefore=4)

HEADER_BG = colors.HexColor("#2f4f4f")
HEADER_FG = colors.white
ALT_ROW_BG = colors.HexColor("#f2f2f2")


def _fmt_pct(value, decimals=1):
    return f"{value:.{decimals}f}%" if value is not None else "-"


def _fmt_num(value, decimals=2):
    return f"{value:.{decimals}f}" if value is not None else "-"


def _table(data, col_widths=None, header_rows=1, small=False):
    """A Table styled consistently across the whole report: a dark bold
    header row (or rows), thin grid lines, and light banded rows for
    readability - the same look for every table in the document."""
    font_size = 7.5 if small else 8.5
    t = Table(data, colWidths=col_widths, repeatRows=header_rows)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("FONTNAME", (0, 0), (-1, header_rows - 1), "Times-Bold"),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), HEADER_FG),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for row in range(header_rows, len(data)):
        if (row - header_rows) % 2 == 1:
            style.append(("BACKGROUND", (0, row), (-1, row), ALT_ROW_BG))
    t.setStyle(TableStyle(style))
    return t


def _logo_flowable():
    """The AMC Engineering College crest embedded in the shipped template,
    reused here for a consistent look with the Excel export - best effort,
    silently skipped if the template's image can't be read for any reason
    (a missing/corrupt template asset shouldn't break the whole report)."""
    try:
        wb = load_workbook(TEMPLATE_PATH)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            images = getattr(ws, "_images", None)
            if images:
                data = images[0].ref.getvalue() if hasattr(images[0].ref, "getvalue") else images[0].ref.read()
                return Image(io.BytesIO(data), width=2.3 * cm, height=1.8 * cm)
    except Exception:
        return None
    return None


def _course_header(course, subtitle):
    flowables = []
    logo = _logo_flowable()
    title_block = [
        Paragraph(course.institution_name or "AMC ENGINEERING COLLEGE", TITLE_STYLE),
        Paragraph(f"DEPARTMENT OF {course.department.upper()}" if course.department else "", SUBTITLE_STYLE),
        Paragraph(subtitle, SUBTITLE_STYLE),
    ]
    if logo is not None:
        header_table = Table([[logo, title_block]], colWidths=[2.6 * cm, None])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ]))
        flowables.append(header_table)
    else:
        flowables.extend(title_block)
    flowables.append(Spacer(1, 0.3 * cm))
    return flowables


def _info_line(course):
    text = (
        f"<b>Subject:</b> {course.subject_code} - {course.subject_name} &nbsp;&nbsp; "
        f"<b>Faculty:</b> {course.faculty_display or '-'} &nbsp;&nbsp; "
        f"<b>Academic Year:</b> {course.academic_year or '-'} &nbsp;&nbsp; "
        f"<b>Semester:</b> {course.semester or '-'}"
    )
    return Paragraph(text, BODY_STYLE)


def build_pdf_bytes(course):
    """Returns the rendered report as PDF bytes for `course`."""
    cos = sorted(course.outcomes, key=lambda co: co.seq)
    pos = sorted(course.program_outcomes, key=lambda po: po.seq)
    students = sorted(course.students, key=lambda s: s.seq)
    components_by_type = {c.type: c for c in course.components}

    result = compute_course_attainment(course)

    story = []
    story += _build_cover_section(course, cos)
    story.append(PageBreak())
    story += _build_co_attainment_section(course, result)
    story.append(PageBreak())
    story += _build_po_attainment_section(course, cos, pos, result)
    story.append(PageBreak())
    story += _build_internal_section(course, cos, students, components_by_type, "IA1", "IA2", "IA3", title="Internal Assessment (IA1-IA3, pooled)")
    story.append(PageBreak())
    story += _build_internal_section(course, cos, students, components_by_type, "ASQM", title="Assignment / Quiz / Seminar (ASQM)")
    story.append(PageBreak())
    story += _build_external_section(course, students)
    story.append(PageBreak())
    story += _build_exit_survey_section(course, cos, students, components_by_type)
    story.append(PageBreak())
    story += _build_result_analysis_section(course, students, components_by_type)
    story.append(PageBreak())
    story += _build_gap_section(course, result)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{course.subject_code} Attainment Report",
    )
    doc.build(story)
    return buffer.getvalue()


# ------------------------------------------------------------------ cover

def _build_cover_section(course, cos):
    flowables = _course_header(course, "CO / PO Attainment Report")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.5 * cm))

    flowables.append(Paragraph("Course Outcomes", SECTION_STYLE))
    if cos:
        rows = [["CO", "Description"]] + [[co.code, co.description or "-"] for co in cos]
        flowables.append(_table(rows, col_widths=[2.5 * cm, None]))
    else:
        flowables.append(Paragraph("No Course Outcomes defined yet.", BODY_STYLE))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("Targets", SECTION_STYLE))
    target_rows = [
        ["Level 1 target (% of students)", _fmt_pct(course.target_level1_pct)],
        ["Level 2 target (% of students)", _fmt_pct(course.target_level2_pct)],
        ["Level 3 target (% of students)", _fmt_pct(course.target_level3_pct)],
        ["Internal marks cutoff (per student, per CO)", _fmt_pct(course.internal_marks_cutoff_pct)],
    ]
    flowables.append(_table([["Target", "Value"]] + target_rows, col_widths=[10 * cm, 4 * cm]))
    return flowables


# ---------------------------------------------------------- CO attainment

def _build_co_attainment_section(course, result):
    flowables = _course_header(course, "CO Attainment")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.4 * cm))
    flowables.append(Paragraph(
        "CIA = average(IA, ASQM). Blended % = 90% x (50% x CIA + 50% x External) + 10% x Exit survey. "
        "Level uses the Level 1/2/3 targets on the cover page.", NOTE_STYLE,
    ))

    header = ["CO", "IA %", "ASQM %", "CIA %", "External %", "Exit Survey %", "Blended %", "Level"]
    rows = [header]
    for row in result.co_rows:
        rows.append([
            row.co_code, _fmt_pct(row.ia_pct), _fmt_pct(row.asqm_pct), _fmt_pct(row.cia_pct),
            _fmt_pct(row.external_pct), _fmt_pct(row.exit_survey_pct), _fmt_pct(row.blended_pct),
            str(row.level) if row.level is not None else "-",
        ])
    if len(rows) == 1:
        rows.append(["No Course Outcomes defined yet.", "", "", "", "", "", "", ""])
    flowables.append(_table(rows, col_widths=[2.5 * cm] + [3.2 * cm] * 6 + [2 * cm]))
    return flowables


# ------------------------------------------------------ PO/PSO attainment

def _build_po_attainment_section(course, cos, pos, result):
    flowables = _course_header(course, "Course PO / PSO Attainment")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.4 * cm))
    flowables.append(Paragraph(
        "Attainment is the correlation-weighted average of the mapped COs' Levels (0-3).", NOTE_STYLE,
    ))

    header = ["PO / PSO", "Type", "Attainment", "Mapped COs"]
    rows = [header]
    for row in result.po_rows:
        rows.append([row.po_code, "PSO" if row.is_pso else "PO", _fmt_num(row.attainment), str(row.mapped_co_count)])
    if len(rows) == 1:
        rows.append(["No Program Outcomes defined yet.", "", "", ""])
    flowables.append(_table(rows, col_widths=[4 * cm, 3 * cm, 4 * cm, 4 * cm]))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("CO-PO/PSO Correlation Matrix", SECTION_STYLE))
    if cos and pos:
        mapping_lookup = {}
        co_ids = [co.id for co in cos]
        for m in COPOMapping.query.filter(COPOMapping.co_id.in_(co_ids)).all():
            mapping_lookup[(m.co_id, m.po_id)] = m.correlation_level
        header_row = ["CO \\ PO"] + [po.code for po in pos]
        matrix_rows = [header_row]
        for co in cos:
            matrix_rows.append([co.code] + [
                str(mapping_lookup[(co.id, po.id)]) if (co.id, po.id) in mapping_lookup else "-"
                for po in pos
            ])
        col_widths = [2.5 * cm] + [max(1.6 * cm, (26 * cm - 2.5 * cm) / max(len(pos), 1))] * len(pos)
        flowables.append(_table(matrix_rows, col_widths=col_widths, small=True))
    else:
        flowables.append(Paragraph("No CO-PO mapping to show yet.", BODY_STYLE))
    return flowables


# -------------------------------------------------------- internal marks

def _marks_attendance_stats(students, items, marks_lookup, cutoff_pct):
    """Same rule as engine.marks_based_co_attainment, but also returning
    the attended/passed counts behind the final percentage (the Results
    page only ever needed the percentage) - used for this report's
    per-CO and per-assessment summary rows."""
    attended = 0
    passed = 0
    for student in students:
        scored, allotted, did_attempt = 0.0, 0.0, False
        for item in items:
            mark = marks_lookup.get((student.id, item.id))
            if mark is not None:
                did_attempt = True
                scored += mark
                allotted += (item.max_marks or 0.0)
        if did_attempt and allotted > 0:
            attended += 1
            if scored / allotted * 100 >= cutoff_pct:
                passed += 1
    pct = (passed / attended * 100) if attended else None
    return attended, passed, pct


def _build_internal_section(course, cos, students, components_by_type, *component_types, title):
    flowables = _course_header(course, title)
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.3 * cm))

    items = []
    for comp_type in component_types:
        component = components_by_type.get(comp_type)
        if component:
            items.extend(component.items)

    if not items or not students:
        flowables.append(Paragraph("No items/students entered for this component yet.", BODY_STYLE))
        return flowables

    student_ids = [s.id for s in students]
    item_ids = [i.id for i in items]
    marks_lookup = {
        (m.student_id, m.item_id): m.marks_obtained
        for m in Mark.query.filter(Mark.student_id.in_(student_ids), Mark.item_id.in_(item_ids)).all()
    }

    flowables.append(Paragraph("Per-CO Summary", SECTION_STYLE))
    co_header = ["CO", "Students Attended", "Students Passed (>= cutoff)", "Attainment %"]
    co_rows = [co_header]
    for co in cos:
        co_items = [item for item in items if any(link.co_id == co.id for link in item.co_links)]
        if not co_items:
            co_rows.append([co.code, "-", "-", "-"])
            continue
        attended, passed, pct = _marks_attendance_stats(students, co_items, marks_lookup, course.internal_marks_cutoff_pct)
        co_rows.append([co.code, str(attended), str(passed), _fmt_pct(pct)])
    flowables.append(_table(co_rows, col_widths=[3 * cm, 6 * cm, 7 * cm, 4 * cm]))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("Per-Student Summary (% scored per CO)", SECTION_STYLE))
    header_row = ["Sl.No.", "USN", "Name"] + [co.code for co in cos]
    student_rows = [header_row]
    co_items_by_co = {co.id: [item for item in items if any(link.co_id == co.id for link in item.co_links)] for co in cos}
    for i, student in enumerate(students, start=1):
        row = [str(i), student.usn, student.name]
        for co in cos:
            co_items = co_items_by_co[co.id]
            scored = allotted = 0.0
            attempted = False
            for item in co_items:
                mark = marks_lookup.get((student.id, item.id))
                if mark is not None:
                    attempted = True
                    scored += mark
                    allotted += (item.max_marks or 0.0)
            pct = (scored / allotted * 100) if attempted and allotted > 0 else None
            row.append(_fmt_pct(pct, decimals=0))
        student_rows.append(row)
    col_widths = [1.4 * cm, 3.2 * cm, None] + [max(1.6 * cm, (22 * cm) / max(len(cos), 1))] * len(cos)
    flowables.append(_table(student_rows, col_widths=col_widths, small=True))
    return flowables


# ------------------------------------------------------------- external

def _build_external_section(course, students):
    flowables = _course_header(course, "External (Semester End Exam)")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.3 * cm))

    if not students:
        flowables.append(Paragraph("No students in the roster yet.", BODY_STYLE))
        return flowables

    results_by_student = {
        r.student_id: r
        for r in ExternalResult.query.filter(ExternalResult.student_id.in_([s.id for s in students])).all()
    }
    considered = [r for r in results_by_student.values() if r.result in ("P", "F") and r.external_marks is not None]
    appeared = len(considered)
    class_average = (sum(r.external_marks for r in considered) / appeared) if appeared else None
    above_average = sum(
        1 for r in results_by_student.values()
        if r.external_marks is not None and class_average is not None and r.external_marks >= class_average
    )
    pct = external_co_attainment(students, results_by_student)

    flowables.append(Paragraph("Class Summary", SECTION_STYLE))
    summary_rows = [
        ["Students appeared (P or F)", str(appeared)],
        ["Class average (external marks)", _fmt_num(class_average)],
        ["Students scoring >= class average", str(above_average)],
        ["External attainment %", _fmt_pct(pct)],
    ]
    flowables.append(_table([["Metric", "Value"]] + summary_rows, col_widths=[10 * cm, 4 * cm]))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("Per-Student Marks and Result", SECTION_STYLE))
    header = ["Sl.No.", "USN", "Name", "Internal Total (50)", "External Marks (100)", "Total", "Result"]
    rows = [header]
    for i, student in enumerate(students, start=1):
        r = results_by_student.get(student.id)
        internal = r.internal_total if r else None
        external = r.external_marks if r else None
        total = (internal or 0) + (external or 0) if (internal is not None or external is not None) else None
        rows.append([
            str(i), student.usn, student.name,
            _fmt_num(internal, 1) if internal is not None else "-",
            _fmt_num(external, 1) if external is not None else "-",
            _fmt_num(total, 1) if total is not None else "-",
            (r.result if r and r.result else "-"),
        ])
    flowables.append(_table(rows, col_widths=[1.4 * cm, 3.2 * cm, None, 3.5 * cm, 3.8 * cm, 2.5 * cm, 2.5 * cm], small=True))
    return flowables


# ---------------------------------------------------------- exit survey

def _build_exit_survey_section(course, cos, students, components_by_type):
    flowables = _course_header(course, "Course Exit Survey")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.3 * cm))

    component = components_by_type.get("EXIT_SURVEY")
    items = component.items if component else []
    if not items or not students:
        flowables.append(Paragraph("No exit-survey questions/students entered yet.", BODY_STYLE))
        return flowables

    student_ids = [s.id for s in students]
    item_ids = [item.id for item in items]
    responses_by_item = {item.id: [] for item in items}
    for r in ExitSurveyResponse.query.filter(
        ExitSurveyResponse.student_id.in_(student_ids), ExitSurveyResponse.item_id.in_(item_ids)
    ).all():
        responses_by_item[r.item_id].append(r.rating)

    flowables.append(Paragraph("Per-Question Summary", SECTION_STYLE))
    q_rows = [["Question", "% rated A or B"]]
    for item in items:
        q_rows.append([item.label, _fmt_pct(exit_survey_item_pct(item.id, responses_by_item))])
    flowables.append(_table(q_rows, col_widths=[6 * cm, 6 * cm]))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("Per-CO Summary", SECTION_STYLE))
    co_rows = [["CO", "Exit Survey Attainment %"]]
    for co in cos:
        mapped_items = [item for item in items if any(link.co_id == co.id for link in item.co_links)]
        co_rows.append([co.code, _fmt_pct(exit_survey_co_attainment(mapped_items, responses_by_item)) if mapped_items else "-"])
    flowables.append(_table(co_rows, col_widths=[6 * cm, 6 * cm]))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("Per-Student Ratings", SECTION_STYLE))
    response_lookup = {}
    for item in items:
        for r in ExitSurveyResponse.query.filter_by(item_id=item.id).all():
            response_lookup[(r.student_id, item.id)] = r.rating
    header_row = ["Sl.No.", "USN", "Name"] + [item.label for item in items]
    rows = [header_row]
    for i, student in enumerate(students, start=1):
        row = [str(i), student.usn, student.name]
        for item in items:
            row.append(response_lookup.get((student.id, item.id), "-"))
        rows.append(row)
    col_widths = [1.4 * cm, 3.2 * cm, None] + [max(1.4 * cm, (22 * cm) / max(len(items), 1))] * len(items)
    flowables.append(_table(rows, col_widths=col_widths, small=True))
    return flowables


# ------------------------------------------------------- result analysis

def _build_result_analysis_section(course, students, components_by_type):
    flowables = _course_header(course, "Result Analysis (Pass/Fail Summary)")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.4 * cm))
    flowables.append(Paragraph(
        "Pass = a student's total marks for the assessment, as a % of the maximum, at or above "
        "the internal marks cutoff (for External: the entered Result column).", NOTE_STYLE,
    ))

    header = ["Assessment", "Students Counted", "Students Passed", "Pass %"]
    rows = [header]

    if students:
        student_ids = [s.id for s in students]
        for comp_type, label in (("IA1", "IA1"), ("IA2", "IA2"), ("IA3", "IA3"), ("ASQM", "ASQM")):
            component = components_by_type.get(comp_type)
            items = component.items if component else []
            if not items:
                continue
            item_ids = [item.id for item in items]
            marks_lookup = {
                (m.student_id, m.item_id): m.marks_obtained
                for m in Mark.query.filter(Mark.student_id.in_(student_ids), Mark.item_id.in_(item_ids)).all()
            }
            attended, passed, pct = _marks_attendance_stats(students, items, marks_lookup, course.internal_marks_cutoff_pct)
            rows.append([label, str(attended), str(passed), _fmt_pct(pct)])

        results_by_student = {
            r.student_id: r
            for r in ExternalResult.query.filter(ExternalResult.student_id.in_(student_ids)).all()
        }
        considered = [r for r in results_by_student.values() if r.result in ("P", "F")]
        if considered:
            passed = sum(1 for r in considered if r.result == "P")
            rows.append(["External (SEE)", str(len(considered)), str(passed), _fmt_pct(passed / len(considered) * 100)])

    if len(rows) == 1:
        rows.append(["No assessment data entered for this course yet.", "", "", ""])
    flowables.append(_table(rows, col_widths=[6 * cm, 6 * cm, 6 * cm, 4 * cm]))
    return flowables


# ------------------------------------------------------------ gap sheet

def _build_gap_section(course, result):
    flowables = _course_header(course, "Attainment Gap Analysis (current offering vs target)")
    flowables.append(_info_line(course))
    flowables.append(Spacer(1, 0.4 * cm))
    flowables.append(Paragraph(
        "Target Level defaults to 2 (a common NBA convention) - this isn't something the app "
        "stores per CO/PO, so treat this column as a starting point to annotate by hand.", NOTE_STYLE,
    ))

    flowables.append(Paragraph("CO Attainment Gap", SECTION_STYLE))
    co_header = ["CO", "Target Level", "Actual Level", "Actual Attainment %", "Gap"]
    co_rows = [co_header]
    for row in result.co_rows:
        gap = (2 - row.level) if row.level is not None else None
        co_rows.append([row.co_code, "2", str(row.level) if row.level is not None else "-", _fmt_pct(row.blended_pct), _fmt_num(gap, 0) if gap is not None else "-"])
    if len(co_rows) == 1:
        co_rows.append(["No Course Outcomes defined yet.", "", "", "", ""])
    flowables.append(_table(co_rows, col_widths=[3 * cm, 4 * cm, 4 * cm, 5 * cm, 3 * cm]))

    flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(Paragraph("PO/PSO Attainment Gap", SECTION_STYLE))
    po_header = ["PO/PSO", "Target Level", "Actual Attainment", "Gap"]
    po_rows = [po_header]
    for row in result.po_rows:
        gap = (2 - row.attainment) if row.attainment is not None else None
        po_rows.append([row.po_code, "2", _fmt_num(row.attainment), _fmt_num(gap) if gap is not None else "-"])
    if len(po_rows) == 1:
        po_rows.append(["No Program Outcomes defined yet.", "", "", ""])
    flowables.append(_table(po_rows, col_widths=[4 * cm, 4 * cm, 5 * cm, 4 * cm]))
    return flowables
