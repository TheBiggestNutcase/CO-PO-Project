"""
Auth / role-enforcement tests: the Admin env-var bootstrap, login/logout,
that a Teacher account is actually confined to "enter marks and view
results" for courses they've been assigned to - nothing else, and that
Admin can add/remove Coordinator and Teacher accounts but nothing about
course data.

Login is by name (case-insensitive), not a separate username - see
User.find_by_name in app/models.py.
"""
import pytest

from app import create_app
from app.extensions import db
from app.migrations import ensure_admin_account
from app.models import Course, User, CourseTeacher, ROLE_ADMIN, ROLE_COORDINATOR, ROLE_TEACHER


@pytest.fixture()
def app():
    # ADMIN_NAME/ADMIN_PASSWORD explicitly None (not just omitted) so
    # these tests are deterministic regardless of whatever's in the
    # ambient environment they happen to run in - see
    # test_admin_bootstrap_does_nothing_without_env_vars below.
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True,
        "ADMIN_NAME": None, "ADMIN_PASSWORD": None,
    })
    yield app


@pytest.fixture()
def client(app):
    with app.app_context():
        yield app.test_client()


def _make_coordinator(name="Test Coordinator", password="testpass123"):
    user = User(username=name, display_name=name, role=ROLE_COORDINATOR)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _make_teacher(name="Test Teacher", password="teachpass123"):
    user = User(username=name, display_name=name, role=ROLE_TEACHER)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _make_admin(name="Test Admin", password="adminpass123"):
    user = User(username=name, display_name=name, role=ROLE_ADMIN)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _make_course(subject_code="T1"):
    course = Course(subject_code=subject_code, subject_name="Test Subject")
    db.session.add(course)
    db.session.commit()
    return course


def _login(client, name, password):
    return client.post("/login", data={"name": name, "password": password}, follow_redirects=True)


# ------------------------------------------------------------- bootstrap

def test_no_users_shows_login_page_not_a_signup_form(client):
    """There's no public account-creation page anymore - a fresh
    deployment with nobody logged in just shows the login form (which
    will fail every attempt until an Admin exists), not a "claim this
    app" screen a random visitor to a public URL could use."""
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    assert b"log in" in resp.data.lower()
    assert b"name" in resp.data.lower() and b"password" in resp.data.lower()


def test_setup_coordinator_route_is_gone(client):
    resp = client.get("/setup-coordinator")
    assert resp.status_code == 404
    resp = client.post("/setup-coordinator", data={"name": "x", "password": "", "confirm_password": ""})
    assert resp.status_code == 404
    assert User.query.count() == 0


def test_admin_env_var_bootstrap_creates_account():
    """The one Admin account is created from ADMIN_NAME/ADMIN_PASSWORD at
    app-construction time (see ensure_admin_account in
    app/migrations.py) - not through any route."""
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True,
        "ADMIN_NAME": "Root Admin", "ADMIN_PASSWORD": "adminpass123",
    })
    with app.app_context():
        admin = User.query.filter_by(role=ROLE_ADMIN).first()
        assert admin is not None
        assert admin.display_name == "Root Admin"
        assert admin.check_password("adminpass123")


def test_admin_bootstrap_does_nothing_without_env_vars(client):
    """No ADMIN_NAME/ADMIN_PASSWORD set (the `client` fixture's app has
    neither) means no Admin gets created - not a fallback account with
    guessable default credentials."""
    assert User.query.filter_by(role=ROLE_ADMIN).count() == 0


def test_admin_bootstrap_is_idempotent(app):
    """Runs on every startup (every deploy, every restart) - must never
    create a second Admin or touch the existing one's password."""
    with app.app_context():
        ensure_admin_account(db, "Root Admin", "first-password")
        first_hash = User.query.filter_by(role=ROLE_ADMIN).one().password_hash

        ensure_admin_account(db, "Root Admin", "second-password")
        admins = User.query.filter_by(role=ROLE_ADMIN).all()
        assert len(admins) == 1
        assert admins[0].password_hash == first_hash  # unchanged - not overwritten


def test_admin_bootstrap_allows_a_blank_password(app):
    """Personal/local-network tool, one machine - a blank password is a
    deliberate, supported choice, not something bootstrap should block."""
    with app.app_context():
        ensure_admin_account(db, "Root Admin", "")
        admin = User.query.filter_by(role=ROLE_ADMIN).one()
        assert admin.check_password("")


# ------------------------------------------------------------- login/logout

def test_login_with_correct_credentials_succeeds(client):
    _make_coordinator()
    resp = _login(client, "Test Coordinator", "testpass123")
    assert resp.status_code == 200
    assert b"Your courses" in resp.data


def test_login_is_case_insensitive_on_name(client):
    _make_coordinator(name="Arushi Arunkumar", password="")
    resp = _login(client, "arushi arunkumar", "")
    assert resp.status_code == 200
    assert b"Your courses" in resp.data


def test_login_with_wrong_password_shows_error(client):
    _make_coordinator()
    resp = _login(client, "Test Coordinator", "wrong-password")
    assert resp.status_code == 200
    assert b"Incorrect name or password" in resp.data


def test_unauthenticated_request_redirects_to_login(client):
    _make_coordinator()
    resp = client.get("/courses/", follow_redirects=True)
    assert b"Incorrect name or password" not in resp.data
    assert b"name" in resp.data.lower() and b"password" in resp.data.lower()


def test_logout_ends_the_session(client):
    _make_coordinator()
    _login(client, "Test Coordinator", "testpass123")
    client.post("/logout", follow_redirects=True)
    resp = client.get("/courses/", follow_redirects=True)
    # Kicked back to the login screen again.
    assert b"password" in resp.data.lower()


# ------------------------------------------------------- coordinator-only

def test_teacher_gets_403_on_coordinator_only_routes(client):
    _make_coordinator()
    course = _make_course()
    _make_teacher()
    _login(client, "Test Teacher", "teachpass123")

    for path in (
        "/courses/new",
        f"/courses/{course.id}",
        f"/courses/{course.id}/edit",
        f"/courses/{course.id}/outcomes",
        f"/courses/{course.id}/program-outcomes",
        f"/courses/{course.id}/mapping",
        f"/courses/{course.id}/structure/",
        f"/courses/{course.id}/teachers",
        f"/courses/{course.id}/marks/roster",
        "/teachers",
    ):
        resp = client.get(path)
        assert resp.status_code == 403, f"expected 403 for {path}, got {resp.status_code}"


def test_manage_teachers_requires_coordinator(client):
    _make_coordinator()
    _make_teacher()
    _login(client, "Test Teacher", "teachpass123")
    resp = client.post("/teachers", data={"action": "add_teacher", "name": "x", "password": "abcdefg"})
    assert resp.status_code == 403
    assert User.query.count() == 2  # nothing new got created


# --------------------------------------------------- assigned teacher access

def test_teacher_can_view_marks_and_results_for_assigned_course(client):
    coordinator = _make_coordinator()
    course = _make_course()
    teacher = _make_teacher()
    db.session.add(CourseTeacher(course_id=course.id, teacher_id=teacher.id))
    db.session.commit()

    _login(client, "Test Teacher", "teachpass123")
    assert client.get(f"/courses/{course.id}/marks/").status_code == 200
    assert client.get(f"/courses/{course.id}/results/").status_code == 200


def test_teacher_can_enter_marks_for_assigned_course(client):
    course = _make_course()
    teacher = _make_teacher()
    _make_coordinator()
    db.session.add(CourseTeacher(course_id=course.id, teacher_id=teacher.id))
    db.session.commit()

    _login(client, "Test Teacher", "teachpass123")
    resp = client.post(
        f"/courses/{course.id}/marks/EXTERNAL",
        data={},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_teacher_gets_403_for_unassigned_course(client):
    _make_coordinator()
    course = _make_course("T1")
    other_course = _make_course("T2")
    teacher = _make_teacher()
    db.session.add(CourseTeacher(course_id=course.id, teacher_id=teacher.id))
    db.session.commit()

    _login(client, "Test Teacher", "teachpass123")
    assert client.get(f"/courses/{course.id}/marks/").status_code == 200
    assert client.get(f"/courses/{other_course.id}/marks/").status_code == 403
    assert client.get(f"/courses/{other_course.id}/results/").status_code == 403


def test_teacher_list_courses_only_shows_assigned(client):
    _make_coordinator()
    course = _make_course("T1")
    _make_course("T2")  # not assigned to the teacher
    teacher = _make_teacher()
    db.session.add(CourseTeacher(course_id=course.id, teacher_id=teacher.id))
    db.session.commit()

    _login(client, "Test Teacher", "teachpass123")
    resp = client.get("/courses/")
    assert b"T1" in resp.data
    assert b"T2" not in resp.data


# ------------------------------------------------------- coordinator manages teachers

def test_coordinator_can_add_teacher_account(client):
    _make_coordinator()
    _login(client, "Test Coordinator", "testpass123")

    resp = client.post("/teachers", data={
        "action": "add_teacher", "name": "New Teacher", "password": "abcdefgh",
    }, follow_redirects=True)
    assert resp.status_code == 200
    teacher = User.query.filter_by(display_name="New Teacher").first()
    assert teacher is not None
    assert teacher.role == ROLE_TEACHER


def test_coordinator_cannot_add_teacher_with_duplicate_name(client):
    _make_coordinator()
    _make_teacher(name="Priya Rao")
    _login(client, "Test Coordinator", "testpass123")

    resp = client.post("/teachers", data={
        "action": "add_teacher", "name": "priya rao", "password": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"already taken" in resp.data
    assert User.query.filter_by(role=ROLE_TEACHER).count() == 1


def test_coordinator_cannot_reset_a_teachers_password(client):
    """Only the Admin can reset a Teacher's password now (see
    test_admin_can_reset_a_coordinator_or_teacher_password below) - the
    Coordinator-facing /teachers page no longer exposes this action at
    all, so posting it is simply a no-op, not an error."""
    _make_coordinator()
    teacher = _make_teacher()
    original_hash = teacher.password_hash
    _login(client, "Test Coordinator", "testpass123")

    resp = client.post("/teachers", data={
        "action": "reset_password", "teacher_id": str(teacher.id), "new_password": "should-not-stick",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(User, teacher.id).password_hash == original_hash
    assert not db.session.get(User, teacher.id).check_password("should-not-stick")


# --------------------------------------------------- per-course teacher assignment

def test_coordinator_assigns_a_teacher_to_a_specific_course(client):
    """Course-scoped assignment (the Teachers tab on a course) replaces
    the old per-teacher course-picker - a coordinator just ticks which
    teacher(s) are assigned to *this* subject."""
    _make_coordinator()
    course = _make_course()
    teacher = _make_teacher()
    _login(client, "Test Coordinator", "testpass123")

    resp = client.get(f"/courses/{course.id}/teachers")
    assert resp.status_code == 200
    assert teacher.display_name.encode() in resp.data

    resp = client.post(f"/courses/{course.id}/teachers", data={
        "teacher_ids": [str(teacher.id)],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert CourseTeacher.query.filter_by(course_id=course.id, teacher_id=teacher.id).first() is not None

    # Unticking removes the assignment again.
    resp = client.post(f"/courses/{course.id}/teachers", data={}, follow_redirects=True)
    assert resp.status_code == 200
    assert CourseTeacher.query.filter_by(course_id=course.id, teacher_id=teacher.id).first() is None


def test_course_teachers_page_excludes_the_courses_own_coordinator(client):
    """The course's own coordinator is already credited automatically
    (Course.faculty_display) - they shouldn't also show up as a
    selectable "teacher" checkbox for their own course."""
    coordinator = _make_coordinator()
    course = _make_course()
    course.coordinator_id = coordinator.id
    db.session.commit()
    _login(client, "Test Coordinator", "testpass123")

    resp = client.get(f"/courses/{course.id}/teachers")
    assert resp.status_code == 200
    assert b"No other Teacher or Coordinator accounts exist yet" in resp.data


def test_a_coordinator_can_be_assigned_as_teacher_on_another_course(client):
    """A Coordinator running their own course(s) can also be picked up as
    a Teacher on someone else's course (they take a class there too)."""
    _make_coordinator(name="Owning Coordinator", password="ownerpass1")
    moonlighting = _make_coordinator(name="Moonlighting Coordinator", password="otherpass1")
    course = _make_course()
    course.coordinator_id = User.query.filter_by(display_name="Owning Coordinator").first().id
    db.session.commit()

    _login(client, "Owning Coordinator", "ownerpass1")
    resp = client.get(f"/courses/{course.id}/teachers")
    assert resp.status_code == 200
    assert b"Moonlighting Coordinator" in resp.data
    assert b"Coordinator</span>" in resp.data  # the role badge shows up next to their name

    resp = client.post(f"/courses/{course.id}/teachers", data={
        "teacher_ids": [str(moonlighting.id)],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert CourseTeacher.query.filter_by(course_id=course.id, teacher_id=moonlighting.id).first() is not None
    assert moonlighting.display_name in db.session.get(Course, course.id).faculty_display


# ------------------------------------------------- comma-separated teachers at course creation

def test_new_course_form_adds_teachers_from_comma_separated_names(client):
    """Point 4: teachers (like sections) can be added as a comma-
    separated list right on the course creation form - existing accounts
    get linked, new names get a fresh Teacher account."""
    _make_coordinator()
    _make_teacher(name="Existing Teacher")
    _login(client, "Test Coordinator", "testpass123")

    resp = client.post("/courses/new", data={
        "subject_code": "T-MULTI", "subject_name": "Multi Teacher Subject",
        "section": "A, B", "teachers": "Existing Teacher, Brand New Teacher",
    }, follow_redirects=True)
    assert resp.status_code == 200

    course = Course.query.filter_by(subject_code="T-MULTI").first()
    assert course is not None
    assert course.sections_list == ["A", "B"]

    assigned_names = {a.teacher.display_name for a in course.teacher_assignments}
    assert assigned_names == {"Existing Teacher", "Brand New Teacher"}

    new_teacher = User.query.filter_by(display_name="Brand New Teacher").first()
    assert new_teacher is not None
    assert new_teacher.role == ROLE_TEACHER


def test_adding_teachers_at_course_creation_never_duplicates_the_coordinator(client):
    """Typing the coordinator's own name in the "teachers to add" field
    should be a harmless no-op, not a duplicate/self CourseTeacher row -
    they're already credited automatically."""
    coordinator = _make_coordinator()
    _login(client, "Test Coordinator", "testpass123")

    client.post("/courses/new", data={
        "subject_code": "T-SELF", "subject_name": "Self Reference Subject",
        "teachers": "Test Coordinator",
    }, follow_redirects=True)

    course = Course.query.filter_by(subject_code="T-SELF").first()
    assert course is not None
    assert CourseTeacher.query.filter_by(course_id=course.id, teacher_id=coordinator.id).first() is None


# --------------------------------------------------------------- admin

def test_admin_login_lands_on_manage_users_not_course_list(client):
    _make_admin()
    resp = _login(client, "Test Admin", "adminpass123")
    assert resp.status_code == 200
    assert b"Add an account" in resp.data


def test_admin_visiting_course_list_gets_redirected_to_manage_users(client):
    _make_admin()
    _login(client, "Test Admin", "adminpass123")
    resp = client.get("/courses/", follow_redirects=True)
    assert b"Add an account" in resp.data


def test_admin_is_blocked_from_course_routes(client):
    """Admin is account governance only - it doesn't get a free pass into
    coordinator-only or teacher-only course routes just because it's a
    privileged account."""
    _make_admin()
    course = _make_course()
    _login(client, "Test Admin", "adminpass123")

    for path in (
        "/courses/new",
        f"/courses/{course.id}",
        f"/courses/{course.id}/teachers",
        f"/courses/{course.id}/marks/",
        f"/courses/{course.id}/results/",
        "/teachers",
    ):
        resp = client.get(path)
        assert resp.status_code == 403, f"expected 403 for {path}, got {resp.status_code}"


def test_non_admin_gets_403_on_manage_users(client):
    _make_admin()
    _make_coordinator()
    _login(client, "Test Coordinator", "testpass123")

    assert client.get("/admin/users").status_code == 403
    resp = client.post("/admin/users", data={"action": "add_user", "name": "x", "password": "", "role": "teacher"})
    assert resp.status_code == 403
    assert User.query.count() == 2  # nothing new got created


def test_admin_can_add_coordinator_and_teacher_accounts(client):
    _make_admin()
    _login(client, "Test Admin", "adminpass123")

    resp = client.post("/admin/users", data={
        "action": "add_user", "name": "New Coordinator", "password": "abcdefgh", "role": "coordinator",
    }, follow_redirects=True)
    assert resp.status_code == 200
    coordinator = User.query.filter_by(display_name="New Coordinator").first()
    assert coordinator is not None
    assert coordinator.role == ROLE_COORDINATOR

    resp = client.post("/admin/users", data={
        "action": "add_user", "name": "New Teacher", "password": "", "role": "teacher",
    }, follow_redirects=True)
    assert resp.status_code == 200
    teacher = User.query.filter_by(display_name="New Teacher").first()
    assert teacher is not None
    assert teacher.role == ROLE_TEACHER


def test_admin_add_user_rejects_duplicate_name(client):
    _make_admin()
    _make_coordinator(name="Priya Rao")
    _login(client, "Test Admin", "adminpass123")

    resp = client.post("/admin/users", data={
        "action": "add_user", "name": "priya rao", "password": "", "role": "teacher",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"already taken" in resp.data
    assert User.query.filter_by(role=ROLE_TEACHER).count() == 0


def test_admin_add_user_requires_a_role(client):
    _make_admin()
    _login(client, "Test Admin", "adminpass123")

    resp = client.post("/admin/users", data={
        "action": "add_user", "name": "No Role", "password": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert User.query.filter_by(display_name="No Role").first() is None


def test_admin_can_remove_coordinator_and_teacher_accounts(client):
    _make_admin()
    coordinator = _make_coordinator()
    teacher = _make_teacher()
    _login(client, "Test Admin", "adminpass123")

    resp = client.post(f"/admin/users/{coordinator.id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(User, coordinator.id) is None

    resp = client.post(f"/admin/users/{teacher.id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(User, teacher.id) is None


def test_admin_cannot_remove_the_admin_account_via_delete_user(client):
    """Belt-and-braces: even a direct POST to the delete route can't
    remove an Admin account - there's exactly one, and it isn't managed
    through this page at all."""
    admin = _make_admin()
    _login(client, "Test Admin", "adminpass123")

    resp = client.post(f"/admin/users/{admin.id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(User, admin.id) is not None


def test_admin_can_reset_a_coordinator_or_teacher_password(client):
    _make_admin()
    coordinator = _make_coordinator()
    _login(client, "Test Admin", "adminpass123")

    resp = client.post("/admin/users", data={
        "action": "reset_password", "user_id": str(coordinator.id), "new_password": "brand-new-pass",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(User, coordinator.id).check_password("brand-new-pass")


def test_manage_users_lists_coordinators_and_teachers_not_admin(client):
    _make_admin()
    _make_coordinator(name="Some Coordinator")
    _make_teacher(name="Some Teacher")
    _login(client, "Test Admin", "adminpass123")

    resp = client.get("/admin/users")
    assert b"Some Coordinator" in resp.data
    assert b"Some Teacher" in resp.data
    # The Admin's own name legitimately appears once, in the topbar - but
    # it should never get a manageable account card (no "Remove account"
    # for it), since there are only the 2 accounts (Coordinator + Teacher)
    # this page is allowed to manage.
    assert resp.data.count(b"Remove account") == 2
