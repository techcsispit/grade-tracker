"""Calculation module for academic grades, GPA, and averages."""

from datetime import date
import re


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


def _dedupe_assessments(grades):
    """Keep the first row for each distinct academic record.

    Legacy, manually edited, or corrupted CSV files can contain the same
    record more than once (even though save_grade now rejects exact
    duplicates on write). Each distinct record must contribute exactly once
    to SGPA/CGPA, so repeats are collapsed here rather than counted again.

    A record with no subject is malformed: it has no identity under the
    schema, so it is never deduplicated (it is always kept). Inventing an
    identity from score/max_score would wrongly split a corrected record.
    Records from different semesters are never collapsed with each other.
    """
    seen = set()
    unique = []
    for g in grades:
        key = assessment_key(g)
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


def letter_grade(score, max_score=100):
    """Determines the letter grade corresponding to a score and max_score."""
    if max_score <= 0:
        raise ValueError("max_score must be greater than 0")

    pct = (score / max_score) * 100

    if pct >= 90:
        return "A"
    elif pct >= 80:
        return "B"
    elif pct >= 70:
        return "C"
    elif pct >= 60:
        return "D"
    else:
        return "F"


def sgpa(grades, student_id=None, semester=None):
    """Calculates Semester Grade Point Average on a 10.0 scale.

    Each distinct assessment (student_id, semester, subject, date) in the
    semester contributes exactly once. Duplicate rows for the same assessment
    — possible in legacy or hand-edited CSV files — are collapsed so a
    subject cannot be double-counted within one semester.
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
    assessment (a legitimate retake) and is kept; only rows that repeat the
    same (student_id, semester, subject, date) assessment are collapsed, so
    duplicate records cannot inflate the CGPA.
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

    Each grade is converted to a percentage first, so marks out of
    different maximum scores compare fairly (35/50 and 70/100 are both 70%).

    Returns None if no entries exist for the subject.
    """
    matching = [
        g
        for g in grades
        if g.get("subject", "").strip().lower() == subject.strip().lower()
    ]
    if not matching:
        return None

    percentages = [(g["score"] / g["max_score"]) * 100 for g in matching]
    return round(sum(percentages) / len(percentages), 2)
