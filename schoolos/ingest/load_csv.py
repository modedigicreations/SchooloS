"""Load a directory of normalized CSV exports into the SchoolOS SQLite database.

Expects the files produced by schoolos.data.generate_demo_data (or a school's
own exports in the same shape): teachers.csv, classes.csv, subjects.csv,
class_subject_teacher.csv, students.csv, terms.csv, assessments.csv,
results.csv, attendance.csv.

Real-world exports are messy (typos, blank cells, a stray "AB" where a score
should be, a row referencing a class that doesn't exist). This loader does
NOT abort the whole import on the first bad row: it coerces and validates
each row, skips the ones that fail, and returns a report of what loaded and
what didn't (and why) so a human can go fix the source file.

Usage: python -m schoolos.ingest.load_csv <csv_dir>
"""

import csv
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from schoolos.db.connection import get_connection, init_db

# Load order matters: parents before the tables that reference them. Each
# column is typed so bad values (text in a score column, etc.) are caught
# and reported per-row instead of corrupting the table or crashing the load.
TABLES: list[tuple[str, str, dict[str, type]]] = [
    ("teachers.csv", "teachers", {"id": int, "name": str, "subject_specialty": str}),
    ("classes.csv", "classes", {"id": int, "name": str, "form_teacher_id": int}),
    ("subjects.csv", "subjects", {"id": int, "name": str}),
    ("class_subject_teacher.csv", "class_subject_teacher",
        {"class_id": int, "subject_id": int, "teacher_id": int}),
    ("students.csv", "students",
        {"id": int, "name": str, "class_id": int, "gender": str, "admission_number": str}),
    ("terms.csv", "terms", {"id": int, "name": str, "sequence": int}),
    ("assessments.csv", "assessments",
        {"id": int, "class_id": int, "subject_id": int, "term_id": int, "name": str,
         "sequence": int, "max_score": float}),
    ("results.csv", "results", {"assessment_id": int, "student_id": int, "score": float}),
    ("attendance.csv", "attendance",
        {"student_id": int, "date": str, "status": str, "term_id": int}),
]

# Columns allowed to be blank (ingest as NULL rather than a coercion failure).
NULLABLE_COLUMNS = {"admission_number"}


class IngestError(Exception):
    """A structural problem with the input (missing file or column) — this
    aborts the whole load, since there's no sensible partial result."""


@dataclass
class RowError:
    table: str
    row_number: int  # 1-based, counting from the first data row (header excluded)
    reason: str
    row: dict


@dataclass
class LoadReport:
    loaded: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = []
        for table, n in self.loaded.items():
            skipped = self.skipped.get(table, 0)
            suffix = f" ({skipped} skipped)" if skipped else ""
            lines.append(f"  {table}: {n} loaded{suffix}")
        if self.errors:
            lines.append(f"\n{len(self.errors)} row(s) had problems:")
            for e in self.errors[:50]:
                lines.append(f"  [{e.table} row {e.row_number}] {e.reason}")
            if len(self.errors) > 50:
                lines.append(f"  ... and {len(self.errors) - 50} more")
        return "\n".join(lines)


def _read_rows(path: Path, required_columns: list[str]) -> list[dict]:
    if not path.exists():
        raise IngestError(f"missing required file: {path.name}")
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required_columns if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestError(f"{path.name} is missing columns: {', '.join(missing)}")
        return list(reader)


def _coerce(value: str, column: str, py_type: type):
    if value is None or value.strip() == "":
        if column in NULLABLE_COLUMNS:
            return None
        raise ValueError(f"'{column}' is blank")
    try:
        return py_type(value)
    except (ValueError, TypeError):
        raise ValueError(f"'{column}' value {value!r} is not a valid {py_type.__name__}")


def load_dir(csv_dir: Path, conn: sqlite3.Connection | None = None) -> LoadReport:
    owns_conn = conn is None
    conn = conn or get_connection()
    init_db(conn)
    report = LoadReport()
    try:
        cur = conn.cursor()
        for filename, table, column_types in TABLES:
            columns = list(column_types.keys())
            rows = _read_rows(csv_dir / filename, columns)
            loaded = 0
            for i, row in enumerate(rows, start=1):
                try:
                    values = [_coerce(row.get(c), c, t) for c, t in column_types.items()]
                except ValueError as exc:
                    report.errors.append(RowError(table, i, str(exc), row))
                    continue
                placeholders = ", ".join("?" for _ in columns)
                col_list = ", ".join(columns)
                try:
                    cur.execute(f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})", values)
                    loaded += 1
                except sqlite3.Error as exc:
                    report.errors.append(RowError(table, i, str(exc), row))
            report.loaded[table] = loaded
            report.skipped[table] = len(rows) - loaded
        conn.commit()
    except IngestError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_conn:
            conn.close()
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m schoolos.ingest.load_csv <csv_dir>", file=sys.stderr)
        sys.exit(1)
    result = load_dir(Path(sys.argv[1]))
    print(result.summary())
    if not result.ok:
        sys.exit(1)
