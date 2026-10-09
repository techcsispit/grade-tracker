"""Regression tests for add-command validation and persistence."""

import contextlib
import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from grade_tracker.cli import main


class TestAddValidation(unittest.TestCase):
    """Exercise the public CLI before any grade row reaches the data file."""

    def run_add(self, data_path, *args):
        argv = ["grade_tracker", "--data", str(data_path), "add", *args]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            try:
                main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "grades.csv"

    def read_rows(self):
        if not self.data_path.exists():
            return []
        with self.data_path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def assert_rejected(self, *args, expected_error):
        code, stdout, stderr = self.run_add(self.data_path, *args)
        self.assertEqual(code, 2)
        self.assertIn(expected_error, stderr)
        self.assertNotIn("Added grade", stdout)
        self.assertEqual(self.read_rows(), [])

    def test_rejects_zero_max_score_without_writing(self):
        self.assert_rejected(
            "S1", "Mathematics", "50", "0", "not-a-date", "3", "1",
            expected_error="max_score must be greater than 0",
        )

    def test_rejects_score_above_max_without_writing(self):
        self.assert_rejected(
            "S1", "Mathematics", "101", "100", "2026-10-05", "3", "1",
            expected_error="score must be between 0 and max_score",
        )

    def test_rejects_negative_score_without_writing(self):
        self.assert_rejected(
            "S1", "Mathematics", "-1", "100", "2026-10-05", "3", "1",
            expected_error="score must be between 0 and max_score",
        )

    def test_rejects_malformed_date_without_writing(self):
        self.assert_rejected(
            "S1", "Mathematics", "50", "100", "not-a-date", "3", "1",
            expected_error="date must use YYYY-MM-DD format",
        )

    def test_rejects_impossible_calendar_date_without_writing(self):
        self.assert_rejected(
            "S1", "Mathematics", "50", "100", "2026-02-30", "3", "1",
            expected_error="date must be a valid calendar date",
        )

    def test_accepts_zero_score_boundary(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "0", "100", "2026-10-05", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Added grade for S1", stdout)
        self.assertEqual(
            self.read_rows(),
            [{
                "student_id": "S1",
                "subject": "Mathematics",
                "score": "0.0",
                "max_score": "100.0",
                "date": "2026-10-05",
                "credits": "3.0",
                "semester": "1",
            }],
        )

    def test_accepts_score_equal_to_max_boundary(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "100", "100", "2026-10-05", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Added grade for S1", stdout)
        self.assertEqual(len(self.read_rows()), 1)

    def test_accepts_normal_valid_input(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "50", "100", "2026-10-05", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Added grade for S1", stdout)
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], "50.0")


class TestDuplicateDetection(unittest.TestCase):
    """Reject the same assessment being recorded twice (Issue #9).

    The assessment identity is (student_id, semester, subject, date). score,
    max_score, and credits are the result of the assessment, not its identity,
    so a corrected score is still the same assessment and must be rejected.
    """

    def run_add(self, data_path, *args):
        argv = ["grade_tracker", "--data", str(data_path), "add", *args]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            try:
                main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "grades.csv"

    def read_rows(self):
        if not self.data_path.exists():
            return []
        with self.data_path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_duplicate_assessment_is_rejected(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Added grade for S1", stdout)
        self.assertEqual(len(self.read_rows()), 1)

        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual(code, 2)
        self.assertIn("assessment already exists", stderr)
        self.assertNotIn("Added grade", stdout)
        self.assertEqual(len(self.read_rows()), 1)

    def test_duplicate_does_not_modify_csv(self):
        self.run_add(self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1")
        self.run_add(self.data_path, "S1", "Physics", "85", "100", "2026-10-06", "3", "1")
        self.assertEqual(len(self.read_rows()), 2)

        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual(code, 2)
        self.assertEqual(len(self.read_rows()), 2)

    def test_corrected_score_same_assessment_is_rejected(self):
        self.run_add(self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1")
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "30", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual(code, 2)
        self.assertIn("assessment already exists", stderr)
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], "25.0")

    def test_different_subjects_same_date_are_both_accepted(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Physics", "85", "100", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(self.read_rows()), 2)

    def test_different_semesters_same_subject_date_are_both_accepted(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "2"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(self.read_rows()), 2)

    def test_different_students_same_assessment_are_both_accepted(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        code, stdout, stderr = self.run_add(
            self.data_path, "S2", "Mathematics", "25", "50", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(self.read_rows()), 2)

    def test_same_score_different_assessments_are_both_accepted(self):
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Mathematics", "50", "100", "2026-10-06", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        code, stdout, stderr = self.run_add(
            self.data_path, "S1", "Physics", "50", "100", "2026-10-07", "3", "1"
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(self.read_rows()), 2)

    def test_save_grade_raises_valueerror_for_duplicate(self):
        from grade_tracker.cli import save_grade

        save_grade("S1", "Mathematics", 25, 50, "2026-10-06", 3, "1", self.data_path)
        with self.assertRaises(ValueError):
            save_grade("S1", "Mathematics", 25, 50, "2026-10-06", 3, "1", self.data_path)
        self.assertEqual(len(self.read_rows()), 1)


class TestDuplicateRecordsLoadedFromCsv(unittest.TestCase):
    """SGPA/CGPA must stay correct when the CSV already contains duplicates.

    save_grade rejects duplicates on write, but legacy or hand-edited files
    can still contain them. These tests load such a file and check the
    summary numbers (Issue #12).
    """

    HEADER = "student_id,subject,score,max_score,date,credits,semester\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "grades.csv"

    def write_csv(self, rows):
        with self.data_path.open("w", newline="", encoding="utf-8") as handle:
            handle.write(self.HEADER)
            handle.writelines(rows)

    def run_summary(self, student_id):
        argv = ["grade_tracker", "--data", str(self.data_path), "summary", student_id]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            try:
                main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def test_summary_reports_deduplicated_sgpa_and_cgpa(self):
        """Duplicate Mathematics rows in semester 1 do not skew SGPA/CGPA."""
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Mathematics,70,100,2025-01-15,3,1\n",  # duplicate record
            "S1,Physics,85,100,2025-01-16,3,1\n",
            "S1,Mathematics,85,100,2025-06-15,3,2\n",  # legit retake, later semester
        ])
        code, stdout, stderr = self.run_summary("S1")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        # Semester 1: first Mathematics record (A=10) + Physics (B=9), credits 3+3
        # SGPA = (10*3 + 9*3)/6 = 9.5
        self.assertIn("Semester 1 (SGPA: 9.5)", stdout)
        # Semester 2: Mathematics retake (B=9)
        self.assertIn("Semester 2 (SGPA: 9.0)", stdout)
        # CGPA: distinct sem1 Math(3), Phy(3), sem2 Math(3)
        # (10*3 + 9*3 + 9*3)/9 = 84/9 = 9.33
        self.assertIn("CGPA: 9.33", stdout)

    def test_summary_preserves_retake_in_both_semesters(self):
        """A repeated subject in a later semester is shown in both semesters."""
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Mathematics,75,100,2025-06-15,3,2\n",
        ])
        code, stdout, stderr = self.run_summary("S1")
        self.assertEqual(code, 0)
        self.assertIn("Semester 1 (SGPA: 10.0)", stdout)
        self.assertIn("Semester 2 (SGPA: 8.0)", stdout)
        # (10*3 + 8*3)/6 = 9.0
        self.assertIn("CGPA: 9.0", stdout)

    def test_persistence_and_calculation_keys_are_identical(self):
        """cli.assessment_key must delegate to calc.assessment_key (no drift)."""
        from grade_tracker.cli import assessment_key as cli_key
        from grade_tracker.calc import assessment_key as calc_key

        self.assertEqual(
            cli_key("S1", "1", "Mathematics", "2026-10-06"),
            calc_key({
                "student_id": "S1",
                "semester": "1",
                "subject": "Mathematics",
                "date": "2026-10-06",
            }),
        )

    def test_legacy_csv_duplicate_corrected_score_is_collapsed(self):
        """A legacy CSV where the same assessment was re-saved with a new score.

        Identity ignores score, so the re-saved row is the same assessment and
        is not double-counted; only the first row contributes.
        """
        self.write_csv([
            "S1,Mathematics,70,100,2025-01-15,3,1\n",
            "S1,Mathematics,95,100,2025-01-15,3,1\n",  # corrected score, same assessment
            "S1,Physics,85,100,2025-01-16,3,1\n",
        ])
        code, stdout, stderr = self.run_summary("S1")
        self.assertEqual(code, 0)
        # First (70 -> C=8) row wins: (8*3 + 9*3)/6 = 8.5
        self.assertIn("Semester 1 (SGPA: 8.5)", stdout)


if __name__ == "__main__":
    unittest.main()
