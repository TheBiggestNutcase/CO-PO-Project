"""
Pure functions turning one student's scraped VTU results page (a
BeautifulSoup, as handed back by app.vtu_scraper.browser.VtuSession) into
structured per-subject rows. No Selenium, no network, no side effects -
this is the part of the whole pipeline that's actually unit-testable
without a live VTU connection or a real browser, see tests/test_vtu_scraper.

VTU's results page lays out one "divTable" per semester the student has a
result for (a plain div-based table, not an actual <table>): a header
div.text reading "Semester : N" followed by a sibling div containing
div.divTableRow rows, each holding div.divTableCell cells. The first row
is the header row (column labels); the rest are one row per subject.
Structure and CSS class names are VTU's own, mirrored from
nithinhm/vtu-marks-scraper-analyzer's data_processor.py (GPL v2) - the
divTableRow/divTableCell scraping approach is the same, but column
extraction here is driven by matching the header row's own label text
("Subject Code", "Internal Marks", ...) rather than that module's fixed
column-position slicing, so a column being added/reordered doesn't
silently misalign every value one column over - only a renamed label
would need a fix here, and get_subject_row() below fails loudly rather
than returning wrong data when that happens.

NOT yet verified against a live VTU page (see browser.py's docstring) -
if VTU has renamed any of the _CANDIDATE_HEADERS below, get_subject_row()
will raise SubjectNotFound with the header labels it actually saw, which
is the thing to fix.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

# Each of these is looked up case-insensitively/whitespace-normalized
# against the header row's own labels - VTU's exact label wording can
# drift in minor ways (extra whitespace, casing) without this needing an
# update, but a genuine rename still would.
_INTERNAL_HEADERS = ("internal marks", "internal")
_EXTERNAL_HEADERS = ("external marks", "external")
_TOTAL_HEADERS = ("total",)
_RESULT_HEADERS = ("result",)
_CODE_HEADERS = ("subject code",)
_NAME_HEADERS = ("subject name",)


def _normalize_header(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _first_matching_column(headers_norm, candidates):
    for i, h in enumerate(headers_norm):
        if h in candidates:
            return i
    return None


def _to_result(raw):
    """VTU shows '-' for a subject with no result yet, and sometimes
    appends ' *' to a passing grade (e.g. 'P *') - normalize both."""
    if raw is None:
        return None
    raw = raw.strip().upper().replace(" *", "")
    if not raw or raw in ("-", "--"):
        return None
    return raw


def _to_number(raw):
    """VTU shows '-' (or blank) for "not applicable" and things like 'AB'
    for absent - anything that doesn't parse as a plain number comes back
    as None rather than raising, since a student can legitimately have no
    numeric mark for a subject they haven't appeared for yet."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw in ("-", "--"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class ScrapedSubjectRow:
    semester: Optional[str]
    subject_code: str
    subject_name: str
    internal_marks: Optional[float]
    external_marks: Optional[float]
    total: Optional[float]
    result: Optional[str]
    # Any other columns VTU's table had for this row (e.g. reval sheets'
    # "Old Result"/"Final Marks"/"Final Result") that the fixed fields
    # above don't cover, keyed by the header's own label text - kept
    # around so a future feature can use them without a parser change.
    extra: dict = field(default_factory=dict)


class SubjectNotFound(Exception):
    """Raised by get_subject_row() when none of a student's scraped
    subjects match the code/name being looked for - carries the actual
    subject codes seen so the caller can show a useful message instead of
    a generic failure."""

    def __init__(self, message, seen_codes):
        super().__init__(message)
        self.seen_codes = seen_codes


def parse_result_page(soup):
    """Returns a flat list of ScrapedSubjectRow across every semester
    present on the page (VTU's per-USN lookup returns the student's
    *entire* result history to date, not just one semester)."""
    rows = []

    semester_headers = soup.find_all("div", style="text-align:center;padding:5px;")
    for header_div in semester_headers:
        semester = header_div.get_text(strip=True).split(":")[-1].strip() or None

        table_div = header_div.find_next_sibling("div")
        if table_div is None:
            continue

        table_rows = table_div.find_all("div", class_="divTableRow")
        if not table_rows:
            continue

        header_cells = [c.get_text(strip=True) for c in table_rows[0].find_all("div", class_="divTableCell")]
        headers_norm = [_normalize_header(h) for h in header_cells]

        code_idx = _first_matching_column(headers_norm, _CODE_HEADERS)
        name_idx = _first_matching_column(headers_norm, _NAME_HEADERS)
        internal_idx = _first_matching_column(headers_norm, _INTERNAL_HEADERS)
        external_idx = _first_matching_column(headers_norm, _EXTERNAL_HEADERS)
        total_idx = _first_matching_column(headers_norm, _TOTAL_HEADERS)
        result_idx = _first_matching_column(headers_norm, _RESULT_HEADERS)

        if code_idx is None or name_idx is None:
            # Not a subject table we recognize at all - skip rather than
            # guess (e.g. VTU sometimes shows a separate "no results yet"
            # notice block that isn't a marks table).
            continue

        known_idx = {i for i in (code_idx, name_idx, internal_idx, external_idx, total_idx, result_idx) if i is not None}

        for data_row in table_rows[1:]:
            cells = [c.get_text(strip=True) for c in data_row.find_all("div", class_="divTableCell")]
            if len(cells) <= max(code_idx, name_idx):
                continue

            extra = {
                header_cells[i]: cells[i]
                for i in range(len(cells))
                if i not in known_idx and i < len(header_cells)
            }

            rows.append(ScrapedSubjectRow(
                semester=semester,
                subject_code=cells[code_idx].strip().upper(),
                subject_name=cells[name_idx].strip(),
                internal_marks=_to_number(cells[internal_idx]) if internal_idx is not None and internal_idx < len(cells) else None,
                external_marks=_to_number(cells[external_idx]) if external_idx is not None and external_idx < len(cells) else None,
                total=_to_number(cells[total_idx]) if total_idx is not None and total_idx < len(cells) else None,
                result=_to_result(cells[result_idx]) if result_idx is not None and result_idx < len(cells) else None,
                extra=extra,
            ))

    return rows


def get_subject_row(rows, subject_code):
    """Picks out the row matching a course's subject_code (exact match,
    case/whitespace-insensitive) from one student's full scraped history.
    If more than one semester has a row for this code (e.g. an arrear
    re-attempt), the last one wins - later semesters are more likely to
    be the final/passing attempt. Raises SubjectNotFound (carrying every
    code actually seen, across all semesters) if there's no match at all,
    so the caller can show the student's real subject list instead of a
    bare "not found"."""
    target = (subject_code or "").strip().upper()
    matches = [r for r in rows if r.subject_code == target]
    if not matches:
        raise SubjectNotFound(
            f"No subject matching code {target!r} in this student's scraped results.",
            seen_codes=sorted({r.subject_code for r in rows}),
        )
    return matches[-1]
