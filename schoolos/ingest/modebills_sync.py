"""Sync one school's data from Modebills (app.modeschools.com) into the local
SchoolOS database, via the read-only /api/enterprise/schoolos/* endpoints.

Configuration (environment variables):
    MODEBILLS_BASE_URL              default: https://app.modeschools.com
    MODEBILLS_ORGANIZATION_ID       the school's organization id (uuid) — set
                                     this OR MODEBILLS_ORGANIZATION_SLUG
    MODEBILLS_ORGANIZATION_SLUG     the school's slug, e.g. as it appears in
                                     its own Modebills URL
                                     (app.modeschools.com/<slug>)
    MODECBT_INTER_SERVICE_SECRET    required — shared secret (see Modebills'
                                     src/server/enterprise-auth.helpers.ts)

This mirrors schoolos.ingest.load_csv in spirit (validate/report rather than
crash on one bad row) but writes straight to the database instead of
round-tripping through CSV text, since the API already gives typed JSON.

Usage: python -m schoolos.ingest.modebills_sync [--from YYYY-MM-DD] [--to YYYY-MM-DD]
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from schoolos.db.connection import get_connection, init_db

DEFAULT_BASE_URL = "https://app.modeschools.com"
TIMEOUT_SECONDS = 30


class SyncError(Exception):
    """A structural/connectivity problem — aborts the whole sync."""


@dataclass
class SyncReport:
    loaded: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

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
            lines.append(f"\n{len(self.errors)} problem(s):")
            for e in self.errors[:50]:
                lines.append(f"  {e}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────────────────────────────────

def _fetch(base_url: str, secret: str, path: str, params: dict) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{base_url.rstrip('/')}{path}?{query}"
    req = urllib.request.Request(url, headers={"x-enterprise-secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SyncError(f"{path} returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SyncError(f"could not reach {path}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"{path} returned invalid JSON: {exc}") from exc


def fetch_all(
    base_url: str,
    secret: str,
    organization_id: str | None = None,
    organization_slug: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, list[dict]]:
    """Fetch every endpoint's raw JSON for one organization, identified by its
    raw id or its human-readable slug (e.g. 'the-word-of-promise-high-school',
    as it appears in the school's own Modebills URL). Kept separate from
    transform/load so the API shape and the local schema can each change
    without touching the other."""
    if not organization_id and not organization_slug:
        raise SyncError("either organization_id or organization_slug is required")
    params = {"organizationId": organization_id} if organization_id else {"organizationSlug": organization_slug}
    return {
        "classes": _fetch(base_url, secret, "/api/enterprise/schoolos/classes", params)["classes"],
        "subjects": _fetch(base_url, secret, "/api/enterprise/schoolos/subjects", params)["subjects"],
        "teachers": _fetch(base_url, secret, "/api/enterprise/schoolos/teachers", params)["teachers"],
        "students": _fetch(base_url, secret, "/api/enterprise/schoolos/students", params)["students"],
        "exam_results": _fetch(base_url, secret, "/api/enterprise/schoolos/exam-results", params)["examResults"],
        "attendance": _fetch(
            base_url, secret, "/api/enterprise/schoolos/attendance",
            {**params, "from": from_date, "to": to_date},
        )["attendance"],
    }


# ─────────────────────────────────────────────────────────────────────────
# Transform: Modebills' JSON shape -> SchoolOS's normalized row shape
# ─────────────────────────────────────────────────────────────────────────

def transform_classes(raw: list[dict]) -> list[tuple]:
    return [(c["id"], c["name"], c.get("classTeacherId")) for c in raw]


def transform_subjects(raw: list[dict]) -> list[tuple]:
    return [(s["id"], s["name"], s.get("code")) for s in raw]


def transform_teachers(raw: list[dict]) -> list[tuple]:
    return [(t["id"], t["name"], t.get("employeeNumber"), t.get("status")) for t in raw]


def transform_students(raw: list[dict]) -> list[tuple]:
    return [(s["id"], s["name"], s["classId"], s.get("gender"), s.get("status"), s["admissionNumber"])
            for s in raw]


def _date_only(iso_timestamp: str) -> str:
    return iso_timestamp[:10]


def transform_exam_results(raw: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """Returns (exam_rows, exam_result_rows). An exam with zero attempts still
    produces an exam row (deduplicated by examId) but no exam_result row."""
    exams_by_id: dict[str, tuple] = {}
    results = []
    for row in raw:
        exams_by_id[row["examId"]] = (
            row["examId"], row["classId"], row["subjectId"], row["examTitle"],
            _date_only(row["startTime"]), row["totalMarks"], row.get("examStatus"),
        )
        if row.get("studentId") is not None:
            results.append((row["examId"], row["studentId"], row.get("score"), row.get("attemptStatus")))
    return list(exams_by_id.values()), results


def transform_attendance(raw: list[dict]) -> list[tuple]:
    return [(a["studentId"], a["classId"], _date_only(a["attendanceDate"]), a["status"]) for a in raw]


# ─────────────────────────────────────────────────────────────────────────
# Load
# ─────────────────────────────────────────────────────────────────────────

def _upsert(conn: sqlite3.Connection, table: str, columns: list[str], rows: list[tuple], report: SyncReport) -> None:
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    loaded = 0
    for row in rows:
        try:
            conn.execute(f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})", row)
            loaded += 1
        except sqlite3.Error as exc:
            report.errors.append(f"[{table}] {row!r}: {exc}")
    report.loaded[table] = loaded
    report.skipped[table] = len(rows) - loaded


def sync_organization(
    organization_id: str | None = None,
    organization_slug: str | None = None,
    base_url: str | None = None,
    secret: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> SyncReport:
    base_url = base_url or os.environ.get("MODEBILLS_BASE_URL", DEFAULT_BASE_URL)
    secret = secret or os.environ.get("MODECBT_INTER_SERVICE_SECRET")
    if not secret:
        raise SyncError("MODECBT_INTER_SERVICE_SECRET is not set")

    raw = fetch_all(base_url, secret, organization_id, organization_slug, from_date, to_date)
    exam_rows, exam_result_rows = transform_exam_results(raw["exam_results"])

    owns_conn = conn is None
    conn = conn or get_connection()
    init_db(conn)
    report = SyncReport()
    try:
        # Parent tables before children, same FK order as the schema.
        _upsert(conn, "teachers", ["id", "name", "employee_number", "status"], transform_teachers(raw["teachers"]), report)
        _upsert(conn, "classes", ["id", "name", "class_teacher_id"], transform_classes(raw["classes"]), report)
        _upsert(conn, "subjects", ["id", "name", "code"], transform_subjects(raw["subjects"]), report)
        _upsert(conn, "students", ["id", "name", "class_id", "gender", "status", "admission_number"],
                transform_students(raw["students"]), report)
        _upsert(conn, "exams", ["id", "class_id", "subject_id", "title", "exam_date", "total_marks", "status"],
                exam_rows, report)
        _upsert(conn, "exam_results", ["exam_id", "student_id", "score", "attempt_status"], exam_result_rows, report)
        _upsert(conn, "attendance", ["student_id", "class_id", "date", "status"],
                transform_attendance(raw["attendance"]), report)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_conn:
            conn.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD, limits attendance sync")
    parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD, limits attendance sync")
    args = parser.parse_args()

    org_id = os.environ.get("MODEBILLS_ORGANIZATION_ID")
    org_slug = os.environ.get("MODEBILLS_ORGANIZATION_SLUG")
    if not org_id and not org_slug:
        print("set MODEBILLS_ORGANIZATION_ID or MODEBILLS_ORGANIZATION_SLUG", file=sys.stderr)
        sys.exit(1)

    try:
        result = sync_organization(
            organization_id=org_id, organization_slug=org_slug,
            from_date=args.from_date, to_date=args.to_date,
        )
    except SyncError as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(result.summary())
    if not result.ok:
        sys.exit(1)
