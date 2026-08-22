"""
Unit tests for the syllabus PDF parser against two real syllabus samples
with genuinely different formatting - the parser was hand-tuned against
four samples total (see tests/fixtures/), these two exercise the format
differences between them:

  sample_syllabus_che.pdf   "CO1: ..." (no space, colon), dual course code
                            with a slash (21CHE12/22), heading
                            "Course outcome (Course Skill Set)"
  sample_syllabus_cs33.pdf  "CO 1. ..." (space, period), single course
                            code, heading "Course outcome (Course Skill Set)"
                            with a two-line wrapped CO description

A third real-world variant seen only in manual testing (heading exactly
"Course Outcomes", no parenthetical) is covered by the CO_HEADING_RE regex
itself rather than a fixture, since it's a one-word difference.
"""
import os

from app.syllabus_import import parse_syllabus_pdf, ParsedSyllabus, _extract_header, _extract_outcomes

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parses_chemistry_syllabus():
    path = os.path.join(FIXTURES, "sample_syllabus_che.pdf")
    with open(path, "rb") as f:
        parsed = parse_syllabus_pdf(f)

    assert parsed.subject_code == "21CHE12/22"
    assert parsed.subject_name == "ENGINEERING CHEMISTRY"
    assert parsed.semester == "1"  # "I Semester" in the PDF, converted to a plain number

    assert [o.code for o in parsed.outcomes] == ["CO1", "CO2", "CO3", "CO4", "CO5"]
    assert "electrochemical energy systems" in parsed.outcomes[0].description
    # CO2's description wraps across two lines in the PDF - both halves
    # must be joined into one description, not truncated at the line break.
    assert "electroplating and electroless plating" in parsed.outcomes[1].description
    assert not parsed.warnings


def test_parses_cs_syllabus_with_different_co_line_style():
    path = os.path.join(FIXTURES, "sample_syllabus_cs33.pdf")
    with open(path, "rb") as f:
        parsed = parse_syllabus_pdf(f)

    assert parsed.subject_code == "21CS33"
    assert parsed.subject_name == "ANALOG AND DIGITAL ELECTRONICS"
    assert parsed.semester == "3"  # "III Semester" in the PDF, converted to a plain number

    assert [o.code for o in parsed.outcomes] == ["CO1", "CO2", "CO3", "CO4", "CO5"]
    # This CO's description wraps across two lines in the PDF.
    assert "regulator IC and op-amp" in parsed.outcomes[0].description
    assert not parsed.warnings


def test_missing_sections_produce_warnings_instead_of_crashing():
    """Text with no recognizable header table or CO section shouldn't blow
    up - it should come back with warnings and empty fields, so the review
    screen can still be used for fully-manual entry. Exercises the two
    extraction steps directly (no real PDF needed) since what's under test
    is the text-parsing logic, not pdfplumber itself."""
    lines = ["This is just a random unrelated document with no syllabus structure.", "Nothing else here."]

    result = ParsedSyllabus()
    _extract_header(lines, result)
    _extract_outcomes(lines, result)

    assert result.subject_code is None
    assert result.subject_name is None
    assert result.outcomes == []
    assert len(result.warnings) == 2  # no Course Code row, no Course Outcomes section
