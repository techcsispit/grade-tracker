"""Regression tests for atomic grade persistence (Issue #13).

save_grade must never leave a partially written or corrupted CSV behind.
These tests inject failures at each stage of the atomic write (writing the
temporary file, fsync, replace) and assert the original file is left
byte-for-byte unchanged with no stray temporary files.
"""

import contextlib
import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from grade_tracker.cli import CSV_HEADER, load_grades, main, save_grade


def _temp_files(directory):
    """Leftover files in ``directory`` other than the CSV itself."""
    return [
        entry.name
        for entry in Path(directory).iterdir()
        if entry.name != "grades.csv"
    ]


class AtomicPersistenceTestCase(unittest.TestCase):
    """Shared setup: an isolated temp directory and a known-good CSV."""

    HEADER = "student_id,subject,score,max_score,date,credits,semester\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "grades.csv"

    def write_existing_csv(self):
        """Writes a small existing CSV with a header and one record."""
        self.data_path.write_text(
            self.HEADER + "S1,Mathematics,95,100,2025-01-15,3,1\n",
            encoding="utf-8",
        )
        return self.data_path.read_bytes()

    def read_rows(self):
        if not self.data_path.exists():
            return []
        with self.data_path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def run_add(self, *args):
        argv = ["grade_tracker", "--data", str(self.data_path), "add", *args]
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


class TestAtomicSave(AtomicPersistenceTestCase):
    """Happy-path behaviour of the atomic write."""

    def test_save_creates_new_file_with_header(self):
        save_grade("S1", "Mathematics", 50, 100, "2026-10-05", 3, "1", self.data_path)

        self.assertTrue(self.data_path.exists())
        with self.data_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], CSV_HEADER)
        self.assertEqual(len(rows), 2)
        self.assertEqual(_temp_files(self.temp_dir.name), [])

    def test_save_preserves_existing_records_and_appends(self):
        self.write_existing_csv()

        save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        rows = self.read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["subject"], "Mathematics")
        self.assertEqual(rows[0]["score"], "95")
        self.assertEqual(rows[1]["subject"], "Physics")
        self.assertEqual(_temp_files(self.temp_dir.name), [])

    def test_successful_replace_is_readable_by_load_grades(self):
        self.write_existing_csv()
        save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        grades = load_grades(self.data_path)
        self.assertEqual(len(grades), 2)
        self.assertEqual(grades[1]["subject"], "Physics")

    def test_existing_record_text_is_rewritten_verbatim(self):
        """The original row keeps its exact original formatting (no reformat)."""
        original = self.write_existing_csv()
        save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        content = self.data_path.read_bytes().decode("utf-8")
        self.assertTrue(content.startswith(original.decode("utf-8")))

    def test_saves_into_missing_parent_directory(self):
        nested = Path(self.temp_dir.name) / "nested" / "grades.csv"
        save_grade("S1", "Mathematics", 50, 100, "2026-10-05", 3, "1", nested)
        self.assertTrue(nested.exists())
        self.assertEqual(len(load_grades(nested)), 1)


class TestAtomicWriteFailures(AtomicPersistenceTestCase):
    """Injected failures must leave the original CSV byte-for-byte unchanged."""

    def assert_original_unchanged(self, original_bytes, expected_rows=None):
        self.assertTrue(self.data_path.exists())
        self.assertEqual(self.data_path.read_bytes(), original_bytes)
        self.assertEqual(_temp_files(self.temp_dir.name), [])
        if expected_rows is not None:
            self.assertEqual(len(self.read_rows()), expected_rows)

    def test_write_failure_leaves_original_unchanged(self):
        original = self.write_existing_csv()

        with mock.patch("grade_tracker.cli.csv.writer",
                        side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        self.assert_original_unchanged(original, expected_rows=1)

    def test_fsync_failure_leaves_original_unchanged(self):
        original = self.write_existing_csv()

        with mock.patch("grade_tracker.cli.os.fsync",
                        side_effect=OSError("simulated fsync failure")):
            with self.assertRaises(OSError):
                save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        self.assert_original_unchanged(original, expected_rows=1)

    def test_replace_failure_leaves_original_unchanged(self):
        original = self.write_existing_csv()

        with mock.patch("grade_tracker.cli.os.replace",
                        side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        self.assert_original_unchanged(original, expected_rows=1)

    def test_temp_creation_failure_leaves_original_unchanged(self):
        original = self.write_existing_csv()

        with mock.patch("grade_tracker.cli.tempfile.mkstemp",
                        side_effect=OSError("simulated temp creation failure")):
            with self.assertRaises(OSError):
                save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)

        self.assert_original_unchanged(original, expected_rows=1)

    def test_failure_on_new_file_creates_no_file(self):
        """A failed first save must not leave a partial CSV behind."""
        with mock.patch("grade_tracker.cli.os.fsync",
                        side_effect=OSError("simulated fsync failure")):
            with self.assertRaises(OSError):
                save_grade("S1", "Mathematics", 50, 100, "2026-10-05", 3, "1", self.data_path)

        self.assertFalse(self.data_path.exists())
        self.assertEqual(_temp_files(self.temp_dir.name), [])

    def test_successful_retry_after_failure(self):
        """A later successful save still works after an injected failure."""
        original = self.write_existing_csv()

        with mock.patch("grade_tracker.cli.os.replace",
                        side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)
        self.assertEqual(self.data_path.read_bytes(), original)

        save_grade("S1", "Physics", 85, 100, "2026-10-06", 3, "1", self.data_path)
        self.assertEqual(len(self.read_rows()), 2)
        self.assertEqual(_temp_files(self.temp_dir.name), [])


class TestValidationAndDuplicates(AtomicPersistenceTestCase):
    """Validation and duplicate rejection must still happen before any write."""

    def test_invalid_grade_is_rejected_before_any_write(self):
        code, stdout, stderr = self.run_add(
            "S1", "Mathematics", "101", "100", "2026-10-05", "3", "1"
        )
        self.assertEqual(code, 2)
        self.assertIn("score must be between 0 and max_score", stderr)
        self.assertNotIn("Added grade", stdout)
        self.assertFalse(self.data_path.exists())
        self.assertEqual(_temp_files(self.temp_dir.name), [])

    def test_duplicate_record_is_rejected_and_file_unchanged(self):
        self.write_existing_csv()
        original = self.data_path.read_bytes()

        code, stdout, stderr = self.run_add(
            "S1", "Mathematics", "95", "100", "2025-01-15", "3", "1"
        )
        self.assertEqual(code, 2)
        self.assertIn("assessment already exists", stderr)
        self.assertNotIn("Added grade", stdout)
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertEqual(_temp_files(self.temp_dir.name), [])


class TestCliReportsPersistenceFailure(AtomicPersistenceTestCase):
    """The CLI must report persistence failures as errors, not success."""

    def test_cli_reports_write_failure_as_error(self):
        original = self.write_existing_csv()

        with mock.patch("grade_tracker.cli.os.replace",
                        side_effect=OSError("simulated replace failure")):
            code, stdout, stderr = self.run_add(
                "S1", "Physics", "85", "100", "2026-10-06", "3", "1"
            )

        self.assertNotEqual(code, 0)
        self.assertIn("Error:", stderr)
        self.assertNotIn("Added grade", stdout)
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertEqual(_temp_files(self.temp_dir.name), [])

    def test_cli_reports_fsync_failure_as_error(self):
        self.write_existing_csv()

        with mock.patch("grade_tracker.cli.os.fsync",
                        side_effect=OSError("simulated fsync failure")):
            code, stdout, stderr = self.run_add(
                "S1", "Physics", "85", "100", "2026-10-06", "3", "1"
            )

        self.assertNotEqual(code, 0)
        self.assertIn("Error:", stderr)
        self.assertNotIn("Added grade", stdout)


if __name__ == "__main__":
    unittest.main()