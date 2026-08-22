"""
Unit tests for app/vtu_scraper/parser.py - the one part of the VTU
scraping pipeline that's testable without a live VTU connection, a real
browser, or a captcha to solve. The fixture HTML below hand-builds the
same div-based "table" structure VTU's results page uses (per
data_processor.py in the source repo this was adapted from - see
app/vtu_scraper/parser.py's docstring): a "Semester : N" header div
followed by a sibling div of div.divTableRow / div.divTableCell rows,
first row being the column headers.

This does NOT prove the parser matches VTU's *actual current* markup
(nobody here has network access to the live site) - it proves the
name-driven column matching and multi-semester handling work correctly
against a page shaped the way VTU's is documented to be shaped.
"""
from bs4 import BeautifulSoup

from app.vtu_scraper.parser import SubjectNotFound, get_subject_row, parse_result_page


def _divtable(semester, header_row, *data_rows):
    def row_html(cells):
        cells_html = "".join(f'<div class="divTableCell">{c}</div>' for c in cells)
        return f'<div class="divTableRow">{cells_html}</div>'

    rows_html = row_html(header_row) + "".join(row_html(r) for r in data_rows)
    return (
        f'<div style="text-align:center;padding:5px;">Semester : {semester}</div>'
        f'<div class="divTable">{rows_html}</div>'
    )


ONE_SEMESTER_HEADER = ["Sl No", "Subject Code", "Subject Name", "Internal Marks", "External Marks", "Total", "Result", ""]


def _page(*semester_blocks):
    return BeautifulSoup(f"<html><body>{''.join(semester_blocks)}</body></html>", "lxml")


def test_parses_a_single_semester_with_two_subjects():
    page = _page(_divtable(
        3, ONE_SEMESTER_HEADER,
        ["1", "21PHY22", "Engineering Physics", "45", "62", "107", "P", ""],
        ["2", "21CS33", "Analog and Digital Electronics", "38", "55", "93", "P", ""],
    ))

    rows = parse_result_page(page)

    assert len(rows) == 2
    phy = rows[0]
    assert phy.semester == "3"
    assert phy.subject_code == "21PHY22"
    assert phy.subject_name == "Engineering Physics"
    assert phy.internal_marks == 45.0
    assert phy.external_marks == 62.0
    assert phy.total == 107.0
    assert phy.result == "P"


def test_parses_multiple_semesters_on_one_page():
    """VTU returns a student's entire result history in one lookup - the
    parser needs to walk every "Semester : N" block on the page, not
    just the first."""
    page = _page(
        _divtable(1, ONE_SEMESTER_HEADER, ["1", "21MAT11", "Mathematics I", "40", "50", "90", "P", ""]),
        _divtable(2, ONE_SEMESTER_HEADER, ["1", "21MAT21", "Mathematics II", "42", "48", "90", "P", ""]),
    )

    rows = parse_result_page(page)

    assert [r.semester for r in rows] == ["1", "2"]
    assert [r.subject_code for r in rows] == ["21MAT11", "21MAT21"]


def test_handles_dash_and_blank_marks_as_not_applicable():
    """A subject a student hasn't appeared for (or that has no numeric
    entry yet) shows '-' rather than a number - that must come back as
    None, not a crash or a silently wrong 0."""
    page = _page(_divtable(
        4, ONE_SEMESTER_HEADER,
        ["1", "21EC44", "Not Yet Appeared", "-", "-", "-", "-", ""],
    ))

    rows = parse_result_page(page)

    row = rows[0]
    assert row.internal_marks is None
    assert row.external_marks is None
    assert row.total is None
    assert row.result is None


def test_column_order_does_not_matter_only_header_labels_do():
    """The parser is driven by matching header *text*, not position - a
    page with External/Internal swapped (or extra columns VTU adds
    later) should still land values in the right field."""
    reordered_header = ["Subject Code", "Subject Name", "External Marks", "Internal Marks", "Result", "Total"]
    page = _page(_divtable(
        5, reordered_header,
        ["21CS55", "Some Subject", "60", "40", "P", "100"],
    ))

    rows = parse_result_page(page)

    row = rows[0]
    assert row.internal_marks == 40.0
    assert row.external_marks == 60.0
    assert row.total == 100.0
    assert row.result == "P"


def test_unrecognized_block_without_subject_code_column_is_skipped_not_crashed():
    page = _page(
        '<div style="text-align:center;padding:5px;">Some Notice</div>'
        '<div class="divTable"><div class="divTableRow">'
        '<div class="divTableCell">Just a message, not a marks table</div>'
        "</div></div>"
    )

    rows = parse_result_page(page)

    assert rows == []


def test_get_subject_row_finds_exact_code_case_insensitively():
    page = _page(_divtable(
        3, ONE_SEMESTER_HEADER,
        ["1", "21phy22", "Engineering Physics", "45", "62", "107", "P", ""],
    ))
    rows = parse_result_page(page)

    row = get_subject_row(rows, "21PHY22")
    assert row.subject_name == "Engineering Physics"


def test_get_subject_row_prefers_the_latest_semester_on_a_repeat():
    """An arrear re-attempt shows the same subject code in a later
    semester's block too - the most recent attempt should win."""
    page = _page(
        _divtable(3, ONE_SEMESTER_HEADER, ["1", "21PHY22", "Engineering Physics", "20", "20", "40", "F", ""]),
        _divtable(4, ONE_SEMESTER_HEADER, ["1", "21PHY22", "Engineering Physics", "45", "62", "107", "P", ""]),
    )
    rows = parse_result_page(page)

    row = get_subject_row(rows, "21PHY22")
    assert row.semester == "4"
    assert row.result == "P"


def test_get_subject_row_raises_with_the_codes_it_did_see():
    page = _page(_divtable(
        3, ONE_SEMESTER_HEADER,
        ["1", "21PHY22", "Engineering Physics", "45", "62", "107", "P", ""],
    ))
    rows = parse_result_page(page)

    try:
        get_subject_row(rows, "21CS33")
        assert False, "expected SubjectNotFound"
    except SubjectNotFound as e:
        assert e.seen_codes == ["21PHY22"]


def test_extra_columns_beyond_the_known_fields_are_preserved():
    reval_header = ["Subject Code", "Subject Name", "Internal Marks", "External Marks", "Total", "Result", "Old Result", "Final Marks", "Final Result"]
    page = _page(_divtable(
        4, reval_header,
        ["21PHY22", "Engineering Physics", "45", "38", "83", "F", "F", "60", "P"],
    ))

    rows = parse_result_page(page)
    row = rows[0]
    assert row.extra["Old Result"] == "F"
    assert row.extra["Final Marks"] == "60"
    assert row.extra["Final Result"] == "P"
