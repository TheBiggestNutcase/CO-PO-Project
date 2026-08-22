"""
Login, admin user management, and teacher account management.

Logging in is by *name*, not a separate username - see User.find_by_name
in app/models.py. It's a case-insensitive match on display_name, so
"Arushi Arunkumar" and "arushi arunkumar" both work. The User model still
has a `username` column (unique, not null), but it's just set equal to
the name behind the scenes now - nothing user-facing reads it anymore.

There's exactly one Admin account, created out-of-band from the
ADMIN_NAME/ADMIN_PASSWORD env vars the first time the app boots (see
ensure_admin_account() in app/migrations.py, called from
app/__init__.py) - there's no web form that creates one. The Admin logs
in like anyone else, then manages Coordinator and Teacher accounts from
/admin/users below: adding them, removing them, resetting passwords.

A Coordinator can create/remove Teacher accounts from /teachers below,
but - deliberately, as of the multi-course/multi-teacher work - can no
longer reset a teacher's password themselves (only the Admin can, from
/admin/users). Which course(s) a Teacher (or a Coordinator moonlighting
as a teacher elsewhere - see Course.faculty_display) is assigned to is
no longer managed from this page either: that's done per-course, from
setup_routes.py's manage_course_teachers ("Teachers" tab on a course),
since a subject only ever has the one Coordinator and picking teachers
*for that subject* reads far more naturally than picking subjects for a
teacher out of every course in the system.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import User, ROLE_COORDINATOR, ROLE_TEACHER
from app.auth import coordinator_required, admin_required

# Roles the Admin's own /admin/users page is allowed to create/manage -
# deliberately excludes ROLE_ADMIN itself, since there's exactly one
# Admin and it's never created through a form (see the ROLE_ADMIN
# comment in app/models.py).
_ADMIN_MANAGEABLE_ROLES = (ROLE_COORDINATOR, ROLE_TEACHER)
_ROLE_LABELS = {ROLE_COORDINATOR: "Unit Coordinator", ROLE_TEACHER: "Teacher"}

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        user = User.find_by_name(name)
        if user and user.check_password(password):
            login_user(user)
            next_url = request.form.get("next") or url_for(_home_endpoint_for(user))
            return redirect(next_url)
        flash("Incorrect name or password.", "error")

    return render_template("auth/login.html", next=request.args.get("next", ""))


def _home_endpoint_for(user):
    """Where a just-logged-in (or already-logged-in) user lands: Admin
    manages accounts, not course data, so it gets its own home."""
    return "auth.manage_users" if user.is_admin else "setup.list_courses"


def _home_for(user):
    return url_for(_home_endpoint_for(user))


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
    """Account directory for Teacher accounts: create one, or remove one.
    Deliberately does NOT do course assignment or password resets -
    course assignment is per-course now (see manage_course_teachers in
    setup_routes.py, linked from each course's "Teachers" tab), and only
    the Admin can reset a Teacher's password (see manage_users below)."""
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
                flash(f"Added a teacher account for {teacher.display_name}. Assign them to a course from that course's Teachers tab.", "success")

        return redirect(url_for("auth.manage_teachers"))

    teachers = User.query.filter_by(role=ROLE_TEACHER).order_by(User.display_name).all()
    return render_template("auth/manage_teachers.html", teachers=teachers)


@auth_bp.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
@coordinator_required
def delete_teacher(teacher_id):
    teacher = db.session.get(User, teacher_id)
    if teacher and teacher.role == ROLE_TEACHER:
        db.session.delete(teacher)
        db.session.commit()
        flash(f"Removed {teacher.display_name}'s account.", "success")
    return redirect(url_for("auth.manage_teachers"))


# --------------------------------------------------------- admin: users

@auth_bp.route("/admin/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_user":
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "")
            if not name:
                flash("Name is required.", "error")
            elif role not in _ADMIN_MANAGEABLE_ROLES:
                flash("Choose a role for the new account.", "error")
            elif User.find_by_name(name):
                flash(f"'{name}' is already taken - names must be unique since that's what's used to log in.", "error")
            else:
                user = User(username=name, display_name=name, role=role)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash(f"Added a {_ROLE_LABELS[role]} account for {user.display_name}.", "success")

        elif action == "reset_password":
            user = db.session.get(User, int(request.form.get("user_id", 0)))
            new_password = request.form.get("new_password", "")
            if user and user.role in _ADMIN_MANAGEABLE_ROLES:
                user.set_password(new_password)
                db.session.commit()
                flash(f"Password reset for {user.display_name}.", "success")

        return redirect(url_for("auth.manage_users"))

    users = (
        User.query.filter(User.role.in_(_ADMIN_MANAGEABLE_ROLES))
        .order_by(User.role, User.display_name)
        .all()
    )
    return render_template("auth/manage_users.html", users=users, role_labels=_ROLE_LABELS)


@auth_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    # Never lets an Admin account be deleted this way (there's only ever
    # one, and it isn't managed through this page) - belt-and-braces on
    # top of the fact that nothing ever links to it from manage_users.html.
    if user and user.role in _ADMIN_MANAGEABLE_ROLES:
        db.session.delete(user)
        db.session.commit()
        flash(f"Removed {user.display_name}'s account.", "success")
    return redirect(url_for("auth.manage_users"))
