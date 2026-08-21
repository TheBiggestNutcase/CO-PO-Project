# CO-PO Attainment Calculator

A personal web app that reimplements the calculations from
`attainment template.xlsx` (the AMC Engineering College CO/PO attainment
workbook) so you can enter course info, CO/PO mapping, assessment
structure and marks once, and get CO & PO attainment computed
automatically instead of hand-wiring spreadsheet formulas.

## What's implemented (v1 / foundation)

- Course setup: institution/department/subject/faculty/section details,
  Level 1/2/3 targets, internal pass cutoff.
- Course Outcomes (COs) and Program Outcomes/PSOs.
- CO-PO/PSO correlation matrix (1-3, NBA scale).
- Assessment structure: define the question/item list (label, max marks,
  CO tag) for IA1, IA2, IA3, ASQM (self-study), and the Exit Survey.
  External doesn't need a structure - it's entered as totals.
- Student roster: one at a time, bulk-paste as `USN, Name` per line, or
  imported from a sections-wise roster Word document (see below).
- Marks entry: a grid per component. Leave a cell blank for an absent
  student - it's excluded rather than counted as zero.
- Results: computed CO attainment (IA %, ASQM %, CIA %, External %, Exit
  Survey %, blended %, Level 0-3) and PO/PSO attainment (correlation-
  weighted average of CO levels).
- Import from syllabus: upload a syllabus PDF and it pre-fills a new
  course's subject code/name and Course Outcomes for you to review and
  edit before anything is saved (see below).

A **Course** represents one section's offering of a subject (e.g.
"21CS33, Section B") - if you teach the same subject to multiple
sections, create one Course per section.

Deliberately **not** built yet (by design, see project discussion):
bulk/CSV import of marks, gap analysis vs previous years, formatted
report export, multi-course dashboards, multiple user accounts.

## Running it

Requires Python 3.9+ (built and tested against 3.11.15, but nothing here
needs 3.11 specifically).

```bash
cd copo-attainment
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

**The `source venv/bin/activate` line matters** - it's what points `pip`
and `python` at the virtual environment instead of your system Python.
If you skip it, `pip install` will print "Defaulting to user installation
because normal site-packages is not writeable" and install into your
system/user Python instead of the project's own isolated environment.
Every command below assumes the venv is activated (you'll see `(venv)` at
the start of your shell prompt once it is).

Then open **http://localhost:8000** in your browser.

Data is stored in `instance/copo.db` (a SQLite file created automatically
on first run) - it persists between restarts, so you can close the app
and come back later to keep entering marks. To start completely fresh,
just delete that file.

## Loading demo/test data

To explore the app without typing everything in by hand first, run the
seed script once (with the venv activated):

```bash
python seed_demo_data.py
```

This creates one realistic demo course - modeled on the original
template's own example (AMC Engineering College, 21PHY22 - Engineering
Physics) - fully populated: 5 COs, PO1-PO12 plus 2 PSOs, a CO-PO
correlation matrix, the complete assessment structure (3 internal tests,
self-study, exit survey), 20 students, and marks/responses for all of
it. The numbers are randomized (with a fixed seed, so they're the same
every time you reseed) but built from a per-student "ability" per CO, so
the results come out believable - some COs land at Level 0, some at
Level 1 or 2, rather than everything being identical.

It's safe to run more than once - if the demo course already exists it
just tells you so instead of creating a duplicate. To reset the demo
data, delete the course from within the app (or delete
`instance/copo.db` to wipe everything) and run the script again.

## Importing from a syllabus PDF

On the courses list, "Import from syllabus" lets you upload a syllabus PDF
instead of typing the subject code/name and Course Outcomes in by hand.
It's tuned against the standard VTU-style layout: a Course Code/CIE/SEE
table near the top of the document, and a "Course Outcomes" (or "Course
outcome (Course Skill Set)") section listing CO1-CO5+ further down.

This is a **best-effort extraction, not a guarantee** - syllabus PDFs
aren't a fixed format, so nothing is saved straight from the upload.
Instead, you land on a review screen with every field pre-filled but
editable (institution/department/faculty/academic year are never in the
syllabus, so those stay blank for you to fill in), plus a few spare blank
CO rows in case the parser missed one. The course and its COs are only
created once you click "Create course" there. If the parser can't find a
section it expects, it says so in a warning banner rather than silently
leaving something blank - you can still fill in everything by hand from
that same screen.

The parsing logic lives in `app/syllabus_import.py`, with two real sample
syllabi (differently formatted) as regression tests in
`tests/test_syllabus_import.py` - if you run this against a syllabus
layout it doesn't handle well, the review screen is exactly what lets you
fix it up rather than being stuck.

## Importing a roster from a Word document

On a course's roster page, "Import from file" lets you upload a
"sections-wise students list" .docx instead of typing or pasting the
roster in by hand. It's tuned against documents made of repeating blocks
- a heading like `"5th Semester Section B:  Total:69"` (or the common
typo `"Sction"`), followed by one or more Sl.No./USN/Name tables.

Real roster documents tend to be messy in ways a rigid parser would choke
on: a section's list can be split across more than one table with no
repeated header row, and a table can come out with extra/duplicate
columns or names that lost their spacing during formatting. This is
extracted best-effort rather than assumed clean - every section lands on
an editable review screen (grouped by section, with each section's own
"Import" button), with row-level warnings on anything that looked
malformed and a section-level warning if the extracted count doesn't
match the "Total: N" the heading declared. Since a Course covers one
section, you'd normally import just the one section that matches it -
the review screen highlights whichever detected section matches the
course's own Section field, and nothing is added to the roster until you
click that section's "Import" button. Existing USNs already on the
roster are skipped automatically rather than erroring.

PDF rosters aren't supported yet - only `.docx`.

The parsing logic lives in `app/roster_import.py`, with a real (genuinely
messy) sample roster as a regression test in `tests/test_roster_import.py`
- including the split-table and mangled-column cases described above, so
they don't silently regress if the parser changes later.

## Running the tests

Tests need `pytest`, which is a dev-only dependency and lives in
`requirements-dev.txt` (not `requirements.txt`, so a hiccup fetching it
never blocks running the app itself):

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

`tests/test_engine.py` hand-verifies the calculation engine against a
worked example using the exact arithmetic from the original spreadsheet.
`tests/test_routes.py` exercises the actual web flow (create course -> COs
-> POs -> mapping -> structure -> roster -> marks -> results, plus the
syllabus-import and roster-import upload/review/confirm flows) through
Flask's test client. `tests/test_syllabus_import.py` and
`tests/test_roster_import.py` check the PDF/Word parsers directly against
real sample documents.

## How the calculation engine works

See the docstring at the top of `app/engine.py` for the full formula
chain (it mirrors the "IA Test only", "ASQM only", "External", "Exit
survey", "CO attainment" and "Course PO attainment" sheets in the
original template), and `tests/test_engine.py` for a hand-worked example
showing exactly how a set of marks turns into CO and PO attainment
numbers.

One deliberate improvement over the spreadsheet: every fact here is tied
to a real Student database record rather than a spreadsheet row position,
so there's no equivalent of the `#REF!` errors we found in the original
template's gap-analysis sheets (caused by rows/sheets being
deleted/reordered over time).

## Project structure

```
app/
  __init__.py       - Flask app factory
  extensions.py     - the shared SQLAlchemy db object
  models.py         - the data model (Course, CO, PO, mapping, marks, ...)
  engine.py         - the calculation engine (pure Python, unit-tested)
  routes/
    setup_routes.py     - courses, COs, POs, CO-PO mapping
    structure_routes.py - assessment item/question structure
    marks_routes.py     - student roster + marks/response entry
    results_routes.py   - computed CO/PO attainment
    import_routes.py    - syllabus PDF upload -> review -> create course
  syllabus_import.py - syllabus PDF text-extraction heuristics
  roster_import.py   - roster .docx text-extraction heuristics
  templates/        - Jinja2/HTML pages (no JS framework - plain forms)
  static/style.css  - all the styling
tests/
  test_engine.py           - calculation engine unit tests
  test_routes.py           - end-to-end route/flow tests
  test_syllabus_import.py  - syllabus PDF parser unit tests
  test_roster_import.py    - roster .docx parser unit tests
  fixtures/                - sample syllabus/roster files used by the tests above
run.py              - entry point (`python run.py`)
```
