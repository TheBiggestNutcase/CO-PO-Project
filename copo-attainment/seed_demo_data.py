"""
Seeds the database with one realistic demo course so you can explore the
app without typing everything in by hand first.

Run it after installing dependencies:

    source venv/bin/activate
    python seed_demo_data.py

It creates a course modeled on the original spreadsheet's own example
(AMC Engineering College, 21PHY22 - Engineering Physics): 5 COs, the
standard PO1-PO12 plus two PSOs, a CO-PO correlation matrix, a full
assessment structure (3 internal tests, self-study/ASQM, exit survey),
20 students, and marks/responses for all of it - with a bit of randomness
(including some absences) so the resulting CO attainment levels come out
varied (a mix of 0/1/2/3) rather than suspiciously uniform.

Safe to re-run: if the demo course already exists (matched by subject
code "21PHY22-DEMO"), it prints a message and exits instead of creating
a duplicate. To reset the demo, delete it from the app (or delete
instance/copo.db entirely to wipe everything) and re-run this script.
"""
import random

from app import create_app
from app.extensions import db
from app.models import (
    Course, CourseOutcome, ProgramOutcome, COPOMapping,
    AssessmentComponent, AssessmentItem, ItemCOMapping,
    Student, Mark, ExternalResult, ExitSurveyResponse,
)
from app.constants import STANDARD_PO_DESCRIPTIONS

PSO_DESCRIPTIONS = {
    "PSO1": "Apply the principles of physics to analyze and troubleshoot problems in electronic and photonic devices.",
    "PSO2": "Use experimental and computational techniques to characterize the physical properties of engineering materials.",
}

DEMO_SUBJECT_CODE = "21PHY22-DEMO"

CO_DESCRIPTIONS = [
    "Explain the fundamental principles of oscillations and waves.",
    "Apply quantum mechanics concepts to simple physical systems.",
    "Analyze the properties of semiconductors and their applications.",
    "Describe the principles behind laser action and fiber optics.",
    "Relate crystal structure to the physical properties of materials.",
]

# CO -> {PO: correlation level 1-3}, loosely modeled on the kind of
# spread you'd see in a real mapping matrix (not every CO touches every PO).
CO_PO_CORRELATION = {
    "CO1": {"PO1": 3, "PO2": 2, "PO12": 2},
    "CO2": {"PO1": 3, "PO2": 3, "PO12": 2},
    "CO3": {"PO1": 3, "PO2": 3, "PO3": 1, "PO12": 2},
    "CO4": {"PO1": 3, "PO2": 2, "PO3": 1, "PO5": 1, "PO12": 2},
    "CO5": {"PO1": 3, "PO2": 2, "PO3": 1, "PO5": 2, "PO8": 3, "PO9": 3, "PO12": 2},
}

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


def build_demo(course_number_of_students=20, seed=7):
    rng = random.Random(seed)

    course = Course(
        institution_name="AMC Engineering College",
        department="Physics",
        subject_code=DEMO_SUBJECT_CODE,
        subject_name="Engineering Physics",
        faculty_name="Nithin H M",
        academic_year="2025-26",
        semester="II",
        target_level1_pct=50, target_level2_pct=60, target_level3_pct=70,
        internal_marks_cutoff_pct=60,
    )
    db.session.add(course)
    db.session.flush()

    cos = []
    for i, description in enumerate(CO_DESCRIPTIONS, start=1):
        co = CourseOutcome(course_id=course.id, seq=i, code=f"CO{i}", description=description)
        db.session.add(co)
        cos.append(co)
    db.session.flush()
    co_by_code = {co.code: co for co in cos}

    pos = []
    for i, code in enumerate(STANDARD_POS, start=1):
        po = ProgramOutcome(course_id=course.id, seq=i, code=code, is_pso=False, description=STANDARD_PO_DESCRIPTIONS.get(code))
        db.session.add(po)
        pos.append(po)
    for i, code in enumerate(["PSO1", "PSO2"], start=len(STANDARD_POS) + 1):
        po = ProgramOutcome(course_id=course.id, seq=i, code=code, is_pso=True, description=PSO_DESCRIPTIONS.get(code))
        db.session.add(po)
        pos.append(po)
    db.session.flush()
    po_by_code = {po.code: po for po in pos}

    for co_code, po_map in CO_PO_CORRELATION.items():
        for po_code, level in po_map.items():
            db.session.add(COPOMapping(co_id=co_by_code[co_code].id, po_id=po_by_code[po_code].id, correlation_level=level))

    # --- Assessment structure: IA1/IA2/IA3 each get 2 questions per CO
    # (one worth 10 marks, one worth 5), ASQM gets 1 item per CO, and the
    # exit survey gets 1 question per CO.
    ia_components = {}
    for test_type in ("IA1", "IA2", "IA3"):
        component = AssessmentComponent(course_id=course.id, type=test_type, name=f"Internal Assessment Test {test_type[-1]}")
        db.session.add(component)
        db.session.flush()
        ia_components[test_type] = component
        seq = 1
        for co in cos:
            for label_suffix, max_marks in (("A", 10), ("B", 5)):
                item = AssessmentItem(component_id=component.id, seq=seq, label=f"{co.seq}{label_suffix}", max_marks=max_marks)
                db.session.add(item)
                db.session.flush()
                db.session.add(ItemCOMapping(item_id=item.id, co_id=co.id))
                seq += 1

    asqm = AssessmentComponent(course_id=course.id, type="ASQM", name="Assignment / Seminar / Quiz / Mini-project (self-study)")
    db.session.add(asqm)
    db.session.flush()
    for i, co in enumerate(cos, start=1):
        item = AssessmentItem(component_id=asqm.id, seq=i, label=f"A{i}", max_marks=5)
        db.session.add(item)
        db.session.flush()
        db.session.add(ItemCOMapping(item_id=item.id, co_id=co.id))

    exit_survey = AssessmentComponent(course_id=course.id, type="EXIT_SURVEY", name="Course Exit Survey")
    db.session.add(exit_survey)
    db.session.flush()
    for i, co in enumerate(cos, start=1):
        item = AssessmentItem(component_id=exit_survey.id, seq=i, label=f"Q{i}")
        db.session.add(item)
        db.session.flush()
        db.session.add(ItemCOMapping(item_id=item.id, co_id=co.id))

    external = AssessmentComponent(course_id=course.id, type="EXTERNAL", name="External (Semester End Exam)")
    db.session.add(external)

    db.session.flush()

    # --- Roster
    students = []
    used_names = set()
    for i in range(1, course_number_of_students + 1):
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        student = Student(course_id=course.id, seq=i, usn=f"1AM22PH{str(i).zfill(3)}", name=name)
        db.session.add(student)
        students.append(student)
    db.session.flush()

    # --- Marks: each student gets a hidden "ability" per CO so results
    # come out varied but internally consistent (a strong student on CO1
    # tends to also do reasonably on CO1's ASQM item and exit rating).
    ability = {(s.id, co.id): rng.uniform(0.4, 0.98) for s in students for co in cos}

    def scored(max_marks, ability_value, noise=0.15):
        pct = max(0.0, min(1.0, ability_value + rng.uniform(-noise, noise)))
        return round(pct * max_marks * 2) / 2  # nearest 0.5

    for test_type, component in ia_components.items():
        for student in students:
            if rng.random() < 0.05:  # ~5% absent from any given test
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
            if rng.random() < 0.1:  # not everyone fills the survey
                continue
            a = ability[(student.id, co.id)]
            # Higher ability -> more likely to rate favorably, but with noise.
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
    return course


def main():
    app = create_app()
    with app.app_context():
        existing = Course.query.filter_by(subject_code=DEMO_SUBJECT_CODE).first()
        if existing:
            print(f"Demo course already exists (course id {existing.id}, '{existing.subject_code}').")
            print("Delete it from the app first if you want to reseed, or just open it - it's already there.")
            return
        course = build_demo()
        print(f"Created demo course: {course.subject_code} - {course.subject_name} (id {course.id})")
        print(f"Open http://localhost:8000/courses/{course.id} after running `python run.py`.")


if __name__ == "__main__":
    main()
