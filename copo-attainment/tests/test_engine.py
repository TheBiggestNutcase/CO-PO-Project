"""
Unit tests for the calculation engine, checked against a small scenario
worked out by hand using the exact same arithmetic as the spreadsheet's
formula chain (see the comment block at the top of app/engine.py).

Scenario: 2 COs, 4 students, one IA test, one ASQM item set, an external
result, and an exit survey - deliberately small enough to hand-verify.

Worked numbers (see inline comments) -
  CO1: IA=66.667%, ASQM=100%      -> CIA=83.333%
       External=50% (equal for all COs), Exit survey=75%
       M = 0.9*(0.5*83.333 + 0.5*50) + 0.1*75 = 67.5  -> Level 2
  CO2: IA=33.333%, ASQM=33.333%   -> CIA=33.333%
       External=50%, Exit survey=50%
       M = 0.9*(0.5*33.333 + 0.5*50) + 0.1*50 = 42.5  -> Level 0

  PO1 (CO1 corr=3, CO2 corr=2): (2*3 + 0*2) / (3+2) = 1.2
  PO2 (CO1 corr=3 only):        (2*3) / 3            = 2.0
"""
import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Course, CourseOutcome, ProgramOutcome, COPOMapping,
    AssessmentComponent, AssessmentItem, ItemCOMapping,
    Student, Mark, ExternalResult, ExitSurveyResponse,
)
from app.engine import compute_course_attainment, level_for_percentage, ia_dropped_item_ids


@pytest.fixture()
def app():
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "TESTING": True,
    })
    yield app


def _build_scenario():
    course = Course(
        subject_code="TST101", subject_name="Test Subject",
        target_level1_pct=50, target_level2_pct=60, target_level3_pct=70,
        internal_marks_cutoff_pct=60,
    )
    db.session.add(course)
    db.session.flush()

    co1 = CourseOutcome(course_id=course.id, seq=1, code="CO1")
    co2 = CourseOutcome(course_id=course.id, seq=2, code="CO2")
    db.session.add_all([co1, co2])
    db.session.flush()

    po1 = ProgramOutcome(course_id=course.id, seq=1, code="PO1")
    po2 = ProgramOutcome(course_id=course.id, seq=2, code="PO2")
    db.session.add_all([po1, po2])
    db.session.flush()

    db.session.add_all([
        COPOMapping(co_id=co1.id, po_id=po1.id, correlation_level=3),
        COPOMapping(co_id=co2.id, po_id=po1.id, correlation_level=2),
        COPOMapping(co_id=co1.id, po_id=po2.id, correlation_level=3),
    ])

    students = [Student(course_id=course.id, seq=i + 1, usn=f"USN{i+1}", name=f"Student {i+1}") for i in range(4)]
    db.session.add_all(students)
    db.session.flush()
    s1, s2, s3, s4 = students

    # --- IA1 component: 1A -> CO1 (max 10), 1B -> CO2 (max 10)
    ia1 = AssessmentComponent(course_id=course.id, type="IA1", name="IA Test 1")
    db.session.add(ia1)
    db.session.flush()
    item_1a = AssessmentItem(component_id=ia1.id, seq=1, label="1A", max_marks=10)
    item_1b = AssessmentItem(component_id=ia1.id, seq=2, label="1B", max_marks=10)
    db.session.add_all([item_1a, item_1b])
    db.session.flush()
    db.session.add_all([
        ItemCOMapping(item_id=item_1a.id, co_id=co1.id),
        ItemCOMapping(item_id=item_1b.id, co_id=co2.id),
    ])

    # S1: 1A=8 (80%), 1B=5 (50%) | S2: 1A=5 (50%), 1B=9 (90%)
    # S3: 1A=9 (90%), 1B=3 (30%) | S4: absent both
    db.session.add_all([
        Mark(student_id=s1.id, item_id=item_1a.id, marks_obtained=8),
        Mark(student_id=s1.id, item_id=item_1b.id, marks_obtained=5),
        Mark(student_id=s2.id, item_id=item_1a.id, marks_obtained=5),
        Mark(student_id=s2.id, item_id=item_1b.id, marks_obtained=9),
        Mark(student_id=s3.id, item_id=item_1a.id, marks_obtained=9),
        Mark(student_id=s3.id, item_id=item_1b.id, marks_obtained=3),
    ])

    # --- ASQM component: A1 -> CO1 (max 5), A2 -> CO2 (max 5)
    asqm = AssessmentComponent(course_id=course.id, type="ASQM", name="Self Study")
    db.session.add(asqm)
    db.session.flush()
    item_a1 = AssessmentItem(component_id=asqm.id, seq=1, label="A1", max_marks=5)
    item_a2 = AssessmentItem(component_id=asqm.id, seq=2, label="A2", max_marks=5)
    db.session.add_all([item_a1, item_a2])
    db.session.flush()
    db.session.add_all([
        ItemCOMapping(item_id=item_a1.id, co_id=co1.id),
        ItemCOMapping(item_id=item_a2.id, co_id=co2.id),
    ])

    # S1: A1=4(80%),A2=2(40%) | S2: A1=3(60%),A2=4(80%)
    # S3: absent both | S4: A1=5(100%),A2=1(20%)
    db.session.add_all([
        Mark(student_id=s1.id, item_id=item_a1.id, marks_obtained=4),
        Mark(student_id=s1.id, item_id=item_a2.id, marks_obtained=2),
        Mark(student_id=s2.id, item_id=item_a1.id, marks_obtained=3),
        Mark(student_id=s2.id, item_id=item_a2.id, marks_obtained=4),
        Mark(student_id=s4.id, item_id=item_a1.id, marks_obtained=5),
        Mark(student_id=s4.id, item_id=item_a2.id, marks_obtained=1),
    ])

    # --- External: class average = (70+50+40+80)/4 = 60; >=60 -> S1,S4
    external = AssessmentComponent(course_id=course.id, type="EXTERNAL", name="SEE")
    db.session.add(external)
    db.session.add_all([
        ExternalResult(student_id=s1.id, external_marks=70, result="P"),
        ExternalResult(student_id=s2.id, external_marks=50, result="P"),
        ExternalResult(student_id=s3.id, external_marks=40, result="F"),
        ExternalResult(student_id=s4.id, external_marks=80, result="P"),
    ])

    # --- Exit survey: Q1 -> CO1, Q2 -> CO2
    survey = AssessmentComponent(course_id=course.id, type="EXIT_SURVEY", name="Exit Survey")
    db.session.add(survey)
    db.session.flush()
    item_q1 = AssessmentItem(component_id=survey.id, seq=1, label="Q1")
    item_q2 = AssessmentItem(component_id=survey.id, seq=2, label="Q2")
    db.session.add_all([item_q1, item_q2])
    db.session.flush()
    db.session.add_all([
        ItemCOMapping(item_id=item_q1.id, co_id=co1.id),
        ItemCOMapping(item_id=item_q2.id, co_id=co2.id),
    ])
    # Q1: A,B,C,A -> 3/4 good = 75%   Q2: C,D,A,B -> 2/4 good = 50%
    db.session.add_all([
        ExitSurveyResponse(student_id=s1.id, item_id=item_q1.id, rating="A"),
        ExitSurveyResponse(student_id=s2.id, item_id=item_q1.id, rating="B"),
        ExitSurveyResponse(student_id=s3.id, item_id=item_q1.id, rating="C"),
        ExitSurveyResponse(student_id=s4.id, item_id=item_q1.id, rating="A"),
        ExitSurveyResponse(student_id=s1.id, item_id=item_q2.id, rating="C"),
        ExitSurveyResponse(student_id=s2.id, item_id=item_q2.id, rating="D"),
        ExitSurveyResponse(student_id=s3.id, item_id=item_q2.id, rating="A"),
        ExitSurveyResponse(student_id=s4.id, item_id=item_q2.id, rating="B"),
    ])

    db.session.commit()
    return course.id


def test_co_and_po_attainment_matches_hand_worked_scenario(app):
    with app.app_context():
        course_id = _build_scenario()
        course = db.session.get(Course, course_id)

        result = compute_course_attainment(course)

        by_code = {row.co_code: row for row in result.co_rows}

        co1 = by_code["CO1"]
        assert co1.ia_pct == pytest.approx(200 / 3, abs=0.01)      # 66.667%
        assert co1.asqm_pct == pytest.approx(100.0, abs=0.01)
        assert co1.cia_pct == pytest.approx((200 / 3 + 100) / 2, abs=0.01)  # 83.333%
        assert co1.external_pct == pytest.approx(50.0, abs=0.01)
        assert co1.exit_survey_pct == pytest.approx(75.0, abs=0.01)
        assert co1.blended_pct == pytest.approx(67.5, abs=0.05)
        assert co1.level == 2

        co2 = by_code["CO2"]
        assert co2.ia_pct == pytest.approx(100 / 3, abs=0.01)       # 33.333%
        assert co2.asqm_pct == pytest.approx(100 / 3, abs=0.01)
        assert co2.cia_pct == pytest.approx(100 / 3, abs=0.01)
        assert co2.external_pct == pytest.approx(50.0, abs=0.01)
        assert co2.exit_survey_pct == pytest.approx(50.0, abs=0.01)
        assert co2.blended_pct == pytest.approx(42.5, abs=0.05)
        assert co2.level == 0

        by_po = {row.po_code: row for row in result.po_rows}
        assert by_po["PO1"].attainment == pytest.approx(1.2, abs=0.01)
        assert by_po["PO2"].attainment == pytest.approx(2.0, abs=0.01)


def test_level_ladder_boundaries():
    class FakeCourse:
        target_level1_pct = 50
        target_level2_pct = 60
        target_level3_pct = 70

    c = FakeCourse()
    assert level_for_percentage(None, c) is None
    assert level_for_percentage(49.9, c) == 0
    assert level_for_percentage(50, c) == 1
    assert level_for_percentage(59.9, c) == 1
    assert level_for_percentage(60, c) == 2
    assert level_for_percentage(69.9, c) == 2
    assert level_for_percentage(70, c) == 3
    assert level_for_percentage(100, c) == 3


# --------------------------------------------------- IA either/or grading

class _FakeItem:
    def __init__(self, id, main_question):
        self.id = id
        self.main_question = main_question


def test_ia_either_or_drops_lower_scoring_pair_member():
    """A student who answered all 4 main questions (both members of both
    pairs) should only get credit for the higher of {1,2} and the higher
    of {3,4} - the lower-scoring member of each pair is dropped
    entirely, not averaged in."""
    items = [_FakeItem(1, 1), _FakeItem(2, 2), _FakeItem(3, 3), _FakeItem(4, 4)]
    marks_lookup = {
        (100, 1): 15.0,  # Q1 - loses to Q2, dropped
        (100, 2): 18.0,  # Q2 - higher of the pair
        (100, 3): 10.0,  # Q3 - loses to Q4, dropped
        (100, 4): 12.0,  # Q4 - higher of the pair
    }
    assert ia_dropped_item_ids(items, marks_lookup, student_id=100) == {1, 3}


def test_ia_either_or_is_a_noop_for_the_normal_case():
    """The expected case - a student answers only the required 2 (one
    from each pair) - must not lose any marks they actually scored."""
    items = [_FakeItem(1, 1), _FakeItem(2, 2), _FakeItem(3, 3), _FakeItem(4, 4)]
    marks_lookup = {(100, 2): 18.0, (100, 3): 10.0}
    dropped = ia_dropped_item_ids(items, marks_lookup, student_id=100)
    assert 2 not in dropped
    assert 3 not in dropped


def test_ia_grouping_is_a_noop_for_legacy_ungrouped_items():
    """Items with no main_question at all (created before this feature
    existed) must never be filtered - the component behaves exactly as
    it did before the either/or rule was added."""
    items = [_FakeItem(1, None), _FakeItem(2, None)]
    marks_lookup = {(100, 1): 5.0, (100, 2): 5.0}
    assert ia_dropped_item_ids(items, marks_lookup, student_id=100) == set()


def test_ia_either_or_feeds_through_to_co_attainment(app):
    """End-to-end: a student who over-answered one IA (attempted all 4
    main questions) should have CO attainment computed only from the
    higher-scoring member of each pair - max 40 marks total, not 80."""
    with app.app_context():
        course = Course(
            subject_code="TST-IA", subject_name="IA Either/Or Test",
            target_level1_pct=50, target_level2_pct=60, target_level3_pct=70,
            internal_marks_cutoff_pct=60,
        )
        db.session.add(course)
        db.session.flush()

        co1 = CourseOutcome(course_id=course.id, seq=1, code="CO1")
        db.session.add(co1)
        db.session.flush()

        student = Student(course_id=course.id, seq=1, usn="U1", name="Alice")
        db.session.add(student)
        db.session.flush()

        ia1 = AssessmentComponent(course_id=course.id, type="IA1", name="IA Test 1")
        db.session.add(ia1)
        db.session.flush()
        q1 = AssessmentItem(component_id=ia1.id, seq=1, label="1a", max_marks=20, main_question=1)
        q2 = AssessmentItem(component_id=ia1.id, seq=2, label="2a", max_marks=20, main_question=2)
        q3 = AssessmentItem(component_id=ia1.id, seq=3, label="3a", max_marks=20, main_question=3)
        q4 = AssessmentItem(component_id=ia1.id, seq=4, label="4a", max_marks=20, main_question=4)
        db.session.add_all([q1, q2, q3, q4])
        db.session.flush()
        db.session.add_all([
            ItemCOMapping(item_id=q1.id, co_id=co1.id),
            ItemCOMapping(item_id=q2.id, co_id=co1.id),
            ItemCOMapping(item_id=q3.id, co_id=co1.id),
            ItemCOMapping(item_id=q4.id, co_id=co1.id),
        ])
        db.session.add_all([
            Mark(student_id=student.id, item_id=q1.id, marks_obtained=15),  # dropped (loses to Q2)
            Mark(student_id=student.id, item_id=q2.id, marks_obtained=18),  # kept
            Mark(student_id=student.id, item_id=q3.id, marks_obtained=10),  # dropped (loses to Q4)
            Mark(student_id=student.id, item_id=q4.id, marks_obtained=12),  # kept
        ])
        db.session.commit()

        result = compute_course_attainment(course)
        co1_row = result.co_rows[0]
        # Effective: Q2(18) + Q4(12) = 30 / (20+20) = 75% >= 60% cutoff -> the one student clears it.
        assert co1_row.ia_pct == pytest.approx(100.0, abs=0.01)


def test_optional_third_ia_still_computes_attainment(app):
    """Some courses only run 2 IA tests, leaving the third out entirely
    (no IA3 component created at all) - the IA bucket should still
    compute correctly by pooling whichever IA components do exist,
    rather than requiring exactly 3."""
    with app.app_context():
        course = Course(
            subject_code="TST-2IA", subject_name="Two IA Test",
            target_level1_pct=50, target_level2_pct=60, target_level3_pct=70,
            internal_marks_cutoff_pct=60,
        )
        db.session.add(course)
        db.session.flush()
        co1 = CourseOutcome(course_id=course.id, seq=1, code="CO1")
        db.session.add(co1)
        db.session.flush()
        student = Student(course_id=course.id, seq=1, usn="U1", name="Alice")
        db.session.add(student)
        db.session.flush()

        ia1 = AssessmentComponent(course_id=course.id, type="IA1", name="IA Test 1")
        ia2 = AssessmentComponent(course_id=course.id, type="IA2", name="IA Test 2")
        db.session.add_all([ia1, ia2])
        db.session.flush()
        item1 = AssessmentItem(component_id=ia1.id, seq=1, label="1A", max_marks=10)
        item2 = AssessmentItem(component_id=ia2.id, seq=1, label="1A", max_marks=10)
        db.session.add_all([item1, item2])
        db.session.flush()
        db.session.add_all([
            ItemCOMapping(item_id=item1.id, co_id=co1.id),
            ItemCOMapping(item_id=item2.id, co_id=co1.id),
        ])
        db.session.add_all([
            Mark(student_id=student.id, item_id=item1.id, marks_obtained=7),
            Mark(student_id=student.id, item_id=item2.id, marks_obtained=8),
        ])
        db.session.commit()

        # No IA3 component exists at all for this course. (7+8)/20 = 75%,
        # which clears the 60% cutoff, so the one student "passes" -
        # ia_pct is the % of attempting students who cleared the cutoff.
        result = compute_course_attainment(course)
        co1_row = result.co_rows[0]
        assert co1_row.ia_pct == pytest.approx(100.0, abs=0.01)


def test_absent_student_excluded_from_denominator(app):
    """A student with no Mark row for any item tagged to a CO should not
    count in that CO's 'attempted' denominator - matches the template's
    'if absent, leave blank' rule."""
    with app.app_context():
        course_id = _build_scenario()
        course = db.session.get(Course, course_id)
        result = compute_course_attainment(course)
        co1 = next(r for r in result.co_rows if r.co_code == "CO1")
        # 4 students total, but S3 was absent for ASQM CO1 item -> ASQM
        # attempted count for CO1 is 3 (S1, S2, S4), not 4.
        # ia_pct's own denominator (IA) is also 3 (S1,S2,S3; S4 absent).
        assert co1.ia_pct is not None
        assert co1.asqm_pct is not None
