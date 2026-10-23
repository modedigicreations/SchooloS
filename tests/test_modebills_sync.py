"""Tests for the Modebills sync transform + load logic.

No live Modebills access is available yet, so these exercise the pure
transform functions and the full sync_organization flow against an
in-memory SQLite db, using mock JSON shaped exactly like the real
/api/enterprise/schoolos/* responses (see the Modebills repo,
src/routes/api/enterprise/schoolos/*.ts, for the actual response shape).

Run: python -m tests.test_modebills_sync
"""

import sqlite3
from unittest.mock import patch

from schoolos.analytics import queries as q
from schoolos.ingest import modebills_sync as sync

MOCK_RAW = {
    "classes": [
        {"id": "org1-class-a", "name": "Primary 5", "classTeacherId": "org1-teacher-1", "classTeacherName": "Mrs. Bello"},
        {"id": "org1-class-b", "name": "Primary 6", "classTeacherId": None, "classTeacherName": None},
    ],
    "subjects": [
        {"id": "org1-subj-math", "name": "Mathematics", "code": "MTH"},
        {"id": "org1-subj-eng", "name": "English Language", "code": "ENG"},
    ],
    "teachers": [
        {"id": "org1-teacher-1", "name": "Mrs. Bello", "employeeNumber": "EMP-01", "status": "active"},
    ],
    "students": [
        {"id": "org1-student-1", "name": "Kelechi Chukwu", "admissionNumber": "ORG1/0001",
         "classId": "org1-class-a", "gender": "male", "status": "active"},
        {"id": "org1-student-2", "name": "Amina Yusuf", "admissionNumber": "ORG1/0002",
         "classId": "org1-class-a", "gender": "female", "status": "active"},
    ],
    "exam_results": [
        {"examId": "org1-exam-1", "examTitle": "Maths CBT 1", "classId": "org1-class-a",
         "subjectId": "org1-subj-math", "subjectName": "Mathematics", "totalMarks": 100,
         "startTime": "2025-02-10T09:00:00.000Z", "examStatus": "completed",
         "studentId": "org1-student-1", "score": 72, "attemptStatus": "graded"},
        {"examId": "org1-exam-1", "examTitle": "Maths CBT 1", "classId": "org1-class-a",
         "subjectId": "org1-subj-math", "subjectName": "Mathematics", "totalMarks": 100,
         "startTime": "2025-02-10T09:00:00.000Z", "examStatus": "completed",
         "studentId": "org1-student-2", "score": 88, "attemptStatus": "graded"},
        # An exam with zero attempts: studentId/score/attemptStatus all null.
        {"examId": "org1-exam-2", "examTitle": "English CBT 1", "classId": "org1-class-a",
         "subjectId": "org1-subj-eng", "subjectName": "English Language", "totalMarks": 50,
         "startTime": "2025-03-01T09:00:00.000Z", "examStatus": "published",
         "studentId": None, "score": None, "attemptStatus": None},
    ],
    "attendance": [
        {"studentId": "org1-student-1", "classId": "org1-class-a", "attendanceDate": "2025-02-10T00:00:00.000Z", "status": "present"},
        {"studentId": "org1-student-2", "classId": "org1-class-a", "attendanceDate": "2025-02-10T00:00:00.000Z", "status": "absent"},
    ],
}


def test_transform_classes_handles_null_class_teacher():
    rows = sync.transform_classes(MOCK_RAW["classes"])
    assert rows[0] == ("org1-class-a", "Primary 5", "org1-teacher-1")
    assert rows[1] == ("org1-class-b", "Primary 6", None)


def test_transform_exam_results_splits_exams_from_results():
    exam_rows, result_rows = sync.transform_exam_results(MOCK_RAW["exam_results"])
    # Two distinct exam ids, deduplicated despite 3 raw rows.
    assert {e[0] for e in exam_rows} == {"org1-exam-1", "org1-exam-2"}
    exam2 = next(e for e in exam_rows if e[0] == "org1-exam-2")
    assert exam2 == ("org1-exam-2", "org1-class-a", "org1-subj-eng", "English CBT 1", "2025-03-01", 50, "published")
    # The zero-attempt exam produced no exam_result row.
    assert len(result_rows) == 2
    assert all(r[0] == "org1-exam-1" for r in result_rows)


def test_transform_attendance_takes_date_only():
    rows = sync.transform_attendance(MOCK_RAW["attendance"])
    assert rows[0] == ("org1-student-1", "org1-class-a", "2025-02-10", "present")


def test_sync_organization_loads_and_is_queryable():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with patch.object(sync, "fetch_all", return_value=MOCK_RAW):
        report = sync.sync_organization("org1", secret="test-secret", conn=conn)

    assert report.ok, report.summary()
    assert report.loaded == {
        "teachers": 1, "classes": 2, "subjects": 2, "students": 2, "exams": 2,
        "exam_results": 2, "attendance": 2,
    }

    # Full round-trip through the real analytics layer.
    classes = q.list_classes(conn)
    assert {c["name"] for c in classes} == {"Primary 5", "Primary 6"}

    profile = q.get_student_profile(conn, "org1-student-1")
    assert profile["student_name"] == "Kelechi Chukwu"
    assert profile["overall_average_percent"] == 72.0

    attendance = q.attendance_summary(conn, class_name="Primary 5")
    assert attendance["overall_present_rate_percent"] == 50.0

    conn.close()


def test_fetch_all_sends_slug_param_when_no_id_given():
    captured = []
    with patch.object(sync, "_fetch", side_effect=lambda base, secret, path, params: captured.append((path, params)) or {
        "classes": [], "subjects": [], "teachers": [], "students": [], "examResults": [], "attendance": [],
    }):
        sync.fetch_all("http://x", "secret", organization_slug="the-word-of-promise-high-school")
    assert all(p.get("organizationSlug") == "the-word-of-promise-high-school" for _, p in captured)
    assert all("organizationId" not in p for _, p in captured)


def test_fetch_all_requires_id_or_slug():
    try:
        sync.fetch_all("http://x", "secret")
        raise AssertionError("expected SyncError")
    except sync.SyncError as exc:
        assert "organization_id or organization_slug" in str(exc)


def test_sync_organization_requires_secret():
    try:
        sync.sync_organization("org1", secret=None)
        raise AssertionError("expected SyncError")
    except sync.SyncError as exc:
        assert "SCHOOLOS_INTER_SERVICE_SECRET" in str(exc)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
