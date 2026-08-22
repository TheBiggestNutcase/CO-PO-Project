"""
Calculation engine.

This is a direct, deliberately-faithful reimplementation of the formula
chain we reverse-engineered from attainment template.xlsx. Every function
here maps to a specific named region of the spreadsheet - see the comment
above each one. Keeping this file free of Flask/DB-session concerns (it
just takes plain model objects/lists in) makes it possible to unit-test
against known template behaviour without spinning up the whole app.

The formula chain, end to end:

  1. Per CO, per marks-based bucket (IA combined across IA1+IA2+IA3, and
     ASQM separately): for each student, sum marks scored / marks allotted
     across every *attempted* item tagged to that CO -> a percentage.
     "Attempted" = a Mark row exists (mirrors the template's rule: if a
     student is absent, leave the cell blank rather than entering 0, so
     that item doesn't count against them).
     Then: CO attainment % (for that bucket) = (% of attempting students
     who cleared the internal marks cutoff) -- 'IA Test only'!AT241 /
     'ASQM only'!AN238 equivalent.

  2. CIA (A) = average(IA-combined %, ASQM %)               -- CO attainment!E
     External (B) = % of students scoring >= class average, applied
     equally to every CO                                     -- External!F242
     Exit survey (C) = average of "% rated A/B" across the survey
     questions mapped to that CO                              -- Exit survey!J

  3. Blended CO attainment % (M) = 0.9 * (0.5*A + 0.5*B) + 0.1*C
     CO attainment Level (N) = 0/1/2/3 ladder against the course's
     Level 1/2/3 "% of students" targets, applied to M.
                                                    -- CO attainment!M,N

  4. PO/PSO attainment = correlation-weighted average of CO Levels (N),
     using only COs that are mapped (have a COPOMapping row) to that PO.
                                                    -- Course PO attainment!16
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models import (
    EXIT_SURVEY_GOOD_RATINGS,
)


def _mean(values):
    """Average of the non-None values, or None if there are none.
    Mirrors Excel AVERAGE()'s habit of ignoring blanks."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def level_for_percentage(pct: float | None, course) -> int | None:
    """The 0/1/2/3 ladder used throughout the template, e.g.
    'CO attainment'!N9 = IF(M9<Level1,0, IF(M9<Level2,1, IF(M9<Level3,2,3)))
    """
    if pct is None:
        return None
    if pct < course.target_level1_pct:
        return 0
    if pct < course.target_level2_pct:
        return 1
    if pct < course.target_level3_pct:
        return 2
    return 3


IA_EITHER_OR_PAIRS = ((1, 2), (3, 4))


def ia_group_totals(items, marks_lookup, student_id):
    """dict[main_question] -> summed marks_obtained across that main
    question's sub-part items, for one student - only counting items
    with an actual Mark row (mirrors the rest of this module's
    "attempted" rule: a blank cell doesn't count as a zero). Items with
    no main_question set (legacy, pre-dating this grouping - see
    AssessmentItem.main_question) are excluded from the grouping
    entirely, not folded into any group."""
    totals = {}
    for item in items:
        if item.main_question is None:
            continue
        mark = marks_lookup.get((student_id, item.id))
        if mark is None:
            continue
        totals[item.main_question] = totals.get(item.main_question, 0.0) + mark
    return totals


def ia_dropped_item_ids(component_items, marks_lookup, student_id):
    """Which item ids to EXCLUDE for this student within one IA
    component, under the paper's either/or design: of main questions
    {1, 2}, only the higher-scoring one counts; same for {3, 4} - so a
    student who (incorrectly) answered both members of a pair still
    only gets credit for the better one, exactly like a real examiner
    only grading the required 2 of 4. Ties keep the lower-numbered
    question (arbitrary but deterministic).

    Returns an empty set - i.e. no filtering at all - for a component
    that has no main_question-grouped items yet, so pre-existing data
    from before this feature existed keeps behaving exactly as it did.
    """
    if not any(i.main_question is not None for i in component_items):
        return set()

    totals = ia_group_totals(component_items, marks_lookup, student_id)
    dropped_ids = set()
    for lower, higher in IA_EITHER_OR_PAIRS:
        lower_total = totals.get(lower, 0.0)
        higher_total = totals.get(higher, 0.0)
        loser = higher if lower_total >= higher_total else lower
        dropped_ids.update(item.id for item in component_items if item.main_question == loser)
    return dropped_ids


def ia_effective_marks_lookup(course, marks_lookup):
    """A copy of `marks_lookup` with the dropped side of each IA
    either/or pair (see ia_dropped_item_ids) removed for every student,
    for every IA1/IA2/IA3 component. ASQM, Exit Survey, and any IA
    component with no grouped items pass through completely unchanged.
    Downstream CO-attainment code then just sums whatever's left in the
    lookup, with no need to know the grouping/dropping rule exists at
    all - a dropped item simply looks identical to "not attempted."""
    effective = dict(marks_lookup)
    for component in course.components:
        if component.type not in ("IA1", "IA2", "IA3") or not component.items:
            continue
        for student in course.students:
            for item_id in ia_dropped_item_ids(component.items, marks_lookup, student.id):
                effective.pop((student.id, item_id), None)
    return effective


def marks_based_co_attainment(students, items, marks_lookup, cutoff_pct):
    """CO attainment % for one marks-based bucket (IA-combined or ASQM),
    restricted to `items` (already filtered to the items tagged to a
    single CO).

    marks_lookup: dict[(student_id, item_id)] -> marks_obtained

    Matches, e.g.:
      'IA Test only'!AT239  = COUNT of students who attempted the CO's questions
      'IA Test only'!AT240  = COUNT of those who scored >= cutoff
      'IA Test only'!AT241  = AT240/AT239*100
    """
    if not items:
        return None

    attempted = 0
    passed = 0
    for student in students:
        scored = 0.0
        allotted = 0.0
        did_attempt = False
        for item in items:
            mark = marks_lookup.get((student.id, item.id))
            if mark is not None:
                did_attempt = True
                scored += mark
                allotted += (item.max_marks or 0.0)
        if did_attempt and allotted > 0:
            attempted += 1
            pct = scored / allotted * 100
            if pct >= cutoff_pct:
                passed += 1

    if attempted == 0:
        return None
    return passed / attempted * 100


def external_co_attainment(students, external_results_by_student):
    """External (SEE) attainment %, applied equally to every CO - matches
    External!F238:F242 ("Equal Contribution of External to all COs").

    external_results_by_student: dict[student_id] -> ExternalResult
    """
    considered = [
        r for r in external_results_by_student.values()
        if r.result in ("P", "F") and r.external_marks is not None
    ]
    total_appeared = len(considered)
    if total_appeared == 0:
        return None

    class_average = sum(r.external_marks for r in considered) / total_appeared

    above_average = sum(
        1 for r in external_results_by_student.values()
        if r.external_marks is not None and r.external_marks >= class_average
    )
    return above_average / total_appeared * 100


def exit_survey_item_pct(item_id, responses_by_item):
    """% of respondents who rated A or B on one survey question.
    Matches Exit survey!E238 = COUNTIF(A,B)/COUNTA(responses)*100."""
    responses = responses_by_item.get(item_id, [])
    if not responses:
        return None
    good = sum(1 for r in responses if r in EXIT_SURVEY_GOOD_RATINGS)
    return good / len(responses) * 100


def exit_survey_co_attainment(items_mapped_to_co, responses_by_item):
    """CO-level exit-survey attainment = average of the mapped questions'
    "% rated A/B". Matches Exit survey!J248 = SUM(item%*mapped)/COUNT(mapped)."""
    pcts = [
        p for p in (
            exit_survey_item_pct(item.id, responses_by_item)
            for item in items_mapped_to_co
        )
        if p is not None
    ]
    if not pcts:
        return None
    return sum(pcts) / len(pcts)


@dataclass
class COAttainmentRow:
    co_id: int
    co_code: str
    ia_pct: float | None = None          # A1
    asqm_pct: float | None = None        # A2
    cia_pct: float | None = None         # A = avg(A1, A2)
    external_pct: float | None = None    # B
    exit_survey_pct: float | None = None  # C
    blended_pct: float | None = None     # M = 0.9*(0.5A+0.5B) + 0.1*C
    level: int | None = None             # N


@dataclass
class POAttainmentRow:
    po_id: int
    po_code: str
    is_pso: bool
    attainment: float | None = None      # correlation-weighted avg of CO levels
    mapped_co_count: int = 0


@dataclass
class CourseAttainmentResult:
    co_rows: list[COAttainmentRow] = field(default_factory=list)
    po_rows: list[POAttainmentRow] = field(default_factory=list)


def compute_course_attainment(course) -> CourseAttainmentResult:
    """Top-level entry point: given a fully-related Course, compute the
    CO attainment table and the PO/PSO attainment table, exactly
    reproducing the 'CO attainment' and 'Course PO attainment' sheets."""

    from app.models import Mark, ExitSurveyResponse, ExternalResult, COPOMapping

    students = list(course.students)
    student_ids = [s.id for s in students]

    # Pool IA1+IA2+IA3 items together, matching the template's combined
    # SUMIF range (D7:AO7) which spans all three tests as one CO bucket.
    ia_items, asqm_items, exit_items = [], [], []
    for component in course.components:
        if component.type in ("IA1", "IA2", "IA3"):
            ia_items.extend(component.items)
        elif component.type == "ASQM":
            asqm_items.extend(component.items)
        elif component.type == "EXIT_SURVEY":
            exit_items.extend(component.items)

    marks_lookup: dict[tuple[int, int], float] = {
        (m.student_id, m.item_id): m.marks_obtained
        for m in Mark.query.filter(Mark.student_id.in_(student_ids)).all()
    } if student_ids else {}
    # Apply the IA either/or grading rule (see ia_effective_marks_lookup)
    # before anything downstream sums marks per CO, so a dropped main
    # question never contributes to attainment.
    marks_lookup = ia_effective_marks_lookup(course, marks_lookup)

    responses_by_item: dict[int, list[str]] = {item.id: [] for item in exit_items}
    if exit_items:
        exit_item_ids = [item.id for item in exit_items]
        for response in ExitSurveyResponse.query.filter(
            ExitSurveyResponse.item_id.in_(exit_item_ids)
        ).all():
            responses_by_item[response.item_id].append(response.rating)

    external_results_by_student = {
        r.student_id: r
        for r in ExternalResult.query.filter(
            ExternalResult.student_id.in_(student_ids)
        ).all()
    } if student_ids else {}

    external_pct = external_co_attainment(students, external_results_by_student)

    result = CourseAttainmentResult()

    for co in course.outcomes:
        ia_for_co = [item for item in ia_items if any(link.co_id == co.id for link in item.co_links)]
        asqm_for_co = [item for item in asqm_items if any(link.co_id == co.id for link in item.co_links)]
        exit_for_co = [item for item in exit_items if any(link.co_id == co.id for link in item.co_links)]

        ia_pct = marks_based_co_attainment(students, ia_for_co, marks_lookup, course.internal_marks_cutoff_pct)
        asqm_pct = marks_based_co_attainment(students, asqm_for_co, marks_lookup, course.internal_marks_cutoff_pct)
        cia_pct = _mean([ia_pct, asqm_pct])
        exit_pct = exit_survey_co_attainment(exit_for_co, responses_by_item)

        # D = 50% of CIA, E = 50% of External, F = D+E, G = 90% of F,
        # H = 10% of exit survey, blended (M) = G + H.
        # Deliberate deviation from the template: if CIA or External is
        # simply missing (no data entered yet), the template would show
        # #DIV/0! and poison the whole blend. Here we treat a missing
        # input as 0 contribution instead, so a partially-filled course
        # still produces a (clearly partial) number rather than a wall of
        # errors. Once both CIA and External are entered this behaves
        # identically to the spreadsheet.
        d = 0.5 * cia_pct if cia_pct is not None else 0.0
        e = 0.5 * external_pct if external_pct is not None else 0.0
        f = d + e
        g = 0.9 * f
        h = 0.1 * exit_pct if exit_pct is not None else 0.0
        blended = g + h if (cia_pct is not None or external_pct is not None or exit_pct is not None) else None

        row = COAttainmentRow(
            co_id=co.id,
            co_code=co.code,
            ia_pct=ia_pct,
            asqm_pct=asqm_pct,
            cia_pct=cia_pct,
            external_pct=external_pct,
            exit_survey_pct=exit_pct,
            blended_pct=blended,
            level=level_for_percentage(blended, course),
        )
        result.co_rows.append(row)

    co_level_by_id = {row.co_id: row.level for row in result.co_rows}

    for po in course.program_outcomes:
        weighted_sum = 0.0
        weight_sum = 0.0
        mapped_count = 0
        for mapping in COPOMapping.query.filter_by(po_id=po.id).all():
            level = co_level_by_id.get(mapping.co_id)
            if level is None:
                continue
            weighted_sum += level * mapping.correlation_level
            weight_sum += mapping.correlation_level
            mapped_count += 1

        attainment = (weighted_sum / weight_sum) if weight_sum > 0 else None
        result.po_rows.append(POAttainmentRow(
            po_id=po.id,
            po_code=po.code,
            is_pso=po.is_pso,
            attainment=attainment,
            mapped_co_count=mapped_count,
        ))

    return result
