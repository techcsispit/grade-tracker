"""Calculation module for academic grades, GPA, and averages.

Numerical policy
----------------
1. Percentages are calculated at full precision.
2. Letter grades are determined from the *unrounded* percentage, never from
   a rounded display percentage.
3. Individual percentages are never rounded before being averaged into a
   subject average.
4. Intermediate weighted grade-point totals and credit totals are never
   rounded; only the final user-facing result is rounded, at the existing
   documented output precision (2 decimals for SGPA/CGPA/subject average,
   1 decimal for displayed percentages in the CLI/report).
5. Letter-grade thresholds (90/80/70/60) are unchanged.

Why Decimal is used for classification
--------------------------------------
Binary floating-point cannot represent most decimal fractions exactly.
Computing ``(score / max_score) * 100`` with ``float`` can land a hair
*below* a threshold even when the values are exactly at that threshold in
decimal (e.g. ``8.1 / 9`` is exactly 90%, but ``(8.1 / 9) * 100`` evaluates
to ``89.99999999999999``), misclassifying the grade. Constructing the
operands with ``Decimal(str(x))`` recovers the shortest round-trip decimal
text (which is what the CSV/CLI actually stored) and yields an exact decimal
percentage for classification. Aggregates (averages, GPAs) stay in ``float``
because their final 2-decimal rounding absorbs representation error
(verified against exact rational arithmetic), so converting the whole
module to ``Decimal`` would be unnecessary refactoring.
"""

from datetime import date
from decimal import Decimal
import re

# Percentages are compared against these thresholds exactly, with no
# epsilon tolerance: a value genuinely below a threshold stays below it.
GRADE_THRESHOLDS = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))


GRADE_POINTS = {
    "A": 10.0,
    "B": 9.0,
    "C": 8.0,
    "D": 7.0,
    "F": 0.0,
}


def assessment_key(grade):
    """Authoritative identity of one academic record (single source of truth).

    Used by BOTH the calculation layer (SGPA/CGPA dedup) and the persistence
    layer (duplicate rejection on write), so the two can never drift apart.

    A grade row records a result for one student, in one (semester), for one
    (subject) on one (date). score, max_score, and credits describe the
    result; they are NOT part of its identity, so a corrected score is still
    the same assessment. Two rows with the same
    (student_id, semester, subject, date) therefore describe the same
    academic record, and repeating one must not be counted twice.

    A subject repeated in a *different* semester yields a different key, so
    legitimate retakes are preserved.

    Returns None when the record is malformed, i.e. it has no (non-empty)
    subject and therefore no determinate identity. Callers must then treat
    the record as un-deduplicatable rather than invent an identity for it.
    score/max_score are deliberately never consulted.
    """
    subject = str(grade.get("subject", "")).strip()
    if not subject:
        return None
    return (
        str(grade.get("student_id", "")).strip(),
        str(grade.get("semester", "")).strip(),
        subject.casefold(),
        str(grade.get("date", "")).strip(),
    )


def assessment_record_key(grade):
    """Return the complete stored-record key used only for GPA deduplication.

    ``assessment_key`` remains the authoritative four-field identity used by
    add/edit/import/validate.  Score, maximum score, and credits deliberately
    stay outside that identity so a correction still addresses the same
    assessment.  Once multiple same-day assessments have been explicitly
    stored, however, calculations must distinguish their complete grade data
    or one of them would silently disappear from SGPA/CGPA.

    Exact repeated rows share this key and are still counted only once.
    """
    key = assessment_key(grade)
    if key is None:
        return None
    return key + (
        grade.get("score"),
        grade.get("max_score"),
        _credit(grade),
    )


def _dedupe_assessments(grades):
    """Keep the first copy of each complete stored grade record.

    Legacy, manually edited, or corrupted CSV files can contain the same
    record more than once (even though save_grade now rejects exact
    duplicates on write). Each distinct record must contribute exactly once
    to SGPA/CGPA, so repeats are collapsed here rather than counted again.

    A record with no subject is malformed: it has no identity under the
    schema, so it is never deduplicated (it is always kept). Records that
    share the four-field assessment identity but differ in grade data are
    separate same-day assessments and are both retained.
    """
    seen = set()
    unique = []
    for g in grades:
        key = assessment_record_key(g)
        if key is None:
            unique.append(g)
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique


def _credit(grade):
    """Credits of a grade row as a float, falling back to the default of 1.0."""
    try:
        return float(grade.get("credits", 1.0))
    except (TypeError, ValueError):
        return 1.0


def validate_grade(score, max_score, date_value):
    """Validate a grade before it can be persisted.

    Raises:
        ValueError: If the score range or calendar date is invalid.
    """
    if max_score <= 0:
        raise ValueError("max_score must be greater than 0")
    if not 0 <= score <= max_score:
        raise ValueError("score must be between 0 and max_score")
    if not isinstance(date_value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
        raise ValueError("date must use YYYY-MM-DD format")
    try:
        date.fromisoformat(date_value)
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date in YYYY-MM-DD format") from exc


def percentage(score, max_score):
    """Returns the full-precision percentage ``score / max_score * 100``.

    Computed with ``Decimal`` built from each value's shortest round-trip
    decimal text, so percentages that are exact in decimal (e.g. ``8.1/9``)
    stay exact instead of drifting a hair below a threshold in binary
    floating-point. The result is a ``Decimal``; use ``float(...)`` for
    display or accumulation.
    """
    if max_score <= 0:
        raise ValueError("max_score must be greater than 0")
    return Decimal(str(score)) / Decimal(str(max_score)) * 100


def letter_grade(score, max_score=100):
    """Determines the letter grade corresponding to a score and max_score.

    Classification uses the unrounded percentage (see :func:`percentage`),
    never a rounded display percentage, and compares against the existing
    thresholds exactly — no epsilon tolerance is applied, so a value
    genuinely below a threshold keeps the lower grade.
    """
    if max_score <= 0:
        raise ValueError("max_score must be greater than 0")

    pct = percentage(score, max_score)

    for threshold, grade in GRADE_THRESHOLDS:
        if pct >= threshold:
            return grade
    return "F"


def sgpa(grades, student_id=None, semester=None):
    """Calculates Semester Grade Point Average on a 10.0 scale.

    Each distinct stored assessment in the semester contributes exactly once.
    Exact duplicate rows are collapsed, while same-day assessments with
    different grade data are retained.

    Numerical policy: each grade point is classified from the unrounded
    percentage, weighted grade-point and credit totals are accumulated
    without rounding, and only the final average is rounded to 2 decimals.
    """
    if student_id is not None:
        grades = [g for g in grades if g.get("student_id") == student_id]
    if semester is not None:
        grades = [g for g in grades if str(g.get("semester", "")) == str(semester)]

    grades = _dedupe_assessments(grades)

    if not grades:
        return None

    total_points = 0.0
    total_credits = 0.0
    for g in grades:
        c = _credit(g)
        pts = GRADE_POINTS.get(letter_grade(g["score"], g["max_score"]), 0.0)
        total_points += pts * c
        total_credits += c

    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)


def cgpa(grades, student_id=None):
    """Calculates Cumulative Grade Point Average on a 10.0 scale.

    Aggregates every distinct assessment across all of the student's
    semesters. A subject repeated in a later semester is a separate
    assessment (a legitimate retake) and is kept. Only exact repeated rows
    are collapsed; distinct same-day assessment results are retained.

    Numerical policy: same as :func:`sgpa` — unrounded classification,
    unrounded intermediate totals, final result rounded to 2 decimals.
    """
    if student_id is not None:
        grades = [g for g in grades if g.get("student_id") == student_id]

    grades = _dedupe_assessments(grades)

    if not grades:
        return None

    total_points = 0.0
    total_credits = 0.0
    for g in grades:
        c = _credit(g)
        pts = GRADE_POINTS.get(letter_grade(g["score"], g["max_score"]), 0.0)
        total_points += pts * c
        total_credits += c

    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)


def subject_average(grades, subject):
    """Calculates the average percentage for a given subject.

    Each grade is converted to a full-precision percentage first (see
    :func:`percentage`), so marks out of different maximum scores compare
    fairly (35/50 and 70/100 are both 70%). Individual percentages are
    never rounded before averaging; only the final average is rounded to
    the existing output precision (2 decimals).

    Returns None if no entries exist for the subject.
    """
    matching = [
        g
        for g in grades
        if g.get("subject", "").strip().lower() == subject.strip().lower()
    ]
    if not matching:
        return None

    percentages = [percentage(g["score"], g["max_score"]) for g in matching]
    return round(float(sum(percentages) / len(percentages)), 2)
