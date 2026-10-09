"""Unit tests for calculation logic in grade_tracker."""

import unittest
from grade_tracker.calc import assessment_key, letter_grade, cgpa, sgpa, subject_average


class TestGradeCalculations(unittest.TestCase):

    def test_letter_grade_top_tier(self):
        """A score of 95/100 should return an 'A'."""
        self.assertEqual(letter_grade(95, 100), "A")

    def test_letter_grade_invalid_max_score(self):
        """A max_score <= 0 should raise ValueError."""
        with self.assertRaises(ValueError):
            letter_grade(50, 0)

    def test_cgpa_empty_returns_none(self):
        """Calculating CGPA on an empty list of grades should return None."""
        self.assertIsNone(cgpa([]))

    def test_sgpa_single_course(self):
        """Single course with score 95/100 should evaluate to 10.0 SGPA (A grade, 10.0 pts)."""
        grades = [{"student_id": "S1", "score": 95, "max_score": 100, "credits": 3, "semester": 1}]
        self.assertEqual(sgpa(grades, "S1", 1), 10.0)

    def test_cgpa_worked_example(self):
        """Worked example for CGPA and SGPA with credits."""
        grades = [
            {"student_id": "S1", "score": 95, "max_score": 100, "credits": 4, "semester": 1}, # A -> 10 * 4 = 40
            {"student_id": "S1", "score": 85, "max_score": 100, "credits": 3, "semester": 1}, # B -> 9 * 3 = 27
            {"student_id": "S1", "score": 75, "max_score": 100, "credits": 2, "semester": 2}, # C -> 8 * 2 = 16
        ]
        # SGPA Sem 1: (40 + 27) / 7 = 67 / 7 = 9.57
        self.assertEqual(sgpa(grades, "S1", 1), 9.57)
        # SGPA Sem 2: 16 / 2 = 8.0
        self.assertEqual(sgpa(grades, "S1", 2), 8.0)
        # CGPA: (40 + 27 + 16) / 9 = 83 / 9 = 9.22
        self.assertEqual(cgpa(grades, "S1"), 9.22)

    def test_subject_average_single_entry(self):
        """A single grade entry for a subject should return that score as average."""
        grades = [{"subject": "Mathematics", "score": 85, "max_score": 100}]
        self.assertEqual(subject_average(grades, "Mathematics"), 85.0)

    def test_subject_average_nonexistent(self):
        """Subject average should return None if no entries exist for that subject."""
        self.assertIsNone(subject_average([], "Physics"))

    def test_subject_average_mixed_max_scores(self):
        """Grades with different maximum scores should be averaged as percentages.

        Regression test for Issue #5: 35/50 and 70/100 are both 70%, so the
        average must be 70.0, not the raw-score average of 52.5.
        """
        grades = [
            {"subject": "Physics", "score": 35, "max_score": 50},
            {"subject": "Physics", "score": 70, "max_score": 100},
        ]
        self.assertEqual(subject_average(grades, "Physics"), 70.0)

    def test_subject_average_same_max_score(self):
        """When every grade shares a maximum score the average percentage matches the raw average."""
        grades = [
            {"subject": "Mathematics", "score": 80, "max_score": 100},
            {"subject": "Mathematics", "score": 90, "max_score": 100},
        ]
        self.assertEqual(subject_average(grades, "Mathematics"), 85.0)

    def test_subject_average_different_percentages(self):
        """Percentages that differ should be averaged, not raw scores."""
        grades = [
            {"subject": "Chemistry", "score": 45, "max_score": 50},   # 90%
            {"subject": "Chemistry", "score": 40, "max_score": 100},  # 40%
        ]
        self.assertEqual(subject_average(grades, "Chemistry"), 65.0)

    def test_letter_grade_boundaries(self):
        """checking letter grade boundaries"""
        self.assertEqual(letter_grade(90, 100), "A")
        self.assertEqual(letter_grade(80, 100), "B")
        self.assertEqual(letter_grade(70, 100), "C")
        self.assertEqual(letter_grade(60, 100), "D")
        self.assertEqual(letter_grade(59, 100), "F")


class TestSemesterRecordConsistency(unittest.TestCase):
    """Regression tests for Issue #12: SGPA/CGPA with repeated/duplicate records.

    Model: a record is identified by (student_id, semester, subject, date).
    Multiple records for the same subject in one semester collapse to one
    contribution in that semester's SGPA (and hence in CGPA); the same
    subject in a different semester is a legitimate retake and is kept.
    """

    @staticmethod
    def rec(subject, score, credits, semester, date="", student_id="S1"):
        return {
            "student_id": student_id,
            "subject": subject,
            "score": score,
            "max_score": 100,
            "date": date,
            "credits": credits,
            "semester": str(semester),
        }

    def test_single_subject_sgpa(self):
        """A single subject contributes exactly its grade point."""
        grades = [self.rec("Math", 95, 3, 1)]
        self.assertEqual(sgpa(grades, "S1", 1), 10.0)

    def test_multiple_subjects_credit_weighted_sgpa(self):
        """SGPA is credit-weighted: (10*4 + 9*3) / 7 = 9.57."""
        grades = [
            self.rec("Math", 95, 4, 1),
            self.rec("Phy", 85, 3, 1),
        ]
        self.assertEqual(sgpa(grades, "S1", 1), 9.57)

    def test_same_subject_different_semesters_is_retake(self):
        """The same subject in two semesters is kept in both semesters."""
        grades = [
            self.rec("Math", 95, 3, 1),
            self.rec("Math", 85, 3, 2),
        ]
        self.assertEqual(sgpa(grades, "S1", 1), 10.0)
        self.assertEqual(sgpa(grades, "S1", 2), 9.0)
        # Retake is a distinct result: CGPA = (10*3 + 9*3)/6 = 9.5
        self.assertEqual(cgpa(grades, "S1"), 9.5)

    def test_duplicate_same_semester_record_with_different_score(self):
        """A duplicate record in one semester must not be double-counted.

        Math appears twice in semester 1 with different scores; only the
        first distinct record counts, so SGPA = (10*3 + 9*3)/6 = 9.5,
        not the double-counted 9.0.
        """
        grades = [
            self.rec("Math", 95, 3, 1),
            self.rec("Math", 70, 3, 1),  # duplicate of the same subject/semester
            self.rec("Phy", 85, 3, 1),
        ]
        self.assertEqual(sgpa(grades, "S1", 1), 9.5)

    def test_exact_duplicate_row_with_different_credits(self):
        """A duplicated row with different credits is still one record."""
        grades = [
            self.rec("Math", 95, 3, 1),
            self.rec("Math", 95, 4, 1),  # same record, credits corrupted to 4
            self.rec("Phy", 85, 3, 1),
        ]
        # First record wins: (10*3 + 9*3)/6 = 9.5
        self.assertEqual(sgpa(grades, "S1", 1), 9.5)

    def test_cgpa_across_semesters_with_duplicate(self):
        """CGPA aggregates distinct records across all semesters."""
        grades = [
            self.rec("Math", 95, 4, 1),
            self.rec("Math", 95, 4, 1),  # duplicate in semester 1
            self.rec("Phy", 85, 3, 1),
            self.rec("Chem", 75, 2, 2),
        ]
        # Distinct: sem1 Math(4), Phy(3); sem2 Chem(2)
        # (10*4 + 9*3 + 8*2)/9 = 83/9 = 9.22
        self.assertEqual(cgpa(grades, "S1"), 9.22)

    def test_unequal_credits_preserved(self):
        """Credit weighting uses each distinct record's own credits."""
        grades = [
            self.rec("Math", 95, 5, 1),
            self.rec("Phy", 75, 1, 1),
        ]
        # (10*5 + 8*1)/6 = 58/6 = 9.67
        self.assertEqual(sgpa(grades, "S1", 1), 9.67)

    def test_duplicate_from_csv_rows(self):
        """Duplicate rows as loaded from a CSV are collapsed."""
        grades = [
            {"student_id": "S1", "subject": "Mathematics", "score": 95.0,
             "max_score": 100.0, "date": "2025-01-15", "credits": 3.0, "semester": "1"},
            {"student_id": "S1", "subject": "Mathematics", "score": 70.0,
             "max_score": 100.0, "date": "2025-01-15", "credits": 3.0, "semester": "1"},
            {"student_id": "S1", "subject": "Physics", "score": 85.0,
             "max_score": 100.0, "date": "2025-01-16", "credits": 3.0, "semester": "1"},
        ]
        # First record wins: (10*3 + 9*3)/6 = 9.5
        self.assertEqual(sgpa(grades, "S1", 1), 9.5)

    def test_different_dates_same_subject_are_distinct_assessments(self):
        """Two assessments of the same subject on different dates both count."""
        grades = [
            self.rec("Math", 95, 3, 1, date="2025-01-15"),
            self.rec("Math", 75, 3, 1, date="2025-02-15"),
        ]
        # (10*3 + 8*3)/6 = 9.0
        self.assertEqual(sgpa(grades, "S1", 1), 9.0)

    def test_zero_credits_returns_zero(self):
        """A semester whose records all have zero credits reports 0.0."""
        grades = [self.rec("Math", 95, 0, 1)]
        self.assertEqual(sgpa(grades, "S1", 1), 0.0)

    def test_empty_and_no_match_return_none(self):
        """No matching records still return None, not a bogus number."""
        self.assertIsNone(sgpa([], "S1", 1))
        self.assertIsNone(cgpa([], "S1"))
        grades = [self.rec("Math", 95, 3, 1)]
        self.assertIsNone(sgpa(grades, "S2", 1))

    def test_different_subjects_same_date_are_distinct(self):
        """Two subjects graded on the same date are separate records."""
        grades = [
            self.rec("Math", 95, 3, 1, date="2025-01-15"),
            self.rec("Phy", 75, 3, 1, date="2025-01-15"),
        ]
        # (10*3 + 8*3)/6 = 9.0
        self.assertEqual(sgpa(grades, "S1", 1), 9.0)

    def test_malformed_records_without_subject_are_never_deduplicated(self):
        """A record with no subject has no identity and is always kept.

        Loose-input callers (and the historical tests) may pass rows without
        a subject. Inventing an identity from score/max_score would wrongly
        collapse or split them, so each malformed row is preserved as-is.
        """
        grades = [
            {"student_id": "S1", "score": 95, "max_score": 100, "credits": 4, "semester": "1"},
            {"student_id": "S1", "score": 85, "max_score": 100, "credits": 3, "semester": "1"},
        ]
        # Both rows kept: (10*4 + 9*3)/7 = 67/7 = 9.57
        self.assertEqual(sgpa(grades, "S1", 1), 9.57)

    def test_corrected_score_same_identity_is_not_split(self):
        """A corrected score keeps the same identity, so it is not double-counted.

        Identity must never depend on score: changing 70 -> 95 for the same
        (student, semester, subject, date) is still one assessment.
        """
        original = self.rec("Math", 70, 3, 1, date="2025-01-15")
        corrected = self.rec("Math", 95, 3, 1, date="2025-01-15")
        self.assertEqual(
            assessment_key(original),
            assessment_key(corrected),
        )
        # Only the first row (70 -> C=8) contributes once; corrected row collapses.
        grades = [original, corrected, self.rec("Phy", 85, 3, 1, date="2025-01-16")]
        # (8*3 + 9*3)/6 = 51/6 = 8.5
        self.assertEqual(sgpa(grades, "S1", 1), 8.5)

    def test_malformed_record_key_is_none(self):
        """assessment_key returns None when subject is missing/empty."""
        self.assertIsNone(assessment_key({"student_id": "S1", "score": 95, "semester": "1"}))
        self.assertIsNone(assessment_key({"student_id": "S1", "subject": "  ", "semester": "1"}))

    def test_well_formed_record_key_matches_persistence_identity(self):
        """calc.assessment_key matches the identity used when persisting."""
        grade = {"student_id": "S1", "subject": "Mathematics", "date": "2026-10-06", "semester": "1"}
        self.assertEqual(
            assessment_key(grade),
            ("S1", "1", "mathematics", "2026-10-06"),
        )


if __name__ == "__main__":
    unittest.main()
