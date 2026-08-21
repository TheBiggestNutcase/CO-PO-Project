"""
Unit tests for the roster .docx parser, against a real (messy) sample
roster: 4 sections (A-D), where section B's list is split across two
tables with no repeated header row, and section D's second table has an
extra duplicate-USN column and a name with its spacing stripped out
("THAKURSUPRIYAMUKESH"). These aren't synthetic edge cases - they're
exactly what showed up in a real "sections-wise students list" document,
which is why the parser is built to flag rather than choke on them.
"""
import os

from app.roster_import import parse_roster_docx

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _parse():
    with open(os.path.join(FIXTURES, "sample_roster.docx"), "rb") as f:
        return parse_roster_docx(f)


def test_finds_all_four_sections_with_header_details():
    parsed = _parse()
    labels = [s.label for s in parsed.sections]
    assert labels == ["A", "B", "C", "D"]
    for section in parsed.sections:
        assert section.department == "Computer Science and Engineering"
        assert section.academic_year == "2024-25"
        assert section.semester == "5th"


def test_section_a_and_c_extract_cleanly_matching_declared_total():
    parsed = _parse()
    section_a = parsed.sections[0]
    assert section_a.declared_total == 66
    assert len(section_a.students) == 66
    assert not section_a.warnings
    usns = [s.usn for s in section_a.students]
    assert "1AM23CS040" in usns

    section_c = parsed.sections[2]
    assert section_c.declared_total == 71
    assert len(section_c.students) == 71
    assert not section_c.warnings


def test_section_b_spans_two_tables_without_losing_students():
    """Section B's list is split across two docx tables with no repeated
    header row on the second - both halves must be combined into one
    section, not treated as two."""
    parsed = _parse()
    section_b = parsed.sections[1]
    assert section_b.declared_total == 69
    assert len(section_b.students) == 69
    assert not section_b.warnings
    usns = [s.usn for s in section_b.students]
    assert "1AM23CS064" in usns  # from the first table
    assert "1AM23CS101" in usns  # from the second table (no header row)


def test_section_d_flags_malformed_rows_instead_of_silently_mangling_them():
    """Section D's second table has a duplicate-USN column and at least
    one name with its spacing stripped - both should come through with a
    per-row warning rather than a clean-looking but wrong result, and the
    declared-vs-extracted count mismatch should be flagged at the section
    level too."""
    parsed = _parse()
    section_d = parsed.sections[3]
    assert section_d.declared_total == 69
    # This section's raw data doesn't actually add up to the declared
    # total (a real quirk of the source file) - the mismatch must be
    # reported, not silently swallowed.
    assert section_d.warnings
    assert any("69" in w and str(len(section_d.students)) in w for w in section_d.warnings)

    flagged = [s for s in section_d.students if s.warning]
    assert flagged, "expected at least one row from the malformed table to carry a warning"
    # 1AM23CS227 genuinely appears twice in the source document: once
    # cleanly at the end of one table, and again (malformed, spacing
    # stripped) at the start of the next - a real duplicate-row quirk in
    # the source file, not something the parser should silently collapse.
    mangled = next(s for s in section_d.students if s.usn == "1AM23CS227" and s.warning)
    assert mangled.name == "THAKURSUPRIYAMUKESH"


def test_missing_heading_pattern_produces_a_warning_not_a_crash():
    """A .docx with no recognizable 'Semester ... Section ...: Total: N'
    heading shouldn't blow up - it should come back with no sections and
    a warning, so the review screen can still be used for manual entry."""
    import io

    import docx

    document = docx.Document()
    document.add_paragraph("Just an unrelated document with no section headings.")
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    parsed = parse_roster_docx(buf)

    assert parsed.sections == []
    assert any("section headings" in w for w in parsed.warnings)
