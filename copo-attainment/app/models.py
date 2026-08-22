"""
Data model.

This mirrors the entities we found inside attainment template.xlsx, but
normalized into proper relational tables instead of "cells at a fixed
spreadsheet coordinate":

  Course                - one row per subject/semester/academic-year offering
  CourseOutcome (CO)     - COs for that course ("CO1".."CO5" in the template)
  ProgramOutcome (PO/PSO)- POs/PSOs listed for that course (PO1..PO12, PSOs)
  COPOMapping            - the correlation matrix cell (1-3, or unmapped)
  AssessmentComponent    - IA1 / IA2 / IA3 / ASQM / EXTERNAL / EXIT_SURVEY
  AssessmentItem          - a question/item within a component, tagged to CO(s)
                            with a max mark (used by IA*, ASQM, EXIT_SURVEY)
  ItemCOMapping           - which CO(s) an item is tagged to (the "Cijk" rows)
  Student                 - roster for a course
  Mark                    - a student's score on one AssessmentItem (IA/ASQM)
  ExternalResult           - a student's SEE (external) marks - entered as
                            totals, not per-question, matching the template
  ExitSurveyResponse      - a student's rating on one exit-survey question

Design note: unlike the spreadsheet (which links rows across sheets by
*position* - "row 12 in IA Test only is the same student as row 12 in ASQM
only" - which is exactly the kind of thing that breaks silently if a row
ever gets inserted/deleted), every fact here is tied to a real Student id.
That's a deliberate improvement, not just a translation.
"""
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db

# The three roles:
#   Admin       - account governance only. Adds/removes Coordinator and
#                 Teacher accounts (see /admin/users in auth_routes.py).
#                 Doesn't touch course data itself. There is exactly one
#                 Admin, created once from the ADMIN_NAME/ADMIN_PASSWORD
#                 env vars on app startup (see ensure_admin_account in
#                 app/migrations.py) - there's no web form that creates
#                 one, deliberately, since this app can now sit on a
#                 public URL and a public "claim the admin account" page
#                 would be a real hole.
#   Coordinator - sets everything up (COs, POs, mapping, targets,
#                 assessment structure, roster) for the courses they run.
#   Teacher     - narrower: entering marks and viewing results for
#                 courses they've been assigned to.
# See app/auth.py for how these are enforced.
ROLE_ADMIN = "admin"
ROLE_COORDINATOR = "coordinator"
ROLE_TEACHER = "teacher"
ROLES = (ROLE_ADMIN, ROLE_COORDINATOR, ROLE_TEACHER)


class Course(db.Model):
    __tablename__ = "course"

    id = db.Column(db.Integer, primary_key=True)

    institution_name = db.Column(db.String(200), nullable=False, default="")
    department = db.Column(db.String(200), nullable=False, default="")
    subject_code = db.Column(db.String(50), nullable=False)
    subject_name = db.Column(db.String(200), nullable=False)
    faculty_name = db.Column(db.String(200), nullable=False, default="")
    academic_year = db.Column(db.String(20), nullable=False, default="")
    semester = db.Column(db.String(20), nullable=False, default="")
    section = db.Column(db.String(10), nullable=False, default="")

    # The Unit Coordinator who owns this course - set automatically to
    # whichever Coordinator created it. Used for reporting ("faculty
    # handled") alongside the Teacher accounts assigned via
    # CourseTeacher; see faculty_display below. Nullable because courses
    # created before this field existed won't have one, and
    # can_access_course()'s "any Coordinator can access any course" rule
    # is unchanged by this - it's descriptive, not an access-control gate.
    coordinator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # Level 1/2/3 targets - "% of students" that must clear the cutoff.
    # These are the E14:E16 cells in 'set target form' (used, per the
    # template's own CO-attainment formula, as the ladder for the FINAL
    # blended CO attainment level - see engine.level_for_percentage).
    target_level1_pct = db.Column(db.Float, nullable=False, default=50.0)
    target_level2_pct = db.Column(db.Float, nullable=False, default=60.0)
    target_level3_pct = db.Column(db.Float, nullable=False, default=70.0)

    # The per-question "did this student clear it" cutoff for internal
    # assessments (IA + ASQM), e.g. 60%. This is F14 in 'set target form'.
    internal_marks_cutoff_pct = db.Column(db.Float, nullable=False, default=60.0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    outcomes = db.relationship(
        "CourseOutcome", back_populates="course",
        cascade="all, delete-orphan", order_by="CourseOutcome.seq",
    )
    program_outcomes = db.relationship(
        "ProgramOutcome", back_populates="course",
        cascade="all, delete-orphan", order_by="ProgramOutcome.seq",
    )
    components = db.relationship(
        "AssessmentComponent", back_populates="course",
        cascade="all, delete-orphan",
    )
    students = db.relationship(
        "Student", back_populates="course",
        cascade="all, delete-orphan", order_by="Student.seq",
    )

    teacher_assignments = db.relationship(
        "CourseTeacher", back_populates="course", cascade="all, delete-orphan",
    )

    coordinator = db.relationship("User", foreign_keys=[coordinator_id])

    def __repr__(self):
        return f"<Course {self.subject_code} {self.academic_year}>"

    @property
    def sections_list(self):
        """The section(s) this course covers, parsed from the comma-
        separated `section` field (e.g. "A, B, C" -> ["A", "B", "C"]).
        Empty list means the course doesn't split its roster by section
        at all - the common case for a course taught to just one class."""
        if not self.section:
            return []
        return [s.strip() for s in self.section.split(",") if s.strip()]

    @property
    def faculty_display(self):
        """Who taught this course, for reports (replaces a single free-
        text name): the Unit Coordinator plus every Teacher account
        assigned via CourseTeacher - each of them takes the course for
        at least one class. Falls back to the free-text faculty_name
        field when no accounts are linked yet, so older courses (or ones
        set up before any Teacher account existed) still show something
        rather than a blank report."""
        names = []
        if self.coordinator is not None and self.coordinator.display_name:
            names.append(self.coordinator.display_name)
        for assignment in self.teacher_assignments:
            name = assignment.teacher.display_name
            if name and name not in names:
                names.append(name)
        return ", ".join(names) if names else self.faculty_name


class CourseOutcome(db.Model):
    __tablename__ = "course_outcome"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    seq = db.Column(db.Integer, nullable=False)  # 1, 2, 3... display order
    code = db.Column(db.String(30), nullable=False)  # e.g. "CO1"
    description = db.Column(db.Text, nullable=True)

    course = db.relationship("Course", back_populates="outcomes")

    def __repr__(self):
        return f"<CO {self.code}>"


class ProgramOutcome(db.Model):
    __tablename__ = "program_outcome"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    seq = db.Column(db.Integer, nullable=False)
    code = db.Column(db.String(30), nullable=False)  # "PO1".."PO12","PSO1"...
    is_pso = db.Column(db.Boolean, nullable=False, default=False)
    description = db.Column(db.Text, nullable=True)

    course = db.relationship("Course", back_populates="program_outcomes")

    def __repr__(self):
        return f"<PO {self.code}>"


class COPOMapping(db.Model):
    """The correlation-matrix cell: how strongly a CO maps to a PO/PSO.

    correlation_level is 1-3 (NBA scale). A CO/PO pair with NO row here
    means "not mapped" - equivalent to the template's convention of
    leaving the cell blank / putting '-' instead of a formula.
    """
    __tablename__ = "co_po_mapping"

    id = db.Column(db.Integer, primary_key=True)
    co_id = db.Column(db.Integer, db.ForeignKey("course_outcome.id"), nullable=False)
    po_id = db.Column(db.Integer, db.ForeignKey("program_outcome.id"), nullable=False)
    correlation_level = db.Column(db.Integer, nullable=False)  # 1, 2, or 3

    co = db.relationship("CourseOutcome")
    po = db.relationship("ProgramOutcome")

    __table_args__ = (
        db.UniqueConstraint("co_id", "po_id", name="uq_co_po_pair"),
    )


# Assessment component types - matches the sheets in the spreadsheet.
COMPONENT_TYPES = ("IA1", "IA2", "IA3", "ASQM", "EXTERNAL", "EXIT_SURVEY")


class AssessmentComponent(db.Model):
    """One assessment instrument for the course: an internal test, the
    self-study/ASQM bucket, the external exam, or the exit survey.

    IA1/IA2/IA3 and ASQM and EXIT_SURVEY are item-based (have
    AssessmentItem rows). EXTERNAL is not - it's entered as per-student
    totals via ExternalResult, matching how the template's "External"
    sheet works (manual total entry, no per-question breakdown).
    """
    __tablename__ = "assessment_component"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # one of COMPONENT_TYPES
    name = db.Column(db.String(100), nullable=False)  # display label

    course = db.relationship("Course", back_populates="components")
    items = db.relationship(
        "AssessmentItem", back_populates="component",
        cascade="all, delete-orphan", order_by="AssessmentItem.seq",
    )

    __table_args__ = (
        db.UniqueConstraint("course_id", "type", name="uq_course_component_type"),
    )


class AssessmentItem(db.Model):
    """A single question/item within a component - e.g. "1A" in IA-1, or
    "Q3" in the exit survey. Tagged to one or more COs (ItemCOMapping) -
    this replaces the spreadsheet's two "Cijk" rows (a question mapped to
    up to two COs); here an item can map to any number of COs.

    max_marks applies to IA/ASQM items. Exit-survey items don't use
    max_marks (they're rated, not scored) - it stays null for those.

    main_question (1-4) only applies to IA1/IA2/IA3 items: a real IA
    paper is four main questions, each with sub-parts (a, b, c...) that
    this row represents one of. A main question's sub-parts must sum to
    20 marks max (enforced in app/routes/structure_routes.py at
    add-item time), and a student's final IA score only counts the
    higher-scoring of {main question 1, 2} plus the higher-scoring of
    {3, 4} - see app/engine.py's ia_effective_marks_lookup for where
    that either/or rule is actually applied. None for ASQM/EXIT_SURVEY
    items (no such grouping there) and for IA items created before this
    field existed (treated as ungrouped/uncapped - see engine.py).
    """
    __tablename__ = "assessment_item"

    id = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(db.Integer, db.ForeignKey("assessment_component.id"), nullable=False)
    seq = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(30), nullable=False)  # "1A", "Q3", ...
    max_marks = db.Column(db.Float, nullable=True)
    main_question = db.Column(db.Integer, nullable=True)

    component = db.relationship("AssessmentComponent", back_populates="items")
    co_links = db.relationship(
        "ItemCOMapping", back_populates="item", cascade="all, delete-orphan",
    )


class ItemCOMapping(db.Model):
    __tablename__ = "item_co_mapping"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("assessment_item.id"), nullable=False)
    co_id = db.Column(db.Integer, db.ForeignKey("course_outcome.id"), nullable=False)

    item = db.relationship("AssessmentItem", back_populates="co_links")
    co = db.relationship("CourseOutcome")

    __table_args__ = (
        db.UniqueConstraint("item_id", "co_id", name="uq_item_co_pair"),
    )


class Student(db.Model):
    __tablename__ = "student"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    seq = db.Column(db.Integer, nullable=False)  # roster order (like Sl.No.)
    usn = db.Column(db.String(30), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    # Which of the course's sections (see Course.sections_list) this
    # student belongs to - blank/None for a course that doesn't split
    # its roster by section at all. Purely informational/filtering; it
    # doesn't affect COs/POs/mapping/structure, which stay shared across
    # every section of a course.
    section = db.Column(db.String(10), nullable=True, default="")

    course = db.relationship("Course", back_populates="students")

    __table_args__ = (
        db.UniqueConstraint("course_id", "usn", name="uq_course_usn"),
    )


class Mark(db.Model):
    """A student's score on one IA/ASQM item. Absent students simply have
    no Mark row for that item (matches the template's "if absent, don't
    enter anything" instruction) rather than a marks_obtained=0, so the
    engine can tell "scored zero" apart from "didn't attempt"."""
    __tablename__ = "mark"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("assessment_item.id"), nullable=False)
    marks_obtained = db.Column(db.Float, nullable=False)

    student = db.relationship("Student")
    item = db.relationship("AssessmentItem")

    __table_args__ = (
        db.UniqueConstraint("student_id", "item_id", name="uq_student_item"),
    )


class ExternalResult(db.Model):
    """A student's SEE (external exam) result - entered as totals, exactly
    like the template's "External" sheet (Internal / External / Total /
    Result columns, filled in by hand)."""
    __tablename__ = "external_result"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    internal_total = db.Column(db.Float, nullable=True)
    external_marks = db.Column(db.Float, nullable=True)
    result = db.Column(db.String(5), nullable=True)  # "P" / "F"

    student = db.relationship("Student")

    __table_args__ = (
        db.UniqueConstraint("student_id", name="uq_external_student"),
    )


# The rating scale used by the exit survey, matching the template's
# "A Excellent / B Good / C Average / D Poor / E Very Poor"-style options.
# Only A and B count towards attainment (the template's COUNTIF looks for
# exactly "A   Excellent" / "B    Good").
EXIT_SURVEY_RATINGS = ("A", "B", "C", "D", "E")
EXIT_SURVEY_GOOD_RATINGS = ("A", "B")


class ExitSurveyResponse(db.Model):
    __tablename__ = "exit_survey_response"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("assessment_item.id"), nullable=False)
    rating = db.Column(db.String(2), nullable=False)  # one of EXIT_SURVEY_RATINGS

    student = db.relationship("Student")
    item = db.relationship("AssessmentItem")

    __table_args__ = (
        db.UniqueConstraint("student_id", "item_id", name="uq_survey_student_item"),
    )


class User(db.Model, UserMixin):
    """A login for this app: exactly one Admin (account governance -
    see ROLE_ADMIN above), however many Coordinator accounts run their
    own courses day to day, and however many Teacher accounts a
    Coordinator assigns to specific courses via CourseTeacher.

    UserMixin (from Flask-Login) supplies is_authenticated/is_active/
    is_anonymous/get_id so this plugs straight into login_user()/
    current_user/@login_required without extra boilerplate.
    """
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(200), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_TEACHER)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    course_assignments = db.relationship(
        "CourseTeacher", back_populates="teacher", cascade="all, delete-orphan",
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @classmethod
    def find_by_name(cls, name):
        """Case-insensitive lookup by display name - this is what login
        uses instead of a separate username, so "Arushi Arunkumar" and
        "arushi arunkumar" both find the same account. `name` is matched
        trimmed and case-folded."""
        name = (name or "").strip()
        if not name:
            return None
        return cls.query.filter(db.func.lower(cls.display_name) == name.lower()).first()

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_coordinator(self):
        return self.role == ROLE_COORDINATOR

    @property
    def is_teacher(self):
        return self.role == ROLE_TEACHER

    def can_access_course(self, course_id):
        """Coordinators can access every course; teachers only the ones
        they've been explicitly assigned to; Admin doesn't manage course
        data at all, so this is always False for an Admin account."""
        if self.is_coordinator:
            return True
        return any(a.course_id == course_id for a in self.course_assignments)

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


class CourseTeacher(db.Model):
    """One teacher's assignment to one course - what lets that teacher log
    in and see/enter marks for that course specifically, and nothing
    else. A teacher can be assigned to more than one course; a course can
    have more than one teacher assigned (co-teaching)."""
    __tablename__ = "course_teacher"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    course = db.relationship("Course", back_populates="teacher_assignments")
    teacher = db.relationship("User", back_populates="course_assignments")

    __table_args__ = (
        db.UniqueConstraint("course_id", "teacher_id", name="uq_course_teacher"),
    )
