"""Command Line Interface for Grade Tracker."""

import argparse
import csv
import sys
from pathlib import Path

from grade_tracker.calc import cgpa, letter_grade, sgpa, subject_average, validate_grade
from grade_tracker.export import build_html

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "grades.csv"


def load_grades(csv_path=DEFAULT_DATA_PATH):
    """Loads grades from CSV file and converts score and max_score to floats."""
    path = Path(csv_path)
    if not path.exists():
        return []

    grades = []
    with open(path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row or not row.get("student_id"):
                continue
            try:
                grades.append({
                    "student_id": row["student_id"].strip(),
                    "subject": row["subject"].strip(),
                    "score": float(row["score"]),
                    "max_score": float(row["max_score"]),
                    "date": row.get("date", "").strip(),
                    "credits": float(row.get("credits", 3.0)),
                    "semester": str(row.get("semester", "1")).strip(),
                })
            except (ValueError, KeyError):
                continue
    return grades


def assessment_key(student_id, semester, subject, date):
    """Identity of an assessment within the existing CSV schema.

    A grade row records what happened on (date) in (subject) for one student
    in one (semester). score, max_score, and credits describe the result of
    that assessment; they are not part of its identity. Two rows with the
    same (student_id, semester, subject, date) therefore describe the same
    assessment, and the second row is a duplicate.
    """
    return (student_id.strip(), str(semester).strip(), subject.strip(), date.strip())


def save_grade(student_id, subject, score, max_score, date, credits, semester, csv_path=DEFAULT_DATA_PATH):
    """Appends a new grade row to the CSV file, unless the assessment exists.

    Raises:
        ValueError: If the assessment (student_id + semester + subject + date)
            is already recorded. The file is left unchanged in that case.
    """
    key = assessment_key(student_id, semester, subject, date)
    for grade in load_grades(csv_path):
        if assessment_key(grade["student_id"], grade["semester"], grade["subject"], grade["date"]) == key:
            raise ValueError(
                f"assessment already exists for student '{student_id}' "
                f"in {subject} (semester {semester}) on {date}"
            )

    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()

    with open(path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["student_id", "subject", "score", "max_score", "date", "credits", "semester"])
        writer.writerow([student_id, subject, score, max_score, date, credits, semester])


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
            pct = (g["score"] / g["max_score"] * 100) if g["max_score"] > 0 else 0
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
