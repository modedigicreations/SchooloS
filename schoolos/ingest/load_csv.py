"""Load a directory of normalized CSV exports into the SchoolOS SQLite database.

Expects the files produced by schoolos.data.generate_demo_data (or a school's
own exports in the same shape): teachers.csv, classes.csv, subjects.csv,
class_subject_teacher.csv, students.csv, terms.csv, assessments.csv,
results.csv, attendance.csv.

Usage: python -m schoolos.ingest.load_csv <csv_dir>
"""

import csv
import sqlite3
import sys
from pathlib import Path

from schoolos.db.connection import get_connection, init_db

# Load order matters: parents before the tables that reference them.
TABLES = [
    ("teachers.csv", "teachers", ["id", "name", "subject_specialty"]),
    ("classes.csv", "classes", ["id", "name", "form_teacher_id"]),
    ("subjects.csv", "subjects", ["id", "name"]),
    ("class_subject_teacher.csv", "class_subject_teacher", ["class_id", "subject_id", "teacher_id"]),
    ("students.csv", "students", ["id", "name", "class_id", "gender"]),
    ("terms.csv", "terms", ["id", "name", "sequence"]),
    ("assessments.csv", "assessments", ["id", "class_id", "subject_id", "term_id", "name", "sequence", "max_score"]),
    ("results.csv", "results", ["assessment_id", "student_id", "score"]),
    ("attendance.csv", "attendance", ["student_id", "date", "status", "term_id"]),
]


class IngestError(Exception):
    pass


def _read_rows(path: Path, required_columns: list[str]) -> list[dict]:
    if not path.exists():
        raise IngestError(f"missing required file: {path.name}")
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required_columns if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestError(f"{path.name} is missing columns: {', '.join(missing)}")
        return list(reader)


def load_dir(csv_dir: Path, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    owns_conn = conn is None
    conn = conn or get_connection()
    init_db(conn)
    counts: dict[str, int] = {}
    try:
        cur = conn.cursor()
        for filename, table, columns in TABLES:
            rows = _read_rows(csv_dir / filename, columns)
            placeholders = ", ".join("?" for _ in columns)
            col_list = ", ".join(columns)
            cur.executemany(
                f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})",
                [[row[c] for c in columns] for row in rows],
            )
            counts[table] = len(rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_conn:
            conn.close()
    return counts


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m schoolos.ingest.load_csv <csv_dir>", file=sys.stderr)
        sys.exit(1)
    result = load_dir(Path(sys.argv[1]))
    for table, n in result.items():
        print(f"  {table}: {n} rows")
