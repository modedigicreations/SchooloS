"""Tests for the Modebills sync transform + load logic, and for cross-tenant
isolation once two schools' data lives in the same shared database.

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


def make_mock_raw(org_id: str, org_name: str, org_slug: str, name_prefix: str) -> dict:
    """Two schools built from this share the same class/subject NAMES
    ("Primary 5", "Mathematics") on purpose — isolation has to come from
    organization_id filtering, not from names happening to differ."""
    return {
        "organization": {"id": org_id, "name": org_name, "slug": org_slug},
        "classes": [
            {"id": f"{name_prefix}-class-a", "name": "Primary 5",
             "classTeacherId": f"{name_prefix}-teacher-1", "classTeacherName": "Mrs. Bello"},
            {"id": f"{name_prefix}-class-b", "name": "Primary 6", "classTeacherId": None, "classTeacherName": None},
        ],
        "subjects": [
            {"id": f"{name_prefix}-subj-math", "name": "Mathematics", "code": "MTH"},
            {"id": f"{name_prefix}-subj-eng", "name": "English Language", "code": "ENG"},
        ],
        "teachers": [
            {"id": f"{name_prefix}-teacher-1", "name": "Mrs. Bello", "employeeNumber": "EMP-01", "status": "active"},
        ],
        "students": [
            {"id": f"{name_prefix}-student-1", "name": "Kelechi Chukwu", "admissionNumber": "0001",
             "classId": f"{name_prefix}-class-a", "gender": "male", "status": "active"},
            {"id": f"{name_prefix}-student-2", "name": "Amina Yusuf", "admissionNumber": "0002",
             "classId": f"{name_prefix}-class-a", "gender": "female", "status": "active"},
        ],
        "exam_results": [
            {"examId": f"{name_prefix}-exam-1", "examTitle": "Maths CBT 1", "classId": f"{name_prefix}-class-a",
             "subjectId": f"{name_prefix}-subj-math", "subjectName": "Mathematics", "totalMarks": 100,
             "startTime": "2025-02-10T09:00:00.000Z", "examStatus": "completed",
             "studentId": f"{name_prefix}-student-1", "score": 72, "attemptStatus": "graded"},
            {"examId": f"{name_prefix}-exam-1", "examTitle": "Maths CBT 1", "classId": f"{name_prefix}-class-a",
             "subjectId": f"{name_prefix}-subj-math", "subjectName": "Mathematics", "totalMarks": 100,
             "startTime": "2025-02-10T09:00:00.000Z", "examStatus": "completed",
             "studentId": f"{name_prefix}-student-2", "score": 88, "attemptStatus": "graded"},
            # An exam with zero attempts: studentId/score/attemptStatus all null.
            {"examId": f"{name_prefix}-exam-2", "examTitle": "English CBT 1", "classId": f"{name_prefix}-class-a",
             "subjectId": f"{name_prefix}-subj-eng", "subjectName": "English Language", "totalMarks": 50,
             "startTime": "2025-03-01T09:00:00.000Z", "examStatus": "published",
             "studentId": None, "score": None, "attemptStatus": None},
        ],
        "attendance": [
            {"studentId": f"{name_prefix}-student-1", "classId": f"{name_prefix}-class-a",
             "attendanceDate": "2025-02-10T00:00:00.000Z", "status": "present"},
            {"studentId": f"{name_prefix}-student-2", "classId": f"{name_prefix}-class-a",
             "attendanceDate": "2025-02-10T00:00:00.000Z", "status": "absent"},
        ],
    }


MOCK_RAW = make_mock_raw("org1", "Org One School", "org-one", "org1")


def test_transform_classes_handles_null_class_teacher():
    rows = sync.transform_classes(MOCK_RAW["classes"], "org1")
    assert rows[0] == ("org1-class-a", "org1", "Primary 5", "org1-teacher-1")
    assert rows[1] == ("org1-class-b", "org1", "Primary 6", None)


def test_transform_exam_results_splits_exams_from_results():
    exam_rows, result_rows = sync.transform_exam_results(MOCK_RAW["exam_results"], "org1")
    # Two distinct exam ids, deduplicated despite 3 raw rows.
    assert {e[0] for e in exam_rows} == {"org1-exam-1", "org1-exam-2"}
    exam2 = next(e for e in exam_rows if e[0] == "org1-exam-2")
    assert exam2 == ("org1-exam-2", "org1", "org1-class-a", "org1-subj-eng", "English CBT 1", "2025-03-01", 50, "published")
    # The zero-attempt exam produced no exam_result row.
    assert len(result_rows) == 2
    assert all(r[0] == "org1-exam-1" for r in result_rows)
    assert all(r[2] == "org1" for r in result_rows)  # organization_id present on every row


def test_transform_attendance_takes_date_only():
    rows = sync.transform_attendance(MOCK_RAW["attendance"], "org1")
    assert rows[0] == ("org1-student-1", "org1", "org1-class-a", "2025-02-10", "present")


def test_sync_organization_loads_and_is_queryable():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with patch.object(sync, "fetch_all", return_value=MOCK_RAW):
        report = sync.sync_organization("org1", secret="test-secret", conn=conn)

    assert report.ok, report.summary()
    assert report.loaded == {
        "organizations": 1, "teachers": 1, "classes": 2, "subjects": 2, "students": 2,
        "exams": 2, "exam_results": 2, "attendance": 2,
    }

    classes = q.list_classes(conn, "org1")
    assert {c["name"] for c in classes} == {"Primary 5", "Primary 6"}

    profile = q.get_student_profile(conn, "org1", "org1-student-1")
    assert profile["student_name"] == "Kelechi Chukwu"
    assert profile["overall_average_percent"] == 72.0

    attendance = q.attendance_summary(conn, "org1", class_name="Primary 5")
    assert attendance["overall_present_rate_percent"] == 50.0

    conn.close()


def test_cross_tenant_isolation():
    """The actual multi-tenancy proof: two schools, same class/subject/student
    names on purpose, synced into one shared database. Every query scoped to
    org1 must never surface org2's data, and vice versa."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    mock_org1 = make_mock_raw("org1", "Org One School", "org-one", "org1")
    mock_org2 = make_mock_raw("org2", "Org Two School", "org-two", "org2")
    # Give org2 a different score so results are distinguishable, not just same-shaped.
    mock_org2["exam_results"][0]["score"] = 40
    mock_org2["exam_results"][1]["score"] = 30

    with patch.object(sync, "fetch_all", return_value=mock_org1):
        report1 = sync.sync_organization("org1", secret="test-secret", conn=conn)
    with patch.object(sync, "fetch_all", return_value=mock_org2):
        report2 = sync.sync_organization("org2", secret="test-secret", conn=conn)
    assert report1.ok and report2.ok

    # list_classes for org1 must not include org2's class ids, despite identical names.
    org1_classes = q.list_classes(conn, "org1")
    org2_classes = q.list_classes(conn, "org2")
    assert {c["id"] for c in org1_classes} == {"org1-class-a", "org1-class-b"}
    assert {c["id"] for c in org2_classes} == {"org2-class-a", "org2-class-b"}
    assert {c["name"] for c in org1_classes} == {c["name"] for c in org2_classes}  # same names, different schools

    # find_student("Kelechi") scoped to org1 must not return org2's Kelechi.
    org1_matches = q.find_student(conn, "org1", "Kelechi")
    assert [m["student_id"] for m in org1_matches] == ["org1-student-1"]

    # get_student_profile must refuse to fetch another org's student even by exact id.
    try:
        q.get_student_profile(conn, "org1", "org2-student-1")
        raise AssertionError("expected NotFoundError — org1 must not see org2's student")
    except q.NotFoundError:
        pass

    # Same class name, different real averages — proves results are the right school's.
    org1_avg = q.underperforming_students(conn, "org1", class_name="Primary 5", threshold_percent=100)
    org2_avg = q.underperforming_students(conn, "org2", class_name="Primary 5", threshold_percent=100)
    org1_by_id = {r["student_id"]: r["average_percent"] for r in org1_avg}
    org2_by_id = {r["student_id"]: r["average_percent"] for r in org2_avg}
    assert org1_by_id["org1-student-1"] == 72.0
    assert org2_by_id["org2-student-1"] == 40.0
    assert set(org1_by_id) != set(org2_by_id)

    # Attendance summary scoped to org1 must only count org1's days.
    att1 = q.attendance_summary(conn, "org1")
    att2 = q.attendance_summary(conn, "org2")
    assert att1["days_recorded"] == 2
    assert att2["days_recorded"] == 2  # not 4 — org2's scope didn't pick up org1's rows too

    conn.close()


def test_fetch_all_sends_slug_param_when_no_id_given():
    captured = []

    def fake_fetch(base, secret, path, params):
        captured.append((path, params))
        return {
            "organization": {"id": "org1", "name": "X", "slug": "the-word-of-promise-high-school"},
            "classes": [], "subjects": [], "teachers": [], "students": [], "examResults": [], "attendance": [],
        }

    with patch.object(sync, "_fetch", side_effect=fake_fetch):
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
