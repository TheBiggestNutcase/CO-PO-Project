"""
Export a Course's full attainment workbook, styled after the original
"attainment template.xlsx" (AMC Engineering College's CO/PO workbook) -
same logos, same institution header banner, same sheet names, and the
same overall formula philosophy (raw marks in, everything else a live
Excel formula) - filled with this course's real data instead of the one
physics example the template shipped with.

Why generated, not literally reused cell-for-cell: the original's raw
marks-entry sheets (IA Test only / ASQM only / External / Exit survey)
hard-code a specific shape - exactly 5 COs, exactly "Test 1 covers CO1+
CO2" style splits, exactly 3 sub-questions (A/B/C) per question. Our
Course model is intentionally more flexible (any number of COs, any
number of items per component, tagged to any CO) - a real course here
essentially never matches that fixed shape. So instead of pretending to
slot into the original's exact columns, every sheet below is *rebuilt*
from the template as a starting point (same file, same logos, same
title-banner styling) with a column layout sized to this course's actual
data, using the same kind of formulas (SUMIF/SUMIFS/COUNT/COUNTIF) the
original used for the same job.

Two sheets in the original ("CO attainment gap", "Course PO attainment
gap") already contained #REF! errors in the file we were given - broken
year-over-year (CAYm1/CAYm2) comparison formulas left over from rows/
sheets being deleted at some point. We don't have prior-year data to
compare against either (this app tracks one course offering at a time),
so those sheets are rebuilt here as a working *current year vs target*
gap analysis instead of reproducing the original's specific breakage.
Their per-row "Target Level" is not something our Course model stores
(we only store the 3 percentage thresholds used to derive a Level from a
blended %), so it's seeded with a common NBA default of 2 in an editable,
yellow-highlighted cell per row - the assumption is documented in a note
on each sheet, per the xlsx skill's "document every hardcoded assumption"
rule.

Cross-checked against app/engine.py: the live formulas this module
writes (SUMIF-by-CO-index, %-of-students-above-cutoff, CO-level blend,
correlation-weighted PO average, etc.) implement the exact same rules
engine.py already reimplements in Python - so the numbers Excel computes
here should match what the Results page shows, give or take rounding.
engine.py itself is not called here; the export is formula-driven end to
end, per the "never hardcoded results" rule - open the file, edit a
mark, everything downstream recalculates.

Simplification, stated once here rather than at every call site: where
an AssessmentItem maps to more than one CO (ItemCOMapping allows it),
the marks-grid sheets' "Cijk" row - like the original template's - can
only name a single CO per question column, so we use the item's first
mapped CO. Every item produced by populate_test_data.py (and by the
app's own "add item" UI in normal single-CO use) has exactly one CO
link, so in practice this never bites - it only matters for a course
that deliberately tags one question to multiple COs, which the Results
page itself (app/engine.py) *does* handle by counting the item toward
every mapped CO. That one edge case is the one place this export is
knowingly narrower than the live Results page.
"""
import copy
import os

from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

from app.models import Mark, ExternalResult, ExitSurveyResponse, COPOMapping

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_assets", "attainment_template.xlsx")

# Component types that feed the "IA Test only" sheet, in display order.
IA_COMPONENT_TYPES = ("IA1", "IA2", "IA3")

YELLOW_FILL = PatternFill(start_color="FFFFFF00", end_color="FFFFFF00", fill_type="solid")


def _style_of(cell):
    """A copyable snapshot of one cell's formatting, to stamp onto newly
    written cells so generated rows/columns look like they belong to the
    original template rather than a bare openpyxl grid."""
    return {
        "font": copy.copy(cell.font),
        "border": copy.copy(cell.border),
        "fill": copy.copy(cell.fill),
        "alignment": copy.copy(cell.alignment),
        "number_format": cell.number_format,
    }


def _apply(cell, style):
    cell.font = style["font"]
    cell.border = style["border"]
    cell.fill = style["fill"]
    cell.alignment = style["alignment"]
    cell.number_format = style["number_format"]


def _bold(cell, size=10, name="Times New Roman"):
    cell.font = Font(name=name, size=size, bold=True)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _plain(cell, size=10, name="Times New Roman"):
    cell.font = Font(name=name, size=size, bold=False)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _unmerge_all(ws):
    """Drop every merged-cell range on a sheet before we write to it.

    The original template merges cells extensively, but every merge
    geometry is specific to its exact 5-CO/3-subquestion layout (e.g.
    "IA Test only"'s title spans A1:AP1, "External"'s spans C1:I1 - no
    two sheets agree, and none of them match our variable-width column
    layout). Since every sheet here is already being *rebuilt* with its
    own column count sized to this course's actual COs/items/students
    (see the module docstring), keeping the original merges would just
    mean writing into read-only non-anchor MergedCell objects. Content
    still reads fine as plain per-cell values with wrap_text + center
    alignment (already applied by _bold/_plain) - only the visual
    cell-spanning is lost, not any information."""
    for rng in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(rng))


def _clear_all_cells(ws):
    """Wipe every cell's value/formula across the sheet's current used
    range (called right after _unmerge_all, before we write anything).

    Without this, the original template's own formulas for its fixed
    5-CO/20-student layout stay sitting beyond whatever narrower area we
    write into (e.g. "IA Test only" runs out to column CC / row 253) -
    referencing cells that are now empty (because our course has a
    different CO/student count) rather than deleted, so they don't just
    go blank, they evaluate to #DIV/0!/#REF! and get flagged by the
    xlsx skill's recalc check. Since every sheet here is a full rebuild,
    not an in-place edit, clearing first is correct - not lossy: nothing
    of the original template's content survives review anyway once we
    write our own column layout on top of it. Row heights, column
    widths, and the embedded logo images (separate drawing objects, not
    cell content) are untouched by this.

    Formatting (fills/borders/fonts left over from the original template)
    is deliberately NOT touched here - an earlier attempt at also
    resetting every cell's style broke the workbook when opened in real
    Excel. Revisit that separately later; for now this only clears
    values."""
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                cell.value = None


def _note(cell, text):
    cell.value = text
    cell.font = Font(name="Times New Roman", size=8, italic=True)
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _q(sheet_name):
    """Quote a sheet name for use in a cross-sheet formula reference -
    required whenever the name contains a space (every sheet here does)."""
    return f"'{sheet_name}'"


def _avg_formula(cell_refs):
    """Average of the given cell refs, skipping any that evaluate to ""
    rather than a number. Our own formulas return "" (via IFERROR) when
    there's no underlying data, and plain AVERAGE()/#VALUE!s on a text
    cell - this is the ISNUMBER-guarded equivalent, safe to nest."""
    nums = "+".join(f"IF(ISNUMBER({c}),{c},0)" for c in cell_refs)
    cnt = "+".join(f"ISNUMBER({c})*1" for c in cell_refs)
    return f'=IFERROR(({nums})/({cnt}),"")'


def build_workbook(course):
    """Returns an openpyxl Workbook for `course`, built from the shipped
    template. Caller is responsible for saving it and (if it contains
    formulas the caller wants pre-calculated - this one always does)
    running it through LibreOffice/Excel at least once, e.g. via the
    xlsx skill's recalc.py, or just opening it normally."""
    wb = load_workbook(TEMPLATE_PATH)

    # Dropped rather than rewritten: its old per-sheet fill-in steps
    # don't apply to a generated workbook, and a from-scratch rewrite is
    # deferred (see git history / conversation) - simplest correct state
    # for now is just not shipping a stale/misleading Instructions tab.
    del wb["Instructions"]

    cos = sorted(course.outcomes, key=lambda co: co.seq)
    pos = sorted(course.program_outcomes, key=lambda po: po.seq)
    students = sorted(course.students, key=lambda s: s.seq)
    components_by_type = {c.type: c for c in course.components}

    student_ids = [s.id for s in students]
    marks_lookup = {
        (m.student_id, m.item_id): m.marks_obtained
        for m in Mark.query.filter(Mark.student_id.in_(student_ids)).all()
    } if student_ids else {}

    _fill_set_target_form(wb["set target form"], course, cos)

    ia_summary, ia_blocks = _fill_marks_grid_sheet(
        wb["IA Test only"], course, cos, students, marks_lookup,
        blocks=[(t, components_by_type[t].items) for t in IA_COMPONENT_TYPES if t in components_by_type and components_by_type[t].items],
        title="INTERNAL - I, II & III marks",
    )
    asqm_summary, _asqm_blocks = _fill_marks_grid_sheet(
        wb["ASQM only"], course, cos, students, marks_lookup,
        blocks=[("ASQM", components_by_type["ASQM"].items)] if "ASQM" in components_by_type and components_by_type["ASQM"].items else [],
        title="Assignment/Quiz/Mini project/Seminar marks",
    )
    external_info = _fill_external_sheet(wb["External"], course, students)
    exit_summary = _fill_exit_survey_sheet(
        wb["Exit survey"], course, cos, students,
        items=components_by_type["EXIT_SURVEY"].items if "EXIT_SURVEY" in components_by_type else [],
    )

    external_ref = external_info["attainment_ref"] if external_info else None
    _fill_co_attainment_sheet(wb["CO attainment"], course, cos, ia_summary, asqm_summary, external_ref, exit_summary)
    _fill_course_po_attainment_sheet(wb["Course PO attainment"], course, cos, pos)
    _fill_result_analysis_sheet(wb["Result Analysis"], course, ia_blocks, external_info)
    _fill_co_attainment_gap_sheet(wb["CO attainment gap"], course, cos)
    _fill_course_po_attainment_gap_sheet(wb["Course PO attainment gap"], course, pos, cos)

    return wb


# ------------------------------------------------------- set target form

def _fill_set_target_form(ws, course, cos):
    # This sheet keeps most of its original static labels/instructions
    # (Level 1/2/3 rows, the form's explanatory text) - only unmerge, so
    # those non-anchor cells become writable if we ever need them, but
    # don't wipe content the way _write_common_header's callers do.
    _unmerge_all(ws)
    ws["E1"] = course.institution_name or "AMC ENGINEERING COLLEGE"
    ws["E2"] = f"DEPARTMENT OF {course.department.upper()}" if course.department else ""
    ws["D4"], ws["E4"] = "Sub code: ", course.subject_code
    ws["F4"], ws["G4"] = "Sub name:", course.subject_name
    ws["D6"] = course.faculty_display
    ws["D7"], ws["E7"] = "Academic Year:", course.academic_year
    ws["F7"], ws["G7"] = "Sem :", course.semester

    # Row 9: one column per CO, starting at E - extend right if this
    # course has more COs than the template's original 5-CO width.
    start_col = 5  # E
    _ensure_columns(ws, start_col, start_col + len(cos) - 1, copy_from_col=start_col)
    for i, co in enumerate(cos):
        ws.cell(row=9, column=start_col + i, value=co.code)

    for row, pct in ((14, course.target_level1_pct), (15, course.target_level2_pct), (16, course.target_level3_pct)):
        ws.cell(row=row, column=5, value=pct)   # E: Internal % of students
        ws.cell(row=row, column=6, value=course.internal_marks_cutoff_pct)  # F: % of marks cutoff
        ws.cell(row=row, column=7, value=pct)   # G: External % of students
        ws.cell(row=row, column=9, value=pct)   # I: Exit survey % of students


def _ensure_columns(ws, min_col, max_col, copy_from_col):
    """No-op helper placeholder: for the courses this app supports (a
    handful of COs/POs), the template's existing column width is always
    enough, so there's nothing to extend. Kept as a named seam rather
    than silently assuming - if a course ever has more COs than fit,
    this is where widening logic would go."""
    return


# -------------------------------------------------------- marks grids
# Shared builder for "IA Test only" (one block per IA1/IA2/IA3 that has
# items) and "ASQM only" (one block). Mirrors the template's own layout:
# a "Cijk" row naming which CO each question belongs to, a "Max Marks"
# row, one column per question, a block Total, then - pooling every
# block together, matching engine.py's IA1+IA2+IA3 pooling - one
# scored/allotted/% column trio per CO, and class-level summary rows
# below the last student using the exact SUMIF/COUNTIF pattern the
# original used.

HEADER_ROW_TITLE = 1
HEADER_ROW_DEPT = 2
HEADER_ROW_SUBTITLE = 3
HEADER_ROW_SUBCODE = 4
HEADER_ROW_FACULTY = 5
ROW_CIJK = 7
ROW_MAXMARKS = 8
ROW_BLOCKLABEL = 9
ROW_COLHEADERS = 10
FIRST_STUDENT_ROW = 11


def _write_common_header(ws, course, title):
    _unmerge_all(ws)
    _clear_all_cells(ws)
    ws.cell(row=HEADER_ROW_TITLE, column=1, value=course.institution_name or "AMC ENGINEERING COLLEGE")
    ws.cell(row=HEADER_ROW_DEPT, column=1, value=f"DEPARTMENT OF {course.department.upper()}" if course.department else "")
    ws.cell(row=HEADER_ROW_SUBTITLE, column=1, value=title)
    ws.cell(row=HEADER_ROW_SUBCODE, column=2, value="Sub code: ")
    ws.cell(row=HEADER_ROW_SUBCODE, column=3, value=course.subject_code)
    ws.cell(row=HEADER_ROW_SUBCODE, column=4, value="Sub name:")
    ws.cell(row=HEADER_ROW_SUBCODE, column=5, value=course.subject_name)
    ws.cell(row=HEADER_ROW_SUBCODE, column=7, value="Academic Year:")
    ws.cell(row=HEADER_ROW_SUBCODE, column=8, value=course.academic_year)
    ws.cell(row=HEADER_ROW_SUBCODE, column=9, value="Sem:")
    ws.cell(row=HEADER_ROW_SUBCODE, column=10, value=course.semester)
    ws.cell(row=HEADER_ROW_FACULTY, column=2, value="Names of Faculty handled:")
    ws.cell(row=HEADER_ROW_FACULTY, column=4, value=course.faculty_display)
    for r in (HEADER_ROW_TITLE, HEADER_ROW_DEPT, HEADER_ROW_SUBTITLE):
        c = ws.cell(row=r, column=1)
        c.font = Font(name="Times New Roman", size=12 if r == HEADER_ROW_TITLE else 10, bold=True)
        c.alignment = Alignment(horizontal="center")
    for coord in ("B4", "D4", "G4", "I4", "B5"):
        ws[coord].font = Font(name="Times New Roman", size=10, bold=True)


def _fill_marks_grid_sheet(ws, course, cos, students, marks_lookup, blocks, title):
    """blocks: list of (label, items) pairs, already filtered to
    non-empty item lists. marks_lookup: dict[(student_id, item_id)] ->
    marks_obtained, built once in build_workbook from the real Mark
    table (mirrors app/engine.py's own lookup).

    Returns (co_summary_cells, block_summary):
      co_summary_cells: {co_id: "SheetName!$COL$ROW"} pointing at each
        CO's final "attainment in %" cell, for the CO attainment sheet.
      block_summary: [{"label", "sheet", "total_col", "first_row",
        "last_row"}, ...] - one entry per block (e.g. per IA1/IA2/IA3),
        for the Result Analysis sheet's per-test pass-rate row.

    If there's nothing to show (no structure set up yet), the sheet is
    left as a labeled, empty grid and both return values are empty.
    """
    _write_common_header(ws, course, title)

    if not blocks or not students:
        ws.cell(row=FIRST_STUDENT_ROW, column=1, value="(No items/students entered for this component yet.)")
        return {}, []

    col = 4  # D: first column after Sl.No/USN/Name
    item_cols = []  # (col, item, co_id) for every question column, across every block
    block_total_cols = []  # (label, total_col, block_start, block_end) - one per block

    ws.cell(row=ROW_CIJK, column=3, value="Cijk")
    ws.cell(row=ROW_MAXMARKS, column=3, value="Max Marks")
    ws.cell(row=FIRST_STUDENT_ROW - 1, column=1, value="Sl.No.")
    ws.cell(row=FIRST_STUDENT_ROW - 1, column=2, value="USN")
    ws.cell(row=FIRST_STUDENT_ROW - 1, column=3, value="Name")
    for coord in ("A10", "B10", "C10", "C7", "C8"):
        _bold(ws[coord])

    co_by_id = {co.id: co for co in cos}
    co_index = {co.id: i + 1 for i, co in enumerate(cos)}  # 1-based, matches "Cijk" convention

    for label, items in blocks:
        block_start = col
        ws.cell(row=ROW_BLOCKLABEL, column=block_start, value=label)
        _bold(ws.cell(row=ROW_BLOCKLABEL, column=block_start))
        for item in items:
            first_co = item.co_links[0].co if item.co_links else None
            ws.cell(row=ROW_CIJK, column=col, value=co_index.get(first_co.id) if first_co else None)
            ws.cell(row=ROW_MAXMARKS, column=col, value=item.max_marks)
            ws.cell(row=ROW_COLHEADERS, column=col, value=item.label)
            _bold(ws.cell(row=ROW_COLHEADERS, column=col))
            _plain(ws.cell(row=ROW_CIJK, column=col))
            _plain(ws.cell(row=ROW_MAXMARKS, column=col))
            item_cols.append((col, item, first_co.id if first_co else None))
            col += 1
        total_col = col
        ws.cell(row=ROW_COLHEADERS, column=total_col, value="Total")
        _bold(ws.cell(row=ROW_COLHEADERS, column=total_col))
        block_total_cols.append((label, total_col, block_start, col - 1))
        col += 1

    cijk_range_start, cijk_range_end = 4, col - 1
    cijk_range = f"${get_column_letter(cijk_range_start)}${ROW_CIJK}:${get_column_letter(cijk_range_end)}${ROW_CIJK}"
    maxmarks_range = f"${get_column_letter(cijk_range_start)}${ROW_MAXMARKS}:${get_column_letter(cijk_range_end)}${ROW_MAXMARKS}"

    # A block-level "Max Marks" total, in the Total column, on the same
    # Max Marks row as the per-item maxima - so Result Analysis can read
    # a student's block Total against this without recomputing anything.
    for label, total_col, b_start, b_end in block_total_cols:
        ws.cell(row=ROW_MAXMARKS, column=total_col,
                value=f"=SUM({get_column_letter(b_start)}{ROW_MAXMARKS}:{get_column_letter(b_end)}{ROW_MAXMARKS})")
        _plain(ws.cell(row=ROW_MAXMARKS, column=total_col))

    # One scored/allotted/% column trio per CO that actually has a
    # mapped item somewhere in these blocks (pooled across IA1/IA2/IA3).
    co_ids_present = []
    for _, _, co_id in item_cols:
        if co_id is not None and co_id not in co_ids_present:
            co_ids_present.append(co_id)
    co_agg_cols = {}  # co_id -> (scored_col, allotted_col, pct_col)
    for co_id in co_ids_present:
        scored_col, allotted_col, pct_col = col, col + 1, col + 2
        co = co_by_id[co_id]
        ws.cell(row=ROW_BLOCKLABEL, column=scored_col, value=co.code)
        _bold(ws.cell(row=ROW_BLOCKLABEL, column=scored_col))
        ws.cell(row=ROW_COLHEADERS, column=scored_col, value="scored")
        ws.cell(row=ROW_COLHEADERS, column=allotted_col, value="allotted")
        ws.cell(row=ROW_COLHEADERS, column=pct_col, value="%")
        for c in (scored_col, allotted_col, pct_col):
            _bold(ws.cell(row=ROW_COLHEADERS, column=c))
        co_agg_cols[co_id] = (scored_col, allotted_col, pct_col)
        col += 3

    # Student rows.
    for i, student in enumerate(students):
        r = FIRST_STUDENT_ROW + i
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=student.usn)
        ws.cell(row=r, column=3, value=student.name)
        for c, item, _co_id in item_cols:
            mark = marks_lookup.get((student.id, item.id))
            ws.cell(row=r, column=c, value=mark)
        for label, total_col, b_start, b_end in block_total_cols:
            ws.cell(row=r, column=total_col, value=f"=SUM({get_column_letter(b_start)}{r}:{get_column_letter(b_end)}{r})")
        row_marks_range = f"${get_column_letter(cijk_range_start)}{r}:${get_column_letter(cijk_range_end)}{r}"
        for co_id, (scored_col, allotted_col, pct_col) in co_agg_cols.items():
            co_idx = co_index[co_id]
            scored_ref = f"{get_column_letter(scored_col)}{r}"
            allotted_ref = f"{get_column_letter(allotted_col)}{r}"
            ws.cell(row=r, column=scored_col, value=f"=SUMIF({cijk_range},{co_idx},{row_marks_range})")
            ws.cell(row=r, column=allotted_col, value=f'=SUMIFS({maxmarks_range},{cijk_range},{co_idx},{row_marks_range},">="&0)')
            ws.cell(row=r, column=pct_col, value=f'=IFERROR({scored_ref}/{allotted_ref}*100,"")')

    last_student_row = FIRST_STUDENT_ROW + len(students) - 1

    # Class-level summary, 2 rows below the last student.
    summary_row_attended = last_student_row + 3
    summary_row_passed = summary_row_attended + 1
    summary_row_pct = summary_row_attended + 2
    ws.cell(row=summary_row_attended, column=3, value="Number of students who attended the CO's questions")
    ws.cell(row=summary_row_passed, column=3, value=f"Number of students scored >= {course.internal_marks_cutoff_pct}% of marks")
    ws.cell(row=summary_row_pct, column=3, value="CO ATTAINMENT IN PERCENTAGE (% of students scored above set % of marks)")
    for r in (summary_row_attended, summary_row_passed, summary_row_pct):
        _bold(ws.cell(row=r, column=3))

    co_summary_cells = {}
    for co_id, (scored_col, allotted_col, pct_col) in co_agg_cols.items():
        letter = get_column_letter(pct_col)
        pct_range = f"{letter}{FIRST_STUDENT_ROW}:{letter}{last_student_row}"
        ws.cell(row=summary_row_attended, column=pct_col, value=f"=COUNT({pct_range})")
        ws.cell(row=summary_row_passed, column=pct_col, value=f'=COUNTIF({pct_range},">="&{_q("set target form")}!$F$14)')
        attended_ref = f"{letter}{summary_row_attended}"
        passed_ref = f"{letter}{summary_row_passed}"
        ws.cell(row=summary_row_pct, column=pct_col, value=f'=IFERROR({passed_ref}/{attended_ref}*100,"")')
        co_summary_cells[co_id] = f"{_q(ws.title)}!{letter}{summary_row_pct}"

    block_summary = [
        {"label": label, "sheet": ws.title, "total_col": total_col,
         "first_row": FIRST_STUDENT_ROW, "last_row": last_student_row}
        for label, total_col, _b_start, _b_end in block_total_cols
    ]
    return co_summary_cells, block_summary


# -------------------------------------------------------------- external

def _fill_external_sheet(ws, course, students):
    """Sl.No/USN/Name/Internal/External/Total/Result per student, then a
    class-level summary using the same "% above class average" rule as
    engine.external_co_attainment (applied equally to every CO there, so
    this sheet returns a single attainment-% cell reference, not one per
    CO). Returns None (nothing to reference) if the roster is empty."""
    _write_common_header(ws, course, "External (Semester End Exam) - Marks and Result")

    headers = ["Sl.No.", "USN", "Name", "Internal Total (50)", "External Marks (100)", "Total", "Result"]
    for i, h in enumerate(headers, start=1):
        _bold(ws.cell(row=ROW_COLHEADERS, column=i, value=h))

    if not students:
        ws.cell(row=FIRST_STUDENT_ROW, column=1, value="(No students in the roster yet.)")
        return None

    results_by_student = {
        r.student_id: r
        for r in ExternalResult.query.filter(ExternalResult.student_id.in_([s.id for s in students])).all()
    }

    for i, student in enumerate(students):
        r = FIRST_STUDENT_ROW + i
        result = results_by_student.get(student.id)
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=student.usn)
        ws.cell(row=r, column=3, value=student.name)
        ws.cell(row=r, column=4, value=result.internal_total if result else None)
        ws.cell(row=r, column=5, value=result.external_marks if result else None)
        ws.cell(row=r, column=6, value=f'=IFERROR(D{r}+E{r},"")')
        ws.cell(row=r, column=7, value=result.result if result else None)
        for c in range(1, 8):
            _plain(ws.cell(row=r, column=c))

    last_row = FIRST_STUDENT_ROW + len(students) - 1
    ext_range = f"E{FIRST_STUDENT_ROW}:E{last_row}"
    result_range = f"G{FIRST_STUDENT_ROW}:G{last_row}"

    row_appeared = last_row + 3
    row_avg = row_appeared + 1
    row_above = row_appeared + 2
    row_pct = row_appeared + 3

    ws.cell(row=row_appeared, column=3, value="Number of students who appeared (P or F)")
    ws.cell(row=row_avg, column=3, value="Class average (external marks, appeared students)")
    ws.cell(row=row_above, column=3, value="Number of students scoring >= class average")
    ws.cell(row=row_pct, column=3, value="EXTERNAL ATTAINMENT IN PERCENTAGE")
    for r in (row_appeared, row_avg, row_above, row_pct):
        _bold(ws.cell(row=r, column=3))

    appeared_formula = f'(COUNTIF({result_range},"P")+COUNTIF({result_range},"F"))'
    sum_formula = f'(SUMIF({result_range},"P",{ext_range})+SUMIF({result_range},"F",{ext_range}))'
    ws.cell(row=row_appeared, column=5, value=f'={appeared_formula}')
    ws.cell(row=row_avg, column=5, value=f'=IFERROR({sum_formula}/{appeared_formula},"")')
    avg_ref = f"E{row_avg}"
    ws.cell(row=row_above, column=5,
            value=(f'=COUNTIFS({result_range},"P",{ext_range},">="&{avg_ref})'
                   f'+COUNTIFS({result_range},"F",{ext_range},">="&{avg_ref})'))
    ws.cell(row=row_pct, column=5, value=f'=IFERROR(E{row_above}/E{row_appeared}*100,"")')

    return {
        "sheet": ws.title,
        "result_range_local": result_range,
        "first_row": FIRST_STUDENT_ROW,
        "last_row": last_row,
        "attainment_ref": f"{_q(ws.title)}!$E${row_pct}",
    }


# ----------------------------------------------------------- exit survey

def _fill_exit_survey_sheet(ws, course, cos, students, items):
    """Sl.No/USN/Name/one rating column per survey question, a per-
    question "% rated A or B" row, and a per-CO average of that % across
    the questions mapped to it (engine.exit_survey_co_attainment).
    Returns {co_id: "SheetName!$COL$ROW"}."""
    _write_common_header(ws, course, "Course Exit Survey")

    ws.cell(row=FIRST_STUDENT_ROW - 1, column=1, value="Sl.No.")
    ws.cell(row=FIRST_STUDENT_ROW - 1, column=2, value="USN")
    ws.cell(row=FIRST_STUDENT_ROW - 1, column=3, value="Name")
    for coord in ("A10", "B10", "C10"):
        _bold(ws[coord])

    if not items or not students:
        ws.cell(row=FIRST_STUDENT_ROW, column=1, value="(No exit-survey questions/students entered yet.)")
        return {}

    responses = ExitSurveyResponse.query.filter(ExitSurveyResponse.student_id.in_([s.id for s in students])).all()
    response_lookup = {(r.student_id, r.item_id): r.rating for r in responses}

    item_col = {}
    col = 4
    for item in items:
        ws.cell(row=ROW_COLHEADERS, column=col, value=item.label)
        _bold(ws.cell(row=ROW_COLHEADERS, column=col))
        item_col[item.id] = col
        col += 1

    for i, student in enumerate(students):
        r = FIRST_STUDENT_ROW + i
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=student.usn)
        ws.cell(row=r, column=3, value=student.name)
        for item in items:
            ws.cell(row=r, column=item_col[item.id], value=response_lookup.get((student.id, item.id)))

    last_row = FIRST_STUDENT_ROW + len(students) - 1
    row_pct_good = last_row + 2
    ws.cell(row=row_pct_good, column=3, value="% of respondents rating A (Excellent) or B (Good)")
    _bold(ws.cell(row=row_pct_good, column=3))

    item_pct_cell = {}
    for item in items:
        c = item_col[item.id]
        letter = get_column_letter(c)
        rng = f"{letter}{FIRST_STUDENT_ROW}:{letter}{last_row}"
        ws.cell(row=row_pct_good, column=c,
                value=f'=IFERROR((COUNTIF({rng},"A")+COUNTIF({rng},"B"))/COUNTA({rng})*100,"")')
        _plain(ws.cell(row=row_pct_good, column=c))
        item_pct_cell[item.id] = f"{letter}{row_pct_good}"

    row_co_label = row_pct_good + 2
    row_co_value = row_pct_good + 3
    ws.cell(row=row_co_label, column=3, value="Per-CO Exit Survey attainment (average of mapped questions)")
    _bold(ws.cell(row=row_co_label, column=3))

    co_summary_cells = {}
    start_col = 4
    for i, co in enumerate(cos):
        c = start_col + i
        ws.cell(row=row_co_label, column=c, value=co.code)
        _bold(ws.cell(row=row_co_label, column=c))
        mapped_items = [item for item in items if any(link.co_id == co.id for link in item.co_links)]
        if mapped_items:
            refs = [item_pct_cell[item.id] for item in mapped_items]
            formula = _avg_formula(refs)
        else:
            formula = '=""'
        ws.cell(row=row_co_value, column=c, value=formula)
        _plain(ws.cell(row=row_co_value, column=c))
        co_summary_cells[co.id] = f"{_q(ws.title)}!{get_column_letter(c)}{row_co_value}"

    return co_summary_cells


# ------------------------------------------------------- CO attainment

CO_ATTAINMENT_HEADER_ROW = 9
CO_ATTAINMENT_FIRST_ROW = 10


def _fill_co_attainment_sheet(ws, course, cos, ia_summary, asqm_summary, external_ref, exit_summary):
    """One row per CO, blending IA+ASQM (CIA), External, and Exit Survey
    exactly as app/engine.py's compute_course_attainment does: CIA =
    avg(IA%,ASQM%); blended% = 0.9*(0.5*CIA + 0.5*External) + 0.1*Exit;
    Level = 0/1/2/3 ladder against 'set target form'!E14:E16."""
    _write_common_header(ws, course, "CO Attainment")

    headers = ["CO", "IA %", "ASQM %", "CIA % (avg)", "External %", "Exit Survey %",
               "Internal contrib. (0.5x CIA)", "External contrib. (0.5x Ext)", "Int+Ext",
               "0.9 x (Int+Ext)", "0.1 x Exit", "Blended Attainment %", "Level (0-3)"]
    for i, h in enumerate(headers, start=1):
        _bold(ws.cell(row=CO_ATTAINMENT_HEADER_ROW, column=i, value=h))

    for i, co in enumerate(cos):
        r = CO_ATTAINMENT_FIRST_ROW + i
        ws.cell(row=r, column=1, value=co.code)
        _bold(ws.cell(row=r, column=1))

        ia_ref = ia_summary.get(co.id)
        asqm_ref = asqm_summary.get(co.id)
        exit_ref = exit_summary.get(co.id)

        ws.cell(row=r, column=2, value=f'=IFERROR({ia_ref},"")' if ia_ref else '=""')
        ws.cell(row=r, column=3, value=f'=IFERROR({asqm_ref},"")' if asqm_ref else '=""')
        ws.cell(row=r, column=4, value=_avg_formula([f"B{r}", f"C{r}"]))
        ws.cell(row=r, column=5, value=f'=IFERROR({external_ref},"")' if external_ref else '=""')
        ws.cell(row=r, column=6, value=f'=IFERROR({exit_ref},"")' if exit_ref else '=""')
        ws.cell(row=r, column=7, value=f'=0.5*IF(ISNUMBER(D{r}),D{r},0)')
        ws.cell(row=r, column=8, value=f'=0.5*IF(ISNUMBER(E{r}),E{r},0)')
        ws.cell(row=r, column=9, value=f'=G{r}+H{r}')
        ws.cell(row=r, column=10, value=f'=0.9*I{r}')
        ws.cell(row=r, column=11, value=f'=0.1*IF(ISNUMBER(F{r}),F{r},0)')
        ws.cell(row=r, column=12,
                value=f'=IF(AND(NOT(ISNUMBER(D{r})),NOT(ISNUMBER(E{r})),NOT(ISNUMBER(F{r}))),"",J{r}+K{r})')
        ws.cell(row=r, column=13,
                value=(f'=IF(L{r}="","",IF(L{r}<{_q("set target form")}!$E$14,0,'
                       f'IF(L{r}<{_q("set target form")}!$E$15,1,'
                       f'IF(L{r}<{_q("set target form")}!$E$16,2,3))))'))
        for c in range(2, 14):
            _plain(ws.cell(row=r, column=c))


# ------------------------------------------------- Course PO attainment

PO_MATRIX_HEADER_ROW = 9
PO_MATRIX_FIRST_ROW = 10


def _fill_course_po_attainment_sheet(ws, course, cos, pos):
    """CO-PO/PSO correlation matrix (COPOMapping.correlation_level, 1-3,
    blank if unmapped), plus a correlation-weighted average of each PO's
    mapped COs' Level (from the 'CO attainment' sheet), matching
    engine.compute_course_attainment's PO loop: weighted_sum = sum(level
    * correlation) / sum(correlation), over COs with a non-blank Level."""
    _write_common_header(ws, course, "Course PO / PSO Attainment (CO-PO Correlation Matrix)")

    mapping_lookup = {}
    if cos:
        co_ids = [co.id for co in cos]
        for m in COPOMapping.query.filter(COPOMapping.co_id.in_(co_ids)).all():
            mapping_lookup[(m.co_id, m.po_id)] = m.correlation_level

    ws.cell(row=PO_MATRIX_HEADER_ROW, column=1, value="CO \\ PO")
    _bold(ws.cell(row=PO_MATRIX_HEADER_ROW, column=1))
    po_col = {}
    for i, po in enumerate(pos):
        c = 2 + i
        ws.cell(row=PO_MATRIX_HEADER_ROW, column=c, value=po.code)
        _bold(ws.cell(row=PO_MATRIX_HEADER_ROW, column=c))
        po_col[po.id] = c

    for i, co in enumerate(cos):
        r = PO_MATRIX_FIRST_ROW + i
        ws.cell(row=r, column=1, value=co.code)
        _bold(ws.cell(row=r, column=1))
        for po in pos:
            level = mapping_lookup.get((co.id, po.id))
            _plain(ws.cell(row=r, column=po_col[po.id], value=level))

    if not cos:
        return

    matrix_last_row = PO_MATRIX_FIRST_ROW + len(cos) - 1
    row_attainment = matrix_last_row + 3
    ws.cell(row=row_attainment, column=1, value="Attainment (correlation-weighted avg of CO Level)")
    _bold(ws.cell(row=row_attainment, column=1))

    # Positional alignment, not by matching absolute row numbers: this
    # matrix's CO rows and the 'CO attainment' sheet's CO rows are both
    # built from the same `cos` list, in the same order, so SUMPRODUCT
    # over same-length ranges lines each CO up correctly even though the
    # two sheets place them at different row numbers.
    level_range = f"{_q('CO attainment')}!$M${CO_ATTAINMENT_FIRST_ROW}:$M${CO_ATTAINMENT_FIRST_ROW + len(cos) - 1}"

    for po in pos:
        c = po_col[po.id]
        letter = get_column_letter(c)
        matrix_range = f"{letter}{PO_MATRIX_FIRST_ROW}:{letter}{matrix_last_row}"
        weighted = f'SUMPRODUCT({matrix_range},IF(ISNUMBER({level_range}),{level_range},0))'
        weight = f'SUMPRODUCT({matrix_range},ISNUMBER({level_range})*1)'
        ws.cell(row=row_attainment, column=c, value=f'=IFERROR({weighted}/{weight},"")')
        _plain(ws.cell(row=row_attainment, column=c))


# ---------------------------------------------------------- gap sheets

def _fill_co_attainment_gap_sheet(ws, course, cos):
    """Current-year CO attainment vs a target Level, per CO. The
    original template's version of this sheet compared this year against
    two prior years (CAYm1/CAYm2) via formulas that were already broken
    (#REF!) in the file we were given, and this app only tracks one
    course offering at a time - so this is a current-vs-target gap
    instead, with an "Action Proposed" column left for the user."""
    _write_common_header(ws, course, "CO Attainment Gap Analysis (current offering vs target)")

    headers = ["CO", "Target Level (0-3)", "Actual Level (0-3)", "Actual Attainment %", "Gap (Target - Actual)", "Action Proposed"]
    for i, h in enumerate(headers, start=1):
        _bold(ws.cell(row=CO_ATTAINMENT_HEADER_ROW, column=i, value=h))

    _note(ws.cell(row=CO_ATTAINMENT_HEADER_ROW - 1, column=1),
          "Target Level defaults to 2 (a common NBA convention) - edit the yellow cells below to set a different target per CO.")

    for i, co in enumerate(cos):
        r = CO_ATTAINMENT_FIRST_ROW + i
        ws.cell(row=r, column=1, value=co.code)
        _bold(ws.cell(row=r, column=1))
        target_cell = ws.cell(row=r, column=2, value=2)
        target_cell.fill = YELLOW_FILL
        _plain(target_cell)
        level_ref = f"{_q('CO attainment')}!$M${CO_ATTAINMENT_FIRST_ROW + i}"
        pct_ref = f"{_q('CO attainment')}!$L${CO_ATTAINMENT_FIRST_ROW + i}"
        ws.cell(row=r, column=3, value=f'=IFERROR({level_ref},"")')
        ws.cell(row=r, column=4, value=f'=IFERROR({pct_ref},"")')
        ws.cell(row=r, column=5, value=f'=IF(ISNUMBER(C{r}),B{r}-C{r},"")')
        for c in (3, 4, 5):
            _plain(ws.cell(row=r, column=c))


def _fill_course_po_attainment_gap_sheet(ws, course, pos, cos):
    """Same idea as the CO gap sheet, for PO/PSO attainment: current
    correlation-weighted attainment (already on a 0-3 Level-equivalent
    scale, same as the CO gap sheet's Target) vs a default target of 2."""
    _write_common_header(ws, course, "Course PO/PSO Attainment Gap Analysis")

    headers = ["PO/PSO", "Target Level (0-3)", "Actual Attainment (Level-equivalent)", "Gap (Target - Actual)", "Action Proposed"]
    for i, h in enumerate(headers, start=1):
        _bold(ws.cell(row=PO_MATRIX_HEADER_ROW, column=i, value=h))

    _note(ws.cell(row=PO_MATRIX_HEADER_ROW - 1, column=1),
          "Target Level defaults to 2 (a common NBA convention) - edit the yellow cells below to set a different target per PO/PSO.")

    if not cos:
        return
    matrix_last_row = PO_MATRIX_FIRST_ROW + len(cos) - 1
    row_attainment = matrix_last_row + 3  # must match _fill_course_po_attainment_sheet's layout

    for i, po in enumerate(pos):
        r = PO_MATRIX_FIRST_ROW + i
        ws.cell(row=r, column=1, value=po.code)
        _bold(ws.cell(row=r, column=1))
        target_cell = ws.cell(row=r, column=2, value=2)
        target_cell.fill = YELLOW_FILL
        _plain(target_cell)
        po_matrix_col = get_column_letter(2 + i)  # must match po_col in _fill_course_po_attainment_sheet
        actual_ref = f"{_q('Course PO attainment')}!${po_matrix_col}${row_attainment}"
        ws.cell(row=r, column=3, value=f'=IFERROR({actual_ref},"")')
        ws.cell(row=r, column=4, value=f'=IF(ISNUMBER(C{r}),B{r}-C{r},"")')
        for c in (3, 4):
            _plain(ws.cell(row=r, column=c))


# ------------------------------------------------------- result analysis

def _fill_result_analysis_sheet(ws, course, ia_blocks, external_info):
    """Course-level pass/fail summary per assessment (IA1/IA2/IA3 and the
    external exam) - a simplified, current-offering-only version of the
    original's Result Analysis sheet (which compared multiple years).
    Pass = a student's block Total, as a % of that block's Max Marks
    total, at or above the course's internal marks cutoff - both read
    live from the 'IA Test only' sheet, not recomputed here."""
    _write_common_header(ws, course, "Result Analysis (Pass/Fail Summary)")

    headers = ["Assessment", "Students Counted", "Students Passed (>= cutoff)", "Pass %"]
    for i, h in enumerate(headers, start=1):
        _bold(ws.cell(row=CO_ATTAINMENT_HEADER_ROW, column=i, value=h))

    cutoff_ref = f"{_q('set target form')}!$F$14"
    row = CO_ATTAINMENT_FIRST_ROW

    for block in ia_blocks:
        sheet_q = _q(block["sheet"])
        col_letter = get_column_letter(block["total_col"])
        total_range = f"{sheet_q}!${col_letter}${block['first_row']}:${col_letter}${block['last_row']}"
        max_ref = f"{sheet_q}!${col_letter}${ROW_MAXMARKS}"
        ws.cell(row=row, column=1, value=block["label"])
        ws.cell(row=row, column=2, value=f'=COUNT({total_range})')
        ws.cell(row=row, column=3,
                value=f'=SUMPRODUCT((({total_range})/{max_ref}*100>={cutoff_ref})*1)')
        ws.cell(row=row, column=4, value=f'=IFERROR(C{row}/B{row}*100,"")')
        for c in range(1, 5):
            _plain(ws.cell(row=row, column=c))
        row += 1

    if external_info:
        result_range = f"{_q(external_info['sheet'])}!${external_info['result_range_local'].replace(':', ':$')}"
        ws.cell(row=row, column=1, value="External (SEE)")
        ws.cell(row=row, column=2, value=f'=COUNTIF({result_range},"P")+COUNTIF({result_range},"F")')
        ws.cell(row=row, column=3, value=f'=COUNTIF({result_range},"P")')
        ws.cell(row=row, column=4, value=f'=IFERROR(C{row}/B{row}*100,"")')
        for c in range(1, 5):
            _plain(ws.cell(row=row, column=c))
        row += 1

    if not ia_blocks and not external_info:
        ws.cell(row=row, column=1, value="(No assessment data entered for this course yet.)")
