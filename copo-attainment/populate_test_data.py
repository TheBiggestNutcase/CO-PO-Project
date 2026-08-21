"""
One-off helper: fills in test data for a course that already has Course
Outcomes (e.g. created via syllabus import) but nothing past that yet -
adds standard PO1-PO12, a CO-PO correlation matrix, a full assessment
structure (IA1/IA2/IA3/ASQM/Exit Survey items, tagged to COs), a roster
of synthetic test students, and marks/responses for all of it - so you
can exercise the whole app end to end, including logging in as a Teacher
and seeing real-looking Results, without hand-typing everything.

Only fills in what's missing: if the course already has POs, a mapping,
a structure, or a roster, this script leaves that part alone rather than
duplicating it. Safe to re-run.

The roster it adds is entirely made up (fake names, fake USNs like
"1AM24CS0xx") - swap it out later with a real roster import (or delete
these students from the app) whenever you're ready to use the course for
real.

Run it with your venv activated - it goes through the real app/database
via the ORM, unlike add_test_users.py which only needed the standard
library:

    source venv/bin/activate
    python populate_test_data.py [SUBJECT_CODE]

Defaults to '21CS33' if you don't pass a subject code.
"""
import random
import sys

from app import create_app
from app.extensions import db
from app.models import (
    Course, CourseOutcome, ProgramOutcome, COPOMapping,
    AssessmentComponent, AssessmentItem, ItemCOMapping,
    Student, Mark, ExternalResult, ExitSurveyResponse,
)
from app.constants import STANDARD_PO_DESCRIPTIONS

NUM_STUDENTS = 15
SEED = 42
STANDARD_POS = [f"PO{i}" for i in range(1, 13)]

FIRST_NAMES = [
    "Aditi", "Rahul", "Priya", "Karan", "Sneha", "Arjun", "Divya", "Vikram",
    "Ananya", "Rohan", "Meera", "Siddharth", "Kavya", "Aryan", "Ishita",
    "Nikhil", "Pooja", "Varun", "Riya", "Aditya",
]
LAST_NAMES = [
    "Rao", "Shetty", "Kumar", "Reddy", "Nair", "Gupta", "Iyer", "Menon",
    "Bhat", "Pillai", "Sharma", "Naik", "Kulkarni", "Hegde", "Prasad",
]


def _ensure_program_outcomes(course):
    if course.program_outcomes:
        print(f"POs already exist ({len(course.program_outcomes)}) - leaving them alone.")
        return course.program_outcomes
    pos = []
    for i, code in enumerate(STANDARD_POS, start=1):
        po = ProgramOutcome(course_id=course.id, seq=i, code=code, is_pso=False, description=STANDARD_PO_DESCRIPTIONS.get(code))
        db.session.add(po)
        pos.append(po)
    db.session.flush()
    print(f"Added standard POs PO1-PO12.")
    return pos


def _ensure_mapping(course, cos, pos):
    existing = COPOMapping.query.join(CourseOutcome).filter(CourseOutcome.course_id == course.id).count()
    if existing:
        print(f"CO-PO mapping already exists ({existing} cells) - leaving it alone.")
        return
    # A generic, plausible spread - not tied to any subject's real
    # outcomes. Two passes: first guarantee every PO/PSO is touched by
    # at least one CO (round-robin), so the Results page's PO table has
    # no blank "-" rows; then add a few more random links per CO on top
    # for a fuller-looking, less mechanical-looking matrix.
    rng = random.Random(SEED)
    added = set()
    for i, po in enumerate(pos):
        co = cos[i % len(cos)]
        level = rng.choice([1, 2, 2, 3])
        db.session.add(COPOMapping(co_id=co.id, po_id=po.id, correlation_level=level))
        added.add((co.id, po.id))
    for co in cos:
        for po in rng.sample(pos, k=min(4, len(pos))):
            if (co.id, po.id) in added:
                continue
            level = rng.choice([1, 2, 2, 3])
            db.session.add(COPOMapping(co_id=co.id, po_id=po.id, correlation_level=level))
            added.add((co.id, po.id))
    db.session.commit()
    print(f"Added a CO-PO correlation matrix covering all {len(pos)} POs.")


def _ensure_structure(course, cos):
    existing_types = {c.type for c in course.components if c.items}
    if existing_types:
        print(f"Structure already exists for {sorted(existing_types)} - leaving those alone.")

    def get_or_create_component(comp_type, name):
        component = next((c for c in course.components if c.type == comp_type), None)
        if component is None:
            component = AssessmentComponent(course_id=course.id, type=comp_type, name=name)
            db.session.add(component)
            db.session.flush()
        return component

    ia_components = {}
    for test_type in ("IA1", "IA2", "IA3"):
        component = get_or_create_component(test_type, f"Internal Assessment Test {test_type[-1]}")
        ia_components[test_type] = component
        if component.items:
            continue
        seq = 1
        for co in cos:
            for label_suffix, max_marks in (("A", 10), ("B", 5)):
                item = AssessmentItem(component_id=component.id, seq=seq, label=f"{co.seq}{label_suffix}", max_marks=max_marks)
                db.session.add(item)
                db.session.flush()
                db.session.add(ItemCOMapping(item_id=item.id, co_id=co.id))
                seq += 1

    asqm = get_or_create_component("ASQM", "Assignment / Seminar / Quiz / Mini-project (self-study)")
    if not asqm.items:
        for i, co in enumerate(cos, start=1):
            item = AssessmentItem(component_id=asqm.id, seq=i, label=f"A{i}", max_marks=5)
            db.session.add(item)
            db.session.flush()
            db.session.add(ItemCOMapping(item_id=item.id, co_id=co.id))

    exit_survey = get_or_create_component("EXIT_SURVEY", "Course Exit Survey")
    if not exit_survey.items:
        for i, co in enumerate(cos, start=1):
            item = AssessmentItem(component_id=exit_survey.id, seq=i, label=f"Q{i}")
            db.session.add(item)
            db.session.flush()
            db.session.add(ItemCOMapping(item_id=item.id, co_id=co.id))

    get_or_create_component("EXTERNAL", "External (Semester End Exam)")

    db.session.commit()
    print("Assessment structure ready (IA1/IA2/IA3: 2 items/CO, ASQM/Exit Survey: 1 item/CO).")
    return ia_components, asqm, exit_survey


def _ensure_roster_and_marks(course, cos, ia_components, asqm, exit_survey):
    if course.students:
        print(f"Roster already has {len(course.students)} student(s) - leaving it alone, no marks generated.")
        return

    rng = random.Random(SEED)
    students = []
    used_names = set()
    for i in range(1, NUM_STUDENTS + 1):
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        student = Student(course_id=course.id, seq=i, usn=f"1AM24CS{str(i).zfill(3)}", name=name)
        db.session.add(student)
        students.append(student)
    db.session.flush()
    print(f"Added {len(students)} synthetic test students (fake names/USNs).")

    ability = {(s.id, co.id): rng.uniform(0.4, 0.98) for s in students for co in cos}

    def scored(max_marks, ability_value, noise=0.15):
        pct = max(0.0, min(1.0, ability_value + rng.uniform(-noise, noise)))
        return round(pct * max_marks * 2) / 2

    for component in ia_components.values():
        for student in students:
            if rng.random() < 0.05:
                continue
            for item in component.items:
                co = item.co_links[0].co
                db.session.add(Mark(student_id=student.id, item_id=item.id, marks_obtained=scored(item.max_marks, ability[(student.id, co.id)])))

    for item in asqm.items:
        co = item.co_links[0].co
        for student in students:
            if rng.random() < 0.03:
                continue
            db.session.add(Mark(student_id=student.id, item_id=item.id, marks_obtained=scored(item.max_marks, ability[(student.id, co.id)], noise=0.2)))

    rating_scale = ["A", "B", "C", "D", "E"]
    for item in exit_survey.items:
        co = item.co_links[0].co
        for student in students:
            if rng.random() < 0.1:
                continue
            a = ability[(student.id, co.id)]
            weights = [max(0.02, a - 0.1 * idx) for idx in range(len(rating_scale))]
            rating = rng.choices(rating_scale, weights=weights, k=1)[0]
            db.session.add(ExitSurveyResponse(student_id=student.id, item_id=item.id, rating=rating))

    for student in students:
        overall_ability = sum(ability[(student.id, co.id)] for co in cos) / len(cos)
        external_marks = round(max(0, min(100, rng.gauss(overall_ability * 100, 10))), 1)
        db.session.add(ExternalResult(
            student_id=student.id,
            internal_total=round(overall_ability * 50, 1),
            external_marks=external_marks,
            result="P" if external_marks >= 35 else "F",
        ))

    db.session.commit()
    print("Generated marks/responses for every component (with a few realistic absences).")


def populate(subject_code):
    course = Course.query.filter_by(subject_code=subject_code).first()
    if course is None:
        print(f"No course with subject code '{subject_code}' found - create it first (or pass a different subject code).")
        return

    cos = sorted(course.outcomes, key=lambda co: co.seq)
    if not cos:
        print(f"'{subject_code}' has no Course Outcomes yet - add those first (by hand, or via Import from syllabus).")
        return

    print(f"Populating test data for {course.subject_code} - {course.subject_name} ({len(cos)} COs found)...")
    pos = _ensure_program_outcomes(course)
    _ensure_mapping(course, cos, pos)
    ia_components, asqm, exit_survey = _ensure_structure(course, cos)
    _ensure_roster_and_marks(course, cos, ia_components, asqm, exit_survey)
    print(f"\nDone. Open the course (id {course.id}) in the app to see Results.")


def main():
    subject_code = sys.argv[1] if len(sys.argv) > 1 else "21CS33"
    app = create_app()
    with app.app_context():
        populate(subject_code)


if __name__ == "__main__":
    main()
