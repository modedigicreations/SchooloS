"""Ground-truth analytics queries over the normalized SchoolOS database.

These functions are the only source of facts the conversational layer is
allowed to state as fact: every number returned here is computed directly
from the ingested data, not produced by a language model.

Multi-tenant: every function takes organization_id and filters by it
directly on every table it queries (not just via a join further up the
chain) — a forgotten join clause can never leak one school's data into
another's answer, because the table actually being read is itself scoped.

Shaped to match what a real school platform actually tracks (see
docs/real-data-ingest-notes.md): there is no "term" — an exam is a
standalone test tied to one class+subject, ordered by date. A teacher is
linked only to a class, as its single "class teacher"; there is no
per-subject teacher assignment.
"""

import sqlite3


class NotFoundError(Exception):
    pass


_SINGULAR = {"classes": "class", "subjects": "subject"}


def _resolve_id(conn: sqlite3.Connection, organization_id: str, table: str, name: str) -> str:
    row = conn.execute(
        f"SELECT id FROM {table} WHERE organization_id = ? AND name = ? COLLATE NOCASE",
        (organization_id, name),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no {_SINGULAR.get(table, table)} named '{name}' in this school")
    return row["id"]


def _check_exists(conn: sqlite3.Connection, organization_id: str, table: str, name: str | None) -> None:
    """Raise NotFoundError on an unmatched name instead of letting a filtered query
    silently return zero rows for a typo'd or misremembered class/subject name."""
    if name is not None:
        _resolve_id(conn, organization_id, table, name)


def resolve_organization_id(conn: sqlite3.Connection, slug: str) -> str:
    """Resolve a school's slug (e.g. 'the-word-of-promise-high-school') to its
    organization_id. Use this once per conversation/session, then pass the
    resulting id to every other function — never let an LLM pass organization_id
    directly, since that would let it ask for a different school's data."""
    row = conn.execute("SELECT id FROM organizations WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise NotFoundError(f"no school with slug '{slug}'")
    return row["id"]


def get_organization(conn: sqlite3.Connection, organization_id: str) -> dict:
    row = conn.execute(
        "SELECT id, name, slug FROM organizations WHERE id = ?", (organization_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no school with id '{organization_id}'")
    return dict(row)


def list_classes(conn: sqlite3.Connection, organization_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name FROM classes WHERE organization_id = ? ORDER BY name", (organization_id,)
    )]


def list_subjects(conn: sqlite3.Connection, organization_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, code FROM subjects WHERE organization_id = ? ORDER BY name", (organization_id,)
    )]


def underperforming_students(
    conn: sqlite3.Connection,
    organization_id: str,
    class_name: str | None = None,
    subject: str | None = None,
    threshold_percent: float = 50.0,
) -> list[dict]:
    """Students whose average percent score (over matching, graded exams) is at or
    below threshold_percent. Ungraded/missing results are not counted."""
    _check_exists(conn, organization_id, "classes", class_name)
    _check_exists(conn, organization_id, "subjects", subject)

    where = ["r.organization_id = ?", "r.score IS NOT NULL"]
    params: list = [organization_id]
    if class_name:
        where.append("cl.name = ? COLLATE NOCASE")
        params.append(class_name)
    if subject:
        where.append("su.name = ? COLLATE NOCASE")
        params.append(subject)
    where_sql = f"WHERE {' AND '.join(where)}"

    sql = f"""
        SELECT st.id AS student_id, st.name AS student_name, cl.name AS class_name,
               AVG(100.0 * r.score / e.total_marks) AS average_percent,
               COUNT(*) AS exams_counted
        FROM exam_results r
        JOIN exams e ON e.id = r.exam_id
        JOIN students st ON st.id = r.student_id
        JOIN classes cl ON cl.id = st.class_id
        JOIN subjects su ON su.id = e.subject_id
        {where_sql}
        GROUP BY st.id
        HAVING average_percent <= ?
        ORDER BY average_percent ASC
    """
    params.append(threshold_percent)
    rows = [dict(r) for r in conn.execute(sql, params)]
    for r in rows:
        r["average_percent"] = round(r["average_percent"], 1)
    return rows


def compare_assessment_performance(
    conn: sqlite3.Connection,
    organization_id: str,
    class_name: str,
    subject: str,
    last_n: int = 3,
) -> list[dict]:
    """Class-average percent score on the most recent `last_n` exams for a
    class+subject, ordered chronologically by exam date."""
    class_id = _resolve_id(conn, organization_id, "classes", class_name)
    subject_id = _resolve_id(conn, organization_id, "subjects", subject)

    sql = """
        SELECT e.id AS exam_id, e.title AS exam_title, e.exam_date, e.total_marks,
               AVG(100.0 * r.score / e.total_marks) AS average_percent,
               SUM(CASE WHEN r.score IS NOT NULL THEN 1 ELSE 0 END) AS students_counted
        FROM exams e
        LEFT JOIN exam_results r ON r.exam_id = e.id
        WHERE e.organization_id = ? AND e.class_id = ? AND e.subject_id = ?
        GROUP BY e.id
        ORDER BY e.exam_date
    """
    rows = [dict(r) for r in conn.execute(sql, (organization_id, class_id, subject_id))]
    for r in rows:
        if r["average_percent"] is not None:
            r["average_percent"] = round(r["average_percent"], 1)
    return rows[-last_n:]


def rank_teacher_improvement(conn: sqlite3.Connection, organization_id: str, top_n: int | None = None) -> list[dict]:
    """Rank teachers by how much their class's (or classes', if they're the class
    teacher of more than one) average percent score improved from its earliest
    graded exam on record to its most recent, per subject, most-improved first.
    Pass top_n to return only the top N instead of every teacher.

    Note: a teacher here means "class teacher" — the platform this data comes
    from has no per-subject teacher assignment, so this reflects the whole
    class's trend across its subjects, not one teacher's own subject.
    """
    sql = """
        SELECT t.id AS teacher_id, t.name AS teacher_name,
               cl.name AS class_name, su.name AS subject_name,
               e.id AS exam_id, e.exam_date,
               AVG(100.0 * r.score / e.total_marks) AS average_percent
        FROM classes cl
        JOIN teachers t ON t.id = cl.class_teacher_id AND t.organization_id = cl.organization_id
        JOIN exams e ON e.class_id = cl.id AND e.organization_id = cl.organization_id
        JOIN subjects su ON su.id = e.subject_id
        JOIN exam_results r ON r.exam_id = e.id AND r.score IS NOT NULL
        WHERE cl.organization_id = ?
        GROUP BY t.id, cl.id, su.id, e.id
        ORDER BY t.id, cl.id, su.id, e.exam_date
    """
    rows = [dict(r) for r in conn.execute(sql, (organization_id,))]

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["teacher_id"], r["teacher_name"], r["class_name"], r["subject_name"])
        groups.setdefault(key, []).append(r)

    assignment_improvements = []
    for (teacher_id, teacher_name, class_name, subject_name), exam_rows in groups.items():
        exam_rows.sort(key=lambda x: x["exam_date"])
        if len(exam_rows) < 2:
            continue
        first, last = exam_rows[0], exam_rows[-1]
        improvement = last["average_percent"] - first["average_percent"]
        assignment_improvements.append({
            "teacher_id": teacher_id,
            "teacher_name": teacher_name,
            "class_name": class_name,
            "subject_name": subject_name,
            "earliest_exam_date": first["exam_date"],
            "earliest_average_percent": round(first["average_percent"], 1),
            "latest_exam_date": last["exam_date"],
            "latest_average_percent": round(last["average_percent"], 1),
            "improvement_percentage_points": round(improvement, 1),
        })

    teacher_totals: dict[str, dict] = {}
    for row in assignment_improvements:
        tid = row["teacher_id"]
        bucket = teacher_totals.setdefault(tid, {"teacher_name": row["teacher_name"], "assignments": []})
        bucket["assignments"].append(row)

    ranked = []
    for tid, bucket in teacher_totals.items():
        avg_improvement = sum(a["improvement_percentage_points"] for a in bucket["assignments"]) / len(bucket["assignments"])
        ranked.append({
            "teacher_id": tid,
            "teacher_name": bucket["teacher_name"],
            "average_improvement_percentage_points": round(avg_improvement, 1),
            "assignments": bucket["assignments"],
        })
    ranked.sort(key=lambda x: x["average_improvement_percentage_points"], reverse=True)
    return ranked[:top_n] if top_n is not None else ranked


def attendance_summary(
    conn: sqlite3.Connection,
    organization_id: str,
    class_name: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    chronic_threshold_percent: float = 85.0,
) -> dict:
    """Overall attendance rate plus the list of students at or below chronic_threshold_percent
    present-rate (candidates for attendance intervention). Optionally scope to one class
    and/or a date range (YYYY-MM-DD, inclusive)."""
    _check_exists(conn, organization_id, "classes", class_name)

    where = ["at.organization_id = ?"]
    params: list = [organization_id]
    if class_name:
        where.append("cl.name = ? COLLATE NOCASE")
        params.append(class_name)
    if from_date:
        where.append("at.date >= ?")
        params.append(from_date)
    if to_date:
        where.append("at.date <= ?")
        params.append(to_date)
    where_sql = f"WHERE {' AND '.join(where)}"

    overall_sql = f"""
        SELECT
            100.0 * SUM(CASE WHEN at.status = 'present' THEN 1 ELSE 0 END) / COUNT(*) AS present_rate,
            COUNT(*) AS days_recorded
        FROM attendance at
        JOIN students st ON st.id = at.student_id
        JOIN classes cl ON cl.id = st.class_id
        {where_sql}
    """
    overall = dict(conn.execute(overall_sql, params).fetchone())
    overall["present_rate"] = round(overall["present_rate"], 1) if overall["present_rate"] is not None else None

    per_student_sql = f"""
        SELECT st.id AS student_id, st.name AS student_name, cl.name AS class_name,
               100.0 * SUM(CASE WHEN at.status = 'present' THEN 1 ELSE 0 END) / COUNT(*) AS present_rate,
               COUNT(*) AS days_recorded
        FROM attendance at
        JOIN students st ON st.id = at.student_id
        JOIN classes cl ON cl.id = st.class_id
        {where_sql}
        GROUP BY st.id
        HAVING present_rate <= ?
        ORDER BY present_rate ASC
    """
    chronic = [dict(r) for r in conn.execute(per_student_sql, params + [chronic_threshold_percent])]
    for r in chronic:
        r["present_rate"] = round(r["present_rate"], 1)

    return {
        "overall_present_rate_percent": overall["present_rate"],
        "days_recorded": overall["days_recorded"],
        "chronic_absence_threshold_percent": chronic_threshold_percent,
        "students_at_or_below_threshold": chronic,
    }


def intervention_candidates(
    conn: sqlite3.Connection,
    organization_id: str,
    class_name: str | None = None,
    subject: str | None = None,
    bottom_percent: float = 10.0,
) -> list[dict]:
    """Bottom `bottom_percent` of students (by overall average percent score, optionally scoped
    to one class/subject) with their score and attendance data, for building intervention plans."""
    _check_exists(conn, organization_id, "classes", class_name)
    _check_exists(conn, organization_id, "subjects", subject)

    where = ["r.organization_id = ?", "r.score IS NOT NULL"]
    params: list = [organization_id]
    if class_name:
        where.append("cl.name = ? COLLATE NOCASE")
        params.append(class_name)
    if subject:
        where.append("su.name = ? COLLATE NOCASE")
        params.append(subject)
    where_sql = f"WHERE {' AND '.join(where)}"

    sql = f"""
        SELECT st.id AS student_id, st.name AS student_name, cl.name AS class_name,
               AVG(100.0 * r.score / e.total_marks) AS average_percent
        FROM exam_results r
        JOIN exams e ON e.id = r.exam_id
        JOIN students st ON st.id = r.student_id
        JOIN classes cl ON cl.id = st.class_id
        JOIN subjects su ON su.id = e.subject_id
        {where_sql}
        GROUP BY st.id
        ORDER BY average_percent ASC
    """
    all_students = [dict(r) for r in conn.execute(sql, params)]
    if not all_students:
        return []
    cutoff = max(1, round(len(all_students) * bottom_percent / 100.0))
    candidates = all_students[:cutoff]

    for c in candidates:
        c["average_percent"] = round(c["average_percent"], 1)
        subject_rows = conn.execute("""
            SELECT su.name AS subject_name, AVG(100.0 * r.score / e.total_marks) AS average_percent
            FROM exam_results r
            JOIN exams e ON e.id = r.exam_id
            JOIN subjects su ON su.id = e.subject_id
            WHERE r.organization_id = ? AND r.student_id = ? AND r.score IS NOT NULL
            GROUP BY su.id
            ORDER BY average_percent ASC
        """, (organization_id, c["student_id"])).fetchall()
        c["by_subject"] = [{"subject": s["subject_name"], "average_percent": round(s["average_percent"], 1)}
                            for s in subject_rows]

        att = conn.execute("""
            SELECT 100.0 * SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) / COUNT(*) AS present_rate
            FROM attendance WHERE organization_id = ? AND student_id = ?
        """, (organization_id, c["student_id"])).fetchone()
        c["attendance_present_rate_percent"] = round(att["present_rate"], 1) if att["present_rate"] is not None else None

    return candidates


def get_class_report_data(
    conn: sqlite3.Connection,
    organization_id: str,
    class_name: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Aggregated facts for drafting a report for one class: overall and
    per-subject averages, top/bottom students, and attendance. Covers every
    graded exam on record by default; optionally scope to a date range
    (YYYY-MM-DD, inclusive) if the school wants a report for one period."""
    class_id = _resolve_id(conn, organization_id, "classes", class_name)

    where = ["e.organization_id = ?", "e.class_id = ?", "r.score IS NOT NULL"]
    params: list = [organization_id, class_id]
    if from_date:
        where.append("e.exam_date >= ?")
        params.append(from_date)
    if to_date:
        where.append("e.exam_date <= ?")
        params.append(to_date)
    where_sql = f"WHERE {' AND '.join(where)}"

    overall = conn.execute(f"""
        SELECT AVG(100.0 * r.score / e.total_marks) AS average_percent, COUNT(DISTINCT r.student_id) AS students_counted
        FROM exam_results r JOIN exams e ON e.id = r.exam_id
        {where_sql}
    """, params).fetchone()

    by_subject = conn.execute(f"""
        SELECT su.name AS subject, AVG(100.0 * r.score / e.total_marks) AS average_percent
        FROM exam_results r
        JOIN exams e ON e.id = r.exam_id
        JOIN subjects su ON su.id = e.subject_id
        {where_sql}
        GROUP BY su.id ORDER BY average_percent DESC
    """, params).fetchall()

    per_student = conn.execute(f"""
        SELECT st.name AS student_name, AVG(100.0 * r.score / e.total_marks) AS average_percent
        FROM exam_results r
        JOIN exams e ON e.id = r.exam_id
        JOIN students st ON st.id = r.student_id
        {where_sql}
        GROUP BY st.id ORDER BY average_percent DESC
    """, params).fetchall()
    per_student = [dict(r) for r in per_student]
    for r in per_student:
        r["average_percent"] = round(r["average_percent"], 1)

    attendance = attendance_summary(conn, organization_id, class_name=class_name, from_date=from_date, to_date=to_date)

    return {
        "class_name": class_name,
        "class_average_percent": round(overall["average_percent"], 1) if overall["average_percent"] is not None else None,
        "students_counted": overall["students_counted"],
        "by_subject": [{"subject": r["subject"], "average_percent": round(r["average_percent"], 1)} for r in by_subject],
        "top_students": per_student[:3],
        "bottom_students": per_student[-3:],
        "attendance": attendance,
    }


def find_student(conn: sqlite3.Connection, organization_id: str, name: str) -> list[dict]:
    """Find students by a (partial, case-insensitive) name match. Returns every
    match so an ambiguous name (e.g. two "Chinedu"s) can be disambiguated by
    student_id before calling get_student_profile."""
    rows = conn.execute("""
        SELECT st.id AS student_id, st.name AS student_name, cl.name AS class_name,
               st.admission_number
        FROM students st
        JOIN classes cl ON cl.id = st.class_id
        WHERE st.organization_id = ? AND st.name LIKE ? COLLATE NOCASE
        ORDER BY st.name
    """, (organization_id, f"%{name}%")).fetchall()
    return [dict(r) for r in rows]


def get_student_profile(conn: sqlite3.Connection, organization_id: str, student_id: str) -> dict:
    """Full profile for one student: overall and per-subject averages, attendance
    rate, and class. Look up student_id with find_student first."""
    student = conn.execute("""
        SELECT st.id, st.name, cl.name AS class_name, st.gender, st.admission_number
        FROM students st JOIN classes cl ON cl.id = st.class_id
        WHERE st.organization_id = ? AND st.id = ?
    """, (organization_id, student_id)).fetchone()
    if student is None:
        raise NotFoundError(f"no student with id {student_id} in this school — use find_student to look one up")

    overall = conn.execute("""
        SELECT AVG(100.0 * r.score / e.total_marks) AS average_percent
        FROM exam_results r JOIN exams e ON e.id = r.exam_id
        WHERE r.organization_id = ? AND r.student_id = ? AND r.score IS NOT NULL
    """, (organization_id, student_id)).fetchone()

    by_subject = conn.execute("""
        SELECT su.name AS subject, AVG(100.0 * r.score / e.total_marks) AS average_percent
        FROM exam_results r
        JOIN exams e ON e.id = r.exam_id
        JOIN subjects su ON su.id = e.subject_id
        WHERE r.organization_id = ? AND r.student_id = ? AND r.score IS NOT NULL
        GROUP BY su.id ORDER BY average_percent DESC
    """, (organization_id, student_id)).fetchall()

    att = conn.execute("""
        SELECT 100.0 * SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) / COUNT(*) AS present_rate,
               COUNT(*) AS days_recorded
        FROM attendance WHERE organization_id = ? AND student_id = ?
    """, (organization_id, student_id)).fetchone()

    return {
        "student_id": student["id"],
        "student_name": student["name"],
        "class_name": student["class_name"],
        "gender": student["gender"],
        "admission_number": student["admission_number"],
        "overall_average_percent": round(overall["average_percent"], 1) if overall["average_percent"] is not None else None,
        "by_subject": [{"subject": r["subject"], "average_percent": round(r["average_percent"], 1)} for r in by_subject],
        "attendance_present_rate_percent": round(att["present_rate"], 1) if att["present_rate"] is not None else None,
        "attendance_days_recorded": att["days_recorded"],
    }


def compare_classes_on_subject(conn: sqlite3.Connection, organization_id: str, subject: str) -> list[dict]:
    """Compare every class's average percent score on one subject, strongest first,
    across all graded exams on record."""
    subject_id = _resolve_id(conn, organization_id, "subjects", subject)

    sql = """
        SELECT cl.name AS class_name,
               AVG(100.0 * r.score / e.total_marks) AS average_percent,
               COUNT(DISTINCT r.student_id) AS students_counted
        FROM exam_results r
        JOIN exams e ON e.id = r.exam_id
        JOIN classes cl ON cl.id = e.class_id
        WHERE r.organization_id = ? AND e.subject_id = ? AND r.score IS NOT NULL
        GROUP BY cl.id
        ORDER BY average_percent DESC
    """
    rows = [dict(r) for r in conn.execute(sql, (organization_id, subject_id))]
    for r in rows:
        r["average_percent"] = round(r["average_percent"], 1)
    return rows
