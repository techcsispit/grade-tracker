"""Command Line Interface for Grade Tracker."""

import argparse
import csv
import os
import sys
import tempfile
import time
from pathlib import Path

from grade_tracker.calc import (
    assessment_key as calc_assessment_key,
    cgpa,
    letter_grade,
    percentage,
    sgpa,
    subject_average,
    validate_grade,
)
from grade_tracker.export import build_html

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "grades.csv"




def _lock_path_for(csv_path):
    """Stable lock file that lives next to the target CSV.

    The lock file is a real file whose identity is fixed for the lifetime of
    the CSV.  Its path is derived from a hash of the CSV's resolved absolute
    path so that two processes pointing at the same CSV always use the same
    lock object, regardless of how the path is written (relative, .., or
    different drive letters).
    """
    resolved = Path(csv_path).resolve()
    # Serialize the resolved path to bytes in a platform-independent way so
    # that two processes referencing the same CSV (with different spellings
    # such as relative vs absolute, or different drive-letter cases) always
    # compute the same lock file.
    raw = resolved.resolve().as_posix().encode("utf-8")
    digest = 0
    for byte in raw:
        digest = (digest * 31 + byte) & 0xFFFFFFFF
    name = "grades-{0:08x}.lock".format(digest)
    return resolved.parent / name


class _InterProcessLock:
    """Cross-process lock backed by a stable lock file.

    Uses the operating system's advisory locking primitives:

    * Windows: ``msvcrt.locking`` on the lock file (shared/exclusive byte
      locking), which works for independent OS processes.
    * Unix: ``fcntl.flock`` on the lock file, which also works across
      independent processes (including fork), and is the standard approach.

    The lock is *advisory*: every writer in this project must acquire it, but
    the operating system honours the lock semantics.  If a future writer
    bypasses this class it will simply not be protected; that is a project
    policy, not a correctness failure of the lock itself.

    ``timeout`` (seconds) bounds how long a blocking acquire may wait.  A
    value of ``None`` blocks indefinitely.  Windows' ``msvcrt.locking`` has no
    native timeout, so the implementation waits with short sleeps and aborts
    only once the wall-clock budget is exhausted, instead of busy-looping
    forever.  ``fcntl.flock`` supports a non-blocking flag plus a retry loop,
    which is also bounded by ``timeout``.
    """

    def __init__(self, path, timeout=None):
        self.path = Path(path)
        self.timeout = timeout
        self._fd = None
        self._file = None

    def _cleanup(self):
        """Close the underlying fd and file, ignoring any error.

        Closing always releases the operating-system lock on both platforms
        (Windows releases ``msvcrt.locking`` bytes on close; Unix releases
        ``fcntl.flock`` on close).  This guarantees a failed acquisition or an
        exception unwinding the lock cannot leave a lock stranded.
        """
        fd, file = self._fd, self._file
        self._fd = None
        self._file = None
        if file is not None:
            try:
                file.close()
            except Exception:
                pass
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass

    def acquire(self, timeout=None):
        if self._file is not None:
            return

        if timeout is None:
            timeout = self.timeout
        if timeout is not None:
            timeout = float(timeout)
        deadline = None if timeout is None else time.monotonic() + timeout

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
            self._file = os.fdopen(self._fd, "r+b", buffering=0)
            self._fd = None  # the file object now owns the descriptor
        except Exception:
            self._cleanup()
            raise

        fileno = self._file.fileno()

        if os.name == "nt":
            import errno
            import msvcrt

            while True:
                try:
                    msvcrt.locking(fileno, msvcrt.LK_NBLCK, 1)
                    return
                except (IOError, OSError) as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or isinstance(exc, PermissionError):
                        if deadline is not None:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                self._cleanup()
                                raise TimeoutError(
                                    f"could not acquire lock for {self.path} "
                                    f"within {timeout} seconds"
                                ) from exc
                            time.sleep(min(0.005, remaining))
                        else:
                            time.sleep(0.005)
                        continue
                    self._cleanup()
                    raise

        import errno
        import fcntl

        while True:
            try:
                fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (IOError, OSError) as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self._cleanup()
                            raise TimeoutError(
                                f"could not acquire lock for {self.path} "
                                f"within {timeout} seconds"
                            ) from exc
                        time.sleep(min(0.005, remaining))
                    else:
                        time.sleep(0.005)
                    continue
                self._cleanup()
                raise

    def release(self):
        if self._file is None:
            return
        try:
            fileno = self._file.fileno()
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)
                except (IOError, OSError):
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(fileno, fcntl.LOCK_UN)
                except (IOError, OSError):
                    pass
        finally:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False



def load_grades(csv_path=DEFAULT_DATA_PATH):
    """Loads grades from CSV file and converts score and max_score to floats.

    ``csv_path`` may be a ``Path`` or a string.  The caller is responsible for
    holding the inter-process lock when mutation is expected; load reads the
    file directly for the duplicate-check transaction.
    """
    path = Path(csv_path)
    if not path.exists():
        return []

    grades = []
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row or not row.get("student_id"):
                continue
            try:
                grades.append(
                    {
                        "student_id": row["student_id"].strip(),
                        "subject": row["subject"].strip(),
                        "score": float(row["score"]),
                        "max_score": float(row["max_score"]),
                        "date": row.get("date", "").strip(),
                        "credits": float(row.get("credits", 3.0)),
                        "semester": str(row.get("semester", "1")).strip(),
                    }
                )
            except (ValueError, KeyError):
                continue
    return grades
def assessment_key(student_id, semester, subject, date):
    """Persistence-layer wrapper around the authoritative identity.

    The identity itself lives in grade_tracker.calc.assessment_key (single
    source of truth for both persistence and calculation). This wrapper keeps
    the flat (student_id, semester, subject, date) call shape used when
    saving a grade.
    """
    return calc_assessment_key(
        {
            "student_id": student_id,
            "semester": semester,
            "subject": subject,
            "date": date,
        }
    )


def save_grade(
    student_id,
    subject,
    score,
    max_score,
    date,
    credits,
    semester,
    csv_path=DEFAULT_DATA_PATH,
):
    """Appends a new grade row to the CSV file, unless the assessment exists.

    The read-check-write transaction is protected by a cross-process lock so
    that concurrent `save_grade` calls from separate OS processes cannot
    bypass duplicate detection or corrupt the file.

    Raises:
        ValueError: If the assessment (student_id + semester + subject + date)
            is already recorded.  The file is left unchanged in that case.
    """
    key = assessment_key(student_id, semester, subject, date)

    lock_file = _lock_path_for(csv_path)
    with _InterProcessLock(lock_file) as _lock:
        # Read the latest persisted records while holding the lock.
        existing = load_grades(csv_path)

        # Duplicate detection is performed under the lock.  The identity is
        # derived from the authoritative calc.assessment_key so that the
        # persistence layer can never drift from the calculation layer.
        for grade in existing:
            if assessment_key(
                grade["student_id"],
                grade["semester"],
                grade["subject"],
                grade["date"],
            ) == key:
                raise ValueError(
                    f"assessment already exists for student '{student_id}' "
                    f"in {subject} (semester {semester}) on {date}"
                )

        # Build the updated CSV contents and write atomically.
        # Read the current CSV (if any) and combine it with the new row.
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_rows = []
        file_exists = path.exists()
        if file_exists:
            with open(path, mode="r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                # Preserve the expected header/column order.  An existing file
                # with an unexpected schema is left untouched rather than
                # silently rewritten into the canonical shape.
                expected_header = [
                    "student_id", "subject", "score", "max_score", "date", "credits", "semester"
                ]
                if header is not None and header != expected_header:
                    raise ValueError(
                        "grades file has an unexpected schema; refusing to rewrite "
                        f"{path} (expected header {expected_header!r}, got {header!r})"
                    )
                if header is not None:
                    existing_rows = list(reader)

        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".grades-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["student_id", "subject", "score", "max_score", "date", "credits", "semester"]
                )
                for row in existing_rows:
                    writer.writerow(row)
                writer.writerow(
                    [student_id, subject, score, max_score, date, credits, semester]
                )
                f.flush()
                os.fsync(f.fileno())

            # Atomically replace the target CSV.
            os.replace(tmp_name, path)
            tmp_name = None
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
def cmd_list(args):
    grades = load_grades(args.data)
    if not grades:
        print("No grades found.")
        return

    print(f"{'Student ID':<12} {'Subject':<20} {'Score':<10} {'Max':<8} {'Grade':<6} {'Date':<12}")
    print("-" * 72)
    for g in grades:
        lg = letter_grade(g["score"], g["max_score"])
        print(f"{g['student_id']:<12} {g['subject']:<20} {g['score']:<10.1f} {g['max_score']:<8.1f} {lg:<6} {g['date']:<12}")


def cmd_summary(args):
    grades = load_grades(args.data)
    student_grades = [g for g in grades if g["student_id"] == args.student_id]
    if not student_grades:
        print(f"No records found for student '{args.student_id}'.")
        return

    student_cgpa = cgpa(grades, args.student_id)
    print(f"Summary for Student: {args.student_id}")
    print(f"CGPA: {student_cgpa}")
    print("-" * 50)

    semesters = sorted(list(set(g["semester"] for g in student_grades)))
    for sem in semesters:
        sem_grades = [g for g in student_grades if g["semester"] == sem]
        sem_sgpa = sgpa(grades, args.student_id, sem)
        print(f"Semester {sem} (SGPA: {sem_sgpa})")
        for g in sem_grades:
            lg = letter_grade(g["score"], g["max_score"])
            # Display-only rounding (1 decimal); the grade above is decided
            # from the unrounded percentage, never from this value.
            pct = float(percentage(g["score"], g["max_score"])) if g["max_score"] > 0 else 0
            print(f"  {g['subject']:<18}: {g['score']:.1f}/{g['max_score']:.1f} ({pct:.1f}%) -> {lg} (Credits: {g['credits']})")


def cmd_average(args):
    grades = load_grades(args.data)
    avg = subject_average(grades, args.subject)
    if avg is None:
        print(f"No records found for subject '{args.subject}'.")
    else:
        print(f"Average for '{args.subject}': {avg}")


def cmd_export(args):
    grades = load_grades(args.data)
    output_file = build_html(grades, args.output)
    print(f"HTML report successfully exported to: {output_file}")


def cmd_add(args):
    try:
        validate_grade(args.score, args.max_score, args.date)
        save_grade(args.student_id, args.subject, args.score, args.max_score, args.date, args.credits, args.semester, args.data)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"Added grade for {args.student_id} in {args.subject}.")


def main():
    parser = argparse.ArgumentParser(description="Grade Tracker CLI")
    parser.add_argument("--data", default=DEFAULT_DATA_PATH, help="Path to grades.csv")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # list
    p_list = subparsers.add_parser("list", help="List all grades")
    p_list.set_defaults(func=cmd_list)

    # summary
    p_summary = subparsers.add_parser("summary", help="Show summary for a student")
    p_summary.add_argument("student_id", help="Student ID")
    p_summary.set_defaults(func=cmd_summary)

    # average
    p_avg = subparsers.add_parser("average", help="Show average for a subject")
    p_avg.add_argument("subject", help="Subject name")
    p_avg.set_defaults(func=cmd_average)

    # export
    p_export = subparsers.add_parser("export", help="Export HTML report")
    p_export.add_argument("--output", default="reports/index.html", help="Output file path")
    p_export.set_defaults(func=cmd_export)

    # add
    p_add = subparsers.add_parser("add", help="Add a new grade entry")
    p_add.add_argument("student_id", help="Student ID")
    p_add.add_argument("subject", help="Subject name")
    p_add.add_argument("score", type=float, help="Score earned")
    p_add.add_argument("max_score", type=float, help="Maximum possible score")
    p_add.add_argument("date", help="Date (YYYY-MM-DD)")
    p_add.add_argument("credits", type=float, help="Credits for the subject")
    p_add.add_argument("semester", help="Semester number")
    p_add.set_defaults(func=cmd_add)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
