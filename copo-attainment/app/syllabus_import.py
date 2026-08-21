"""
Best-effort extraction of subject details and Course Outcomes from a
syllabus PDF, so a new course can be pre-filled instead of typed from
scratch.

This is deliberately *not* trying to be a general PDF-to-database import:
syllabus documents aren't a fixed format, colleges format them differently,
and even within one format the exact wording varies year to year. What it
targets is the common VTU-style layout seen in practice:

    <Semester>
                        <SUBJECT NAME IN CAPS>
    Course Code      <code>      CIE Marks   50
    ...
    Course outcome (Course Skill Set)   [or: "Course Outcomes"]
    At the end of the course the student will be able to:
    CO1: <description that may wrap onto the next line(s)>
    CO 2. <description...>
    ...
    Assessment Details (both CIE and SEE)   <- section ends here

Nothing found by this module is saved anywhere on its own - the route that
calls it always routes through a human-editable review screen first
(see app/routes/import_routes.py), because the parsing here is heuristic
and will occasionally miss or misread a line on a syllabus laid out
differently than the ones this was built against.
"""
import re
from dataclasses import dataclass, field

# "I Semester" / "III Semester" / ... - Roman numeral, so it's just I-VIII.
SEMESTER_RE = re.compile(r"^(VIII|VII|VI|V|IV|III|II|I)\s+Semester\s*$", re.IGNORECASE)

# "Course Code   21CS33   CIE Marks   50" - single-spaced once pdfplumber
# flattens the table's columns onto one line; the "CIE Marks" anchor is
# what keeps this from matching some unrelated "Course Code" mention.
COURSE_CODE_ROW_RE = re.compile(r"Course Code\s+(\S+)\s+CIE Marks\s+(\S+)", re.IGNORECASE)

# A line that looks like a stray "revised on" date stamp some syllabus
# PDFs print above the semester line (e.g. "11.12.2021").
DATE_LINE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")

# Section heading - seen as both "Course outcome (Course Skill Set)" and
# plain "Course Outcomes".
CO_HEADING_RE = re.compile(r"^course outcomes?\b", re.IGNORECASE)

# A CO line - handles both "CO1: ..." and "CO 1. ..." (and "CO-1:" etc.)
CO_LINE_RE = re.compile(r"^CO\s*-?\s*(\d+)\s*[.:]\s*(.*)$", re.IGNORECASE)

# Headings that mark the end of the CO list, wherever they appear next.
STOP_HEADINGS = (
    "assessment details", "suggested learning", "textbook", "web links",
    "activity based learning", "question paper", "reference book", "books",
)


@dataclass
class ParsedOutcome:
    code: str
    description: str


@dataclass
class ParsedSyllabus:
    subject_code: str | None = None
    subject_name: str | None = None
    semester: str | None = None
    outcomes: list[ParsedOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_syllabus_pdf(file_obj) -> ParsedSyllabus:
    """Parse a syllabus PDF (a file path or a file-like object) into a
    ParsedSyllabus. Raises if the file isn't a readable PDF at all -
    callers should catch that and fall back to plain manual entry."""
    import pdfplumber

    result = ParsedSyllabus()

    with pdfplumber.open(file_obj) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]
    lines = [ln.strip() for ln in "\n".join(page_texts).split("\n")]

    _extract_header(lines, result)
    _extract_outcomes(lines, result)

    return result


def _extract_header(lines, result):
    code_row_idx = None
    for i, ln in enumerate(lines):
        m = COURSE_CODE_ROW_RE.search(ln)
        if m:
            result.subject_code = m.group(1).strip()
            code_row_idx = i
            break

    if code_row_idx is None:
        result.warnings.append(
            "Couldn't find a 'Course Code' row in the PDF - subject code and name need to be entered by hand."
        )
    else:
        # The subject name is normally the nearest non-empty line above the
        # Course Code row that isn't itself a semester or date line.
        for j in range(code_row_idx - 1, max(code_row_idx - 5, -1), -1):
            candidate = lines[j]
            if not candidate:
                continue
            if SEMESTER_RE.match(candidate) or DATE_LINE_RE.match(candidate):
                continue
            result.subject_name = candidate
            break
        if not result.subject_name:
            result.warnings.append("Couldn't find the subject name above the Course Code row - enter it by hand.")

    for ln in lines:
        m = SEMESTER_RE.match(ln)
        if m:
            result.semester = m.group(1).upper()
            break


def _extract_outcomes(lines, result):
    co_start = None
    for i, ln in enumerate(lines):
        if CO_HEADING_RE.match(ln):
            co_start = i
            break

    if co_start is None:
        result.warnings.append("Couldn't find a 'Course Outcomes' section in the PDF - add COs by hand below.")
        return

    current = None
    for ln in lines[co_start + 1:]:
        if not ln:
            continue
        if any(ln.lower().startswith(h) for h in STOP_HEADINGS):
            break
        m = CO_LINE_RE.match(ln)
        if m:
            if current:
                result.outcomes.append(current)
            current = ParsedOutcome(code=f"CO{m.group(1)}", description=m.group(2).strip())
        elif current:
            # A wrapped continuation of the previous CO's description.
            current.description = f"{current.description} {ln}".strip()
    if current:
        result.outcomes.append(current)

    if not result.outcomes:
        result.warnings.append("Found a 'Course Outcomes' heading but couldn't parse any CO lines from it - add them by hand below.")
