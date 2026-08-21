"""
Login, first-run coordinator setup, and teacher account management.

Logging in is by *name*, not a separate username - see User.find_by_name
in app/models.py. It's a case-insensitive match on display_name, so
"Arushi Arunkumar" and "arushi arunkumar" both work. The User model still
has a `username` column (unique, not null), but it's just set equal to
the name behind the scenes now - nothing user-facing reads it anymore.

There's exactly one Unit Coordinator account, created once via
setup_coordinator() the very first time the app runs (see the
before_request bootstrap check in app/__init__.py, which redirects there
whenever no User exists yet). From then on, the coordinator logs in
normally and manages Teacher accounts from /teachers - creating them,
assigning each one to the specific course(s) they should see, and
resetting their password if they forget it (there's no self-service email
flow here - it's a personal tool, not a hosted service).
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import User, Course, CourseTeacher, ROLE_COORDINATOR, ROLE_TEACHER
from app.auth import coordinator_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/setup-coordinator", methods=["GET", "POST"])
def setup_coordinator():
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name:
            flash("Your name is required.", "error")
        elif password != confirm_password:
            flash("Passwords don't match.", "error")
        else:
            coordinator = User(
                username=name, display_name=name, role=ROLE_COORDINATOR,
            )
            coordinator.set_password(password)
            db.session.add(coordinator)
            db.session.commit()
            login_user(coordinator)
            flash("Your coordinator account is set up. You can add teacher accounts from the Teachers page.", "success")
            return redirect(url_for("setup.list_courses"))

    return render_template("auth/setup_coordinator.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if User.query.count() == 0:
        return redirect(url_for("auth.setup_coordinator"))
    if current_user.is_authenticated:
        return redirect(url_for("setup.list_courses"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        user = User.find_by_name(name)
        if user and user.check_password(password):
            login_user(user)
            next_url = request.form.get("next") or url_for("setup.list_courses")
            return redirect(next_url)
        flash("Incorrect name or password.", "error")

    return render_template("auth/login.html", next=request.args.get("next", ""))


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------- teachers

@auth_bp.route("/teachers", methods=["GET", "POST"])
@coordinator_required
def manage_teachers():
    courses = Course.query.order_by(Course.subject_code).all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_teacher":
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "")
            if not name:
                flash("Name is required.", "error")
            elif User.find_by_name(name):
                flash(f"'{name}' is already taken - names must be unique since that's what's used to log in.", "error")
            else:
                teacher = User(username=name, display_name=name, role=ROLE_TEACHER)
                teacher.set_password(password)
                db.session.add(teacher)
                db.session.commit()
                flash(f"Added a teacher account for {teacher.display_name}. Assign them to a course below.", "success")

        elif action == "update_assignments":
            teacher = db.session.get(User, int(request.form.get("teacher_id", 0)))
            if teacher and teacher.role == ROLE_TEACHER:
                selected_ids = {int(cid) for cid in request.form.getlist("course_ids")}
                existing_by_course = {a.course_id: a for a in teacher.course_assignments}
                for course in courses:
                    if course.id in selected_ids and course.id not in existing_by_course:
                        db.session.add(CourseTeacher(course_id=course.id, teacher_id=teacher.id))
                    elif course.id not in selected_ids and course.id in existing_by_course:
                        db.session.delete(existing_by_course[course.id])
                db.session.commit()
                flash(f"Updated {teacher.display_name}'s course assignments.", "success")

        elif action == "reset_password":
            teacher = db.session.get(User, int(request.form.get("teacher_id", 0)))
            new_password = request.form.get("new_password", "")
            if teacher and teacher.role == ROLE_TEACHER:
                teacher.set_password(new_password)
                db.session.commit()
                flash(f"Password reset for {teacher.display_name}.", "success")

        return redirect(url_for("auth.manage_teachers"))

    teachers = User.query.filter_by(role=ROLE_TEACHER).order_by(User.display_name).all()
    assigned_course_ids = {t.id: {a.course_id for a in t.course_assignments} for t in teachers}
    return render_template(
        "auth/manage_teachers.html",
        teachers=teachers, courses=courses, assigned_course_ids=assigned_course_ids,
    )


@auth_bp.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
@coordinator_required
def delete_teacher(teacher_id):
    teacher = db.session.get(User, teacher_id)
    if teacher and teacher.role == ROLE_TEACHER:
        db.session.delete(teacher)
        db.session.commit()
        flash(f"Removed {teacher.display_name}'s account.", "success")
    return redirect(url_for("auth.manage_teachers"))
