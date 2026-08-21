"""
Auth / role-enforcement tests: the one-time coordinator bootstrap, login/
logout, and that a Teacher account is actually confined to "enter marks and
view results" for courses they've been assigned to - nothing else.

Login is by name (case-insensitive), not a separate username - see
User.find_by_name in app/models.py.
"""
import pytest

from app import create_app
from app.extensions import db
from app.models import Course, User, CourseTeacher, ROLE_COORDINATOR, ROLE_TEACHER


@pytest.fixture()
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
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


def _make_course(subject_code="T1"):
    course = Course(subject_code=subject_code, subject_name="Test Subject")
    db.session.add(course)
    db.session.commit()
    return course


def _login(client, name, password):
    return client.post("/login", data={"name": name, "password": password}, follow_redirects=True)


# ------------------------------------------------------------- bootstrap

def test_no_users_redirects_to_setup_coordinator(client):
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    assert b"coordinator" in resp.data.lower()


def test_setup_coordinator_creates_account_and_logs_in(client):
    resp = client.post("/setup-coordinator", data={
        "name": "Coordinator Name", "password": "testpass123", "confirm_password": "testpass123",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert User.query.count() == 1
    assert User.query.first().role == ROLE_COORDINATOR
    # Setup logged us straight in - we should land on the (empty) course list.
    assert b"No courses yet" in resp.data


def test_setup_coordinator_allows_a_blank_password(client):
    """Personal/local-network tool, one machine - a blank password is a
    deliberate, supported choice, not something the form should block."""
    resp = client.post("/setup-coordinator", data={
        "name": "Co", "password": "", "confirm_password": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    user = User.query.filter_by(display_name="Co").first()
    assert user is not None
    assert user.check_password("")

    client.post("/logout", follow_redirects=True)
    resp = _login(client, "Co", "")
    assert resp.status_code == 200
    assert b"Your courses" in resp.data


def test_setup_coordinator_unreachable_once_a_user_exists(client):
    _make_coordinator()
    resp = client.get("/setup-coordinator", follow_redirects=True)
    assert b"Log in" in resp.data or b"log in" in resp.data.lower()
    assert User.query.count() == 1


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

def test_coordinator_can_add_teacher_and_assign_course(client):
    _make_coordinator()
    course = _make_course()
    _login(client, "Test Coordinator", "testpass123")

    resp = client.post("/teachers", data={
        "action": "add_teacher", "name": "New Teacher", "password": "abcdefgh",
    }, follow_redirects=True)
    assert resp.status_code == 200
    teacher = User.query.filter_by(display_name="New Teacher").first()
    assert teacher is not None
    assert teacher.role == ROLE_TEACHER

    resp = client.post("/teachers", data={
        "action": "update_assignments", "teacher_id": str(teacher.id),
        "course_ids": [str(course.id)],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert CourseTeacher.query.filter_by(course_id=course.id, teacher_id=teacher.id).first() is not None


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
