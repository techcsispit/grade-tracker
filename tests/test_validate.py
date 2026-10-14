"""Regression tests for the read-only CSV integrity audit command (Issue #23)."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grade_tracker.cli import main


class TestValidateCommand(unittest.TestCase):
    """Exercise the public validate subcommand end-to-end."""

    HEADER = "student_id,subject,score,max_score,date,credits,semester\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "grades.csv"

    def write_csv(self, rows, header=None):
        """Write a raw CSV file byte-for-byte (no implicit BOM or newline)."""
        text = (self.HEADER if header is None else header) + "".join(rows)
        self.data_path.write_text(text, encoding="utf-8", newline="")

    def file_bytes(self):
        return self.data_path.read_bytes()

    def run_validate(self):
        argv = ["grade_tracker", "--data", str(self.data_path), "validate"]
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch("sys.argv", argv),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()

        return 0, stdout.getvalue(), stderr.getvalue()

    def test_valid_csv_passes(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Physics,85,100,2025-01-16,3,1\n",
        ])

        code, stdout, stderr = self.run_validate()

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("passed validation", stdout)

    def test_valid_csv_with_boundary_values_passes(self):
        self.write_csv([
            "S1,Mathematics,0,100,2025-01-15,0.5,1\n",
            "S1,Physics,100,100,2025-01-16,4.0,2\n",
        ])

        code, stdout, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("passed validation", stdout)

    def test_missing_file_reports_error(self):
        code, stdout, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("file not found", stderr)
        self.assertEqual(stdout, "")
        self.assertFalse(self.data_path.exists())

    def test_empty_file_reports_error(self):
        self.data_path.write_text("", encoding="utf-8")

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("file is empty", stderr)

    def test_missing_required_columns(self):
        self.write_csv(
            ["S1,Mathematics,95,2025-01-15,3,1\n"],
            header="student_id,subject,score,date,credits,semester\n",
        )

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 1: missing required column(s): max_score", stderr)

    def test_duplicate_header_column(self):
        self.write_csv(
            ["S1,Mathematics,95,100,2025-01-15,3,1\n"],
            header="student_id,subject,score,max_score,date,credits,semester,credits\n",
        )

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 1: duplicate header column(s): credits", stderr)

    def test_blank_student_id_is_flagged(self):
        self.write_csv([",Mathematics,95,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field student_id: missing or blank student ID", stderr)

    def test_blank_subject_is_flagged(self):
        self.write_csv(["S1,,95,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field subject: missing or blank subject", stderr)

    def test_blank_date_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,100,,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field date: missing or blank date", stderr)

    def test_blank_semester_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,100,2025-01-15,3,\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field semester: missing or blank semester", stderr)

    def test_blank_score_is_flagged(self):
        self.write_csv(["S1,Mathematics,,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field score: missing or blank score", stderr)

    def test_blank_max_score_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field max_score: missing or blank maximum score", stderr)

    def test_malformed_row_wrong_field_count(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Physics,85,100,2025-01-16\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 3: malformed record: expected 7 fields, got 5", stderr)

    def test_extra_field_count_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,100,2025-01-15,3,1,extra\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2: malformed record: expected 7 fields, got 8", stderr)

    def test_non_numeric_score_is_flagged(self):
        self.write_csv(["S1,Mathematics,abc,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field score: 'abc' is not a numeric score", stderr)

    def test_non_numeric_max_score_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,abc,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn(
            "row 2, field max_score: 'abc' is not a numeric maximum score", stderr
        )

    def test_non_numeric_credits_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,100,2025-01-15,abc,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn(
            "row 2, field credits: 'abc' is not a numeric credit value", stderr
        )

    def test_non_finite_numeric_values_are_flagged(self):
        self.write_csv(["S1,Mathematics,inf,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field score:", stderr)
        self.assertIn("is not a numeric score", stderr)

    def test_negative_score_is_flagged(self):
        self.write_csv(["S1,Mathematics,-5,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field score: score '-5' must be between 0 and", stderr)

    def test_score_above_max_is_flagged(self):
        self.write_csv(["S1,Mathematics,150,100,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field score: score '150' must be between 0 and", stderr)

    def test_zero_max_score_is_flagged(self):
        self.write_csv(["S1,Mathematics,50,0,2025-01-15,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn(
            "row 2, field max_score: maximum score '0' must be greater than 0", stderr
        )

    def test_malformed_date_format_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,100,15-01-2025,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn(
            "row 2, field date: '15-01-2025' must use YYYY-MM-DD format", stderr
        )

    def test_impossible_calendar_date_is_flagged(self):
        self.write_csv(["S1,Mathematics,95,100,2025-02-30,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn(
            "row 2, field date: '2025-02-30' is not a valid calendar date", stderr
        )

    def test_distinct_same_day_assessments_pass_validation(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Mathematics,70,100,2025-01-15,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_case_insensitive_subject_duplicate_is_flagged(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,MATHEMATICS,95,100,2025-01-15,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 3: duplicate assessment of row 2", stderr)

    def test_same_subject_different_semester_is_not_duplicate(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Mathematics,70,100,2025-01-15,3,2\n",
        ])

        code, stdout, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("passed validation", stdout)

    def test_same_subject_different_date_is_not_duplicate(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Mathematics,70,100,2025-01-16,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_multiple_errors_across_rows_are_all_reported(self):
        self.write_csv([
            "S1,Mathematics,abc,100,2025-01-15,3,1\n",
            ",Physics,85,100,2025-01-16,3,1\n",
            "S1,Mathematics,50,100,not-a-date,3,1\n",
            "S1,,95,100,2025-01-17,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field score:", stderr)
        self.assertIn("row 3, field student_id:", stderr)
        self.assertIn("row 4, field date:", stderr)
        self.assertIn("row 5, field subject:", stderr)

    def test_row_numbers_count_header_as_row_one(self):
        self.write_csv([
            "\n",
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            ",Physics,85,100,2025-01-16,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 4, field student_id", stderr)

    def test_multiple_problems_in_one_row(self):
        self.write_csv(["S1,,abc,0,bad-date,3,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 2, field subject:", stderr)
        self.assertIn("row 2, field date:", stderr)
        self.assertIn("row 2, field score:", stderr)
        self.assertIn("row 2, field max_score:", stderr)

    def test_summary_counts_all_problems(self):
        self.write_csv([
            ",Physics,85,100,2025-01-16,3,1\n",
            "S1,,abc,100,2025-01-17,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("Validation failed: 3 problems found.", stderr)

    def test_legacy_row_without_credits_column_passes(self):
        self.write_csv(
            ["S1,Mathematics,95,100,2025-01-15,1\n"],
            header="student_id,subject,score,max_score,date,semester\n",
        )

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_legacy_row_without_semester_column_passes(self):
        self.write_csv(
            ["S1,Mathematics,95,100,2025-01-15,3\n"],
            header="student_id,subject,score,max_score,date,credits\n",
        )

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_legacy_row_with_empty_credits_cell_passes(self):
        self.write_csv(["S1,Mathematics,95,100,2025-01-15,,1\n"])

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_blank_credits_cell_allows_distinct_same_day_assessments(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,,1\n",
            "S1,Mathematics,70,100,2025-01-15,,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_blank_line_between_records_is_flagged(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            ",,,,,,\n",
            "S1,Physics,85,100,2025-01-16,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("row 3: malformed record", stderr)

    def test_same_assessment_different_student_is_not_duplicate(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S2,Mathematics,70,100,2025-01-15,3,1\n",
        ])

        code, _, stderr = self.run_validate()

        self.assertEqual((code, stderr), (0, ""))

    def test_validate_never_modifies_valid_file(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Physics,85.5,100.0,2025-01-16,3,1\n",
        ])
        before = self.file_bytes()
        before_mtime = os.stat(self.data_path).st_mtime_ns

        code, _, _ = self.run_validate()

        self.assertEqual(code, 0)
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual(os.stat(self.data_path).st_mtime_ns, before_mtime)

    def test_validate_never_modifies_invalid_file(self):
        self.write_csv([
            ",Mathematics,abc,100,2025-01-15,3,1\n",
            "S1,Physics,150,100,2025-01-16,3,1\n",
            "S1,Physics,85,100,2025-01-16,3,1\n",
        ])
        before = self.file_bytes()
        before_mtime = os.stat(self.data_path).st_mtime_ns

        code, _, _ = self.run_validate()

        self.assertEqual(code, 1)
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual(os.stat(self.data_path).st_mtime_ns, before_mtime)

    def test_validate_does_not_create_missing_file(self):
        code, _, _ = self.run_validate()

        self.assertEqual(code, 1)
        self.assertFalse(self.data_path.exists())

    def test_does_not_call_save_grade_or_any_writer(self):
        self.write_csv([
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
            "S1,Mathematics,95,100,2025-01-15,3,1\n",
        ])

        with mock.patch("grade_tracker.cli.save_grade") as save_mock:
            code, _, _ = self.run_validate()

        self.assertEqual(code, 1)
        save_mock.assert_not_called()

    def test_unreadable_file_reports_gracefully(self):
        self.write_csv(["S1,Mathematics,95,100,2025-01-15,3,1\n"])

        with mock.patch("grade_tracker.cli.open", side_effect=PermissionError("denied")):
            code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("cannot read file", stderr)

    def test_directory_path_reports_gracefully(self):
        directory = Path(self.temp_dir.name) / "subdir"
        directory.mkdir()
        argv = ["grade_tracker", "--data", str(directory), "validate"]
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch("sys.argv", argv),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                main()
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0

        self.assertEqual(code, 1)
        self.assertIn("not a regular file", stderr.getvalue())

    def test_non_utf8_file_reports_gracefully(self):
        self.data_path.write_bytes(
            b"student_id,subject,score,max_score,date,credits,semester\n"
            b"S1,Math\xe9matiques,95,100,2025-01-15,3,1\n"
        )

        code, _, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertIn("not valid UTF-8 text", stderr)

    def test_repository_data_file_is_valid(self):
        """The committed sample CSV must itself pass the audit."""
        repo_data = (
            Path(__file__).resolve().parent.parent / "data" / "grades.csv"
        )
        argv = ["grade_tracker", "--data", str(repo_data), "validate"]
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch("sys.argv", argv),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                main()
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0

        self.assertEqual((code, stderr.getvalue()), (0, ""))
        self.assertIn("passed validation", stdout.getvalue())

    def test_validate_output_lists_errors_on_stderr_only(self):
        self.write_csv(["S1,Mathematics,abc,100,2025-01-15,3,1\n"])

        code, stdout, stderr = self.run_validate()

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Error: row 2, field score:", stderr)


if __name__ == "__main__":
    unittest.main()
