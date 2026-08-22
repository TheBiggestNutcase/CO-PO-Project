"""
One-off helper: adds three test accounts directly to instance/copo.db -
"Admin" (Admin), "Co" (Unit Coordinator) and "Teach" (Teacher), all with
a blank password, so you can try out every role without setting
ADMIN_NAME/ADMIN_PASSWORD or going through any web form by hand. In
production there's no equivalent to this script - the one real Admin
account only ever comes from the ADMIN_NAME/ADMIN_PASSWORD env vars (see
DEPLOYMENT.md and ensure_admin_account() in app/migrations.py).

Uses only the standard library (sqlite3) - no venv/dependencies needed,
so `python3 add_test_users.py` works even before `pip install -r
requirements.txt` has been run.

Safe to re-run: skips creating an account if a user with that name
already exists, and never touches or deletes anything else in the
database.

Delete this file (or the accounts themselves, from the app's Users/
Teachers pages) whenever you're done testing - it's not part of the app
itself.
"""
import os
import sqlite3
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "copo.db")

# Pre-computed werkzeug scrypt hashes of an EMPTY password. Generated with
# `werkzeug.security.generate_password_hash("")` - kept as a literal here
# so this script has zero dependencies beyond the standard library.
HASH_ADMIN = "scrypt:32768:8:1$DFL1GGnF5ezlMpM1$0e03875e74f25a1b073c58ce485d0abc5b9949331127747a3a914f4f7a14ad2ae7bfdaa632c2e800a78f0579d1714750c75bdfd91e251b0d2cfabfc63a7f11ae"
HASH_COORDINATOR = "scrypt:32768:8:1$ien7TmEN9zyTylKk$d54e8a2e0329b6b060877ed207bc5a28490995446b061131b8cfc57aacc9f7e5398841d16f16ef9d683e631ae5f7a16166cc85d11bd14f1a58f5444bde017403"
HASH_TEACHER = "scrypt:32768:8:1$2M2UswfQS5LMpoXp$aadac426718b831e99905b292a9f14c0e1a0b5479fa00894f06d6da3e1d2ec6d0c2222d75685f00def9f3fd7c53e1456c8c3e5a60da1c740ce1afebd7f90cfb0"


def main():
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH} yet - run the app once first (`python run.py`), then re-run this.")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    now = datetime.datetime.utcnow().isoformat(sep=" ")

    def ensure_user(name, password_hash, role):
        cur.execute("SELECT id FROM user WHERE display_name = ?", (name,))
        row = cur.fetchone()
        if row:
            print(f"'{name}' already exists (id {row[0]}) - leaving it alone.")
            return row[0]
        cur.execute(
            "INSERT INTO user (username, display_name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, name, password_hash, role, now),
        )
        print(f"Created {role} account '{name}'.")
        return cur.lastrowid

    ensure_user("Admin", HASH_ADMIN, "admin")
    coordinator_id = ensure_user("Co", HASH_COORDINATOR, "coordinator")
    teacher_id = ensure_user("Teach", HASH_TEACHER, "teacher")

    # Assign the teacher to every course that exists so far, for testing -
    # adjust from the app's Teachers page any time.
    cur.execute("SELECT id, subject_code FROM course")
    courses = cur.fetchall()
    for course_id, subject_code in courses:
        cur.execute(
            "SELECT 1 FROM course_teacher WHERE course_id = ? AND teacher_id = ?",
            (course_id, teacher_id),
        )
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO course_teacher (course_id, teacher_id) VALUES (?, ?)",
            (course_id, teacher_id),
        )
        print(f"Assigned 'Teach' to {subject_code}.")

    con.commit()
    con.close()

    print()
    print("Done. Log in with:")
    print("  Admin       - name: Admin  password: (blank)")
    print("  Coordinator - name: Co     password: (blank)")
    print("  Teacher     - name: Teach  password: (blank)")


if __name__ == "__main__":
    main()
