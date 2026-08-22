"""
Full reset + demo seed: wipes the existing database completely and
rebuilds it from scratch with two fully-populated courses, a Coordinator
account, and a Teacher account - everything needed to demo the app end
to end (setup, structure, roster, marks entry for every component type,
results, and the Coordinator/Teacher role split) without hand-entering
anything first.

THIS DELETES instance/copo.db. There is no undo - if you want to keep
what's in there now, copy it somewhere first:

    cp instance/copo.db instance/copo.db.bak

What it builds:
  - 21PHY22-DEMO (Engineering Physics) - the full built-in demo dataset:
    5 COs, PO1-PO12 + 2 PSOs, a CO-PO correlation matrix, the complete
    assessment structure, 20 students, and marks/responses for every
    component (see seed_demo_data.py).
  - 21CS33 (Analog and Digital Electronics) - the real course you
    imported from a syllabus earlier, with its real COs, plus a
    generated PO set, mapping, structure, a synthetic 15-student roster,
    and marks (see populate_test_data.py).
  - Coordinator account "Shwetha K R" (blank password) - can see/edit
    both courses.
  - Teacher account "KRIS" (blank password) - assigned to 21CS33 only,
    so logging in as them demonstrates the restricted Teacher view
    (enter marks / view results for that one course, nothing else).

Run with your venv activated:

    source venv/bin/activate
    python reset_and_seed_demo.py
"""
import os

# Delete the existing database (and any leftover journal/wal files)
# *before* importing app/ - db.create_all() then builds a completely
# fresh schema instead of adding to what's there.
INSTANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "copo.db")
for suffix in ("", "-journal", "-wal", "-shm"):
    path = DB_PATH + suffix
    if os.path.exists(path):
        os.remove(path)
        print(f"Deleted {os.path.basename(path)}")

from app import create_app
from app.extensions import db
from app.models import Course, CourseOutcome, User, CourseTeacher, ROLE_COORDINATOR, ROLE_TEACHER

import seed_demo_data
import populate_test_data

COORDINATOR_NAME = "Shwetha K R"
TEACHER_NAME = "KRIS"

CS33_COS = [
    ("CO1", "Design and analyze application of analog circuits using photo devices, timer IC, power supply and regulator IC and op-amp."),
    ("CO2", "Explain the basic principles of A/D and D/A conversion circuits and develop the same."),
    ("CO3", "Simplify digital circuits using Karnaugh Map, and Quine-McClusky Methods"),
    ("CO4", "Explain Gates and flip flops and make us in designing different data processing circuits, registers and counters and compare the types."),
    ("CO5", "Develop simple HDL programs"),
]


def main():
    app = create_app()  # also runs db.create_all() on the fresh (now-empty) instance dir
    with app.app_context():
        print("\n--- Course 1: 21PHY22-DEMO (full demo dataset) ---")
        seed_demo_data.build_demo()

        print("\n--- Course 2: 21CS33 (real COs + generated structure/roster/marks) ---")
        course2 = Course(
            subject_code="21CS33", subject_name="Analog and Digital Electronics",
            institution_name="AMC Engineering College", department="Electronics and Communication",
            faculty_name=COORDINATOR_NAME, academic_year="2022-23", semester="3",
            target_level1_pct=50, target_level2_pct=60, target_level3_pct=70,
            internal_marks_cutoff_pct=60,
        )
        db.session.add(course2)
        db.session.flush()
        for i, (code, desc) in enumerate(CS33_COS, start=1):
            db.session.add(CourseOutcome(course_id=course2.id, seq=i, code=code, description=desc))
        db.session.commit()
        populate_test_data.populate("21CS33")

        print("\n--- Accounts ---")
        coordinator = User(username=COORDINATOR_NAME, display_name=COORDINATOR_NAME, role=ROLE_COORDINATOR)
        coordinator.set_password("")
        db.session.add(coordinator)

        teacher = User(username=TEACHER_NAME, display_name=TEACHER_NAME, role=ROLE_TEACHER)
        teacher.set_password("")
        db.session.add(teacher)
        db.session.flush()

        db.session.add(CourseTeacher(course_id=course2.id, teacher_id=teacher.id))
        db.session.commit()

        print(f"Created coordinator '{COORDINATOR_NAME}' and teacher '{TEACHER_NAME}' (both blank password).")
        print(f"'{TEACHER_NAME}' is assigned only to 21CS33 - log in as them to see the Teacher role's restricted view.")
        print("\nDone. Run `python run.py` and log in to start the demo.")


if __name__ == "__main__":
    main()
