"""
Best-effort extraction of a student roster from a "sections-wise list"
Word document, so a course's roster can be pre-filled instead of typed or
pasted in by hand.

Targets the layout actually seen in practice: a document made up of
repeating blocks, each block being a few heading paragraphs followed by
one or more tables -

    AMC ENGINEERING COLLEGE
    Computer Science and Engineering 2024-25
    5th Semester Sction A:  Total:66
    <table: Sl.No. | USN | Name, one row per student>

    AMC ENGINEERING COLLEGE
    Computer Science and Engineering 2024-25
    5th Semester Sction B:  Total:69
    <table>
    <table>                          <- a section's list can split across
                                         more than one table, with no
                                         repeated header row on the second

Real files are messy: a section's student count can span more than one
table, and a table can come out with extra/duplicate columns (e.g. a USN
repeated in two cells) or with spaces stripped from a name. This module
extracts what it reasonably can and flags anything it isn't confident
about - the caller always routes the result through a human-editable
review screen before anything is saved, exactly like syllabus import.
"""
import re
from dataclasses import dataclass, field

# "Computer Science and Engineering 2024-25" - a department name followed
# by an academic year. Doesn't match the institution line above it (no
# trailing year there), which is deliberate - institution isn't imported.
DEPT_YEAR_RE = re.compile(r"^(?P<department>.+?)\s+(?P<year>\d{4}-\d{2,4})\s*$")

# "5th Semester Sction A:  Total:66" - "Section" is sometimes misspelled
# "Sction" in these documents (consistently, in practice), hence the
# optional "e". Colons and spacing around "Total" vary too.
SECTION_HEADING_RE = re.compile(
    r"^(?P<semester>.+?)\s+Semester\s+S(?:e)?ction\s+(?P<section>[A-Za-z0-9]+)\s*:?\s*Total\s*:?\s*(?P<total>\d+)",
    re.IGNORECASE,
)

# VTU-style USN: a digit, 2 letters, 2 digits, 2 letters, 3 digits - e.g.
# "1AM23CS040". Anchored (not searched) so it only matches a cell holding
# nothing but a USN, not one that merely contains one.
USN_RE = re.compile(r"^[0-9][A-Za-z]{2}[0-9]{2}[A-Za-z]{2}[0-9]{3}$")

# A "Sl. No." style cell - digits with an optional trailing period.
SLNO_RE = re.compile(r"^\d+\.?$")


@dataclass
class ParsedStudent:
    usn: str
    name: str
    warning: str | None = None


@dataclass
class ParsedSection:
    label: str
    department: str | None = None
    academic_year: str | None = None
    semester: str | None = None
    declared_total: int | None = None
    students: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class ParsedRoster:
    sections: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def parse_roster_docx(file_obj) -> ParsedRoster:
    """Parse a sections-wise roster .docx (a file path or file-like
    object) into a ParsedRoster. Raises if the file isn't a readable
    .docx at all - callers should catch that and fall back to manual
    entry."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(file_obj)
    result = ParsedRoster()

    current_section = None
    pending_department = None
    pending_year = None

    for child in document.element.body.iterchildren():
        tag = child.tag.split("}")[-1]

        if tag == "p":
            text = Paragraph(child, document).text.strip()
            if not text:
                continue
            m = SECTION_HEADING_RE.match(text)
            if m:
                if current_section is not None:
                    _finalize_section(current_section, result)
                current_section = ParsedSection(
                    label=m.group("section").strip(),
                    department=pending_department,
                    academic_year=pending_year,
                    semester=m.group("semester").strip(),
                    declared_total=int(m.group("total")),
                )
                continue
            m = DEPT_YEAR_RE.match(text)
            if m:
                pending_department = m.group("department").strip()
                pending_year = m.group("year").strip()

        elif tag == "tbl":
            if current_section is None:
                result.warnings.append(
                    "Found a student table before any recognizable 'Semester ... Section ...: Total: N' "
                    "heading - it was skipped."
                )
                continue
            _extract_table_rows(Table(child, document), current_section)

    if current_section is not None:
        _finalize_section(current_section, result)

    if not result.sections:
        result.warnings.append(
            "Couldn't find any 'Semester ... Section ...: Total: N' section headings - "
            "this file may use a different layout than expected."
        )

    return result


def _extract_table_rows(table, section):
    for row in table.rows:
        cells = [c.text.strip() for c in row.cells]
        if any(c.lower() == "usn" for c in cells):
            continue  # the header row

        usn_cell_indexes = [i for i, c in enumerate(cells) if USN_RE.match(c)]
        if not usn_cell_indexes:
            if any(cells):
                section.warnings.append(f"Couldn't find a USN in a row ({cells}) - it was skipped.")
            continue
        usn = cells[usn_cell_indexes[0]]

        name = ""
        for i in range(len(cells) - 1, -1, -1):
            c = cells[i]
            if not c or i in usn_cell_indexes or SLNO_RE.match(c):
                continue
            name = c
            break

        warning = None
        if len(usn_cell_indexes) > 1:
            warning = "row had extra/duplicate columns in the source table"
        if name and " " not in name and len(name) > 12:
            spacing_note = "name may have lost its spacing in the source document"
            warning = f"{warning}; {spacing_note}" if warning else spacing_note

        section.students.append(ParsedStudent(usn=usn, name=name, warning=warning))


def _finalize_section(section, result):
    if section.declared_total is not None and len(section.students) != section.declared_total:
        section.warnings.append(
            f"The heading said {section.declared_total} students, but {len(section.students)} were "
            f"extracted - check for missing or extra rows below."
        )
    result.sections.append(section)
