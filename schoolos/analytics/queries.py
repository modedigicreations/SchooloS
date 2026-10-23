"""Ground-truth analytics queries over the normalized SchoolOS database.

These functions are the only source of facts the conversational layer is
allowed to state as fact: every number returned here is computed directly
from the ingested data, not produced by a language model.
"""

import sqlite3
from dataclasses import dataclass


class NotFoundError(Exception):
    pass


def _resolve_id(conn: sqlite3.Connection, table: str, name: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row is None:
        raise NotFoundError(f"no {table[:-1]} named '{name}'")
    return row["id"]


def list_classes(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name FROM classes ORDER BY id")]


def list_subjects(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name FROM subjects ORDER BY id")]


def list_terms(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name, sequence FROM terms ORDER BY sequence")]


def underperforming_students(
    conn: sqlite3.Connection,
    class_name: str | None = None,
    subject: str | None = None,
    term: str | None = None,
    threshold_percent: float = 50.0,
) -> list[dict]:
    """Students whose average percent score (over matching assessments) is at or below threshold_percent."""
    where = []
    params: list = []
    if class_name:
        where.append("cl.name = ? COLLATE NOCASE")
        params.append(class_name)
    if subject:
        where.append("su.name = ? COLLATE NOCASE")
        params.append(subject)
    if term:
        where.append("te.name = ? COLLATE NOCASE")
        params.append(term)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = f"""
        SELECT st.id AS student_id, st.name AS student_name, cl.name AS class_name,
               AVG(100.0 * r.score / a.max_score) AS average_percent,
               COUNT(*) AS assessments_counted
        FROM results r
        JOIN assessments a ON a.id = r.assessment_id
        JOIN students st ON st.id = r.student_id
        JOIN classes cl ON cl.id = st.class_id
        JOIN subjects su ON su.id = a.subject_id
        JOIN terms te ON te.id = a.term_id
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
    class_name: str,
    subject: str,
    last_n: int = 3,
) -> list[dict]:
    """Class-average percent score on the most recent `last_n` assessments for a class+subject,
    ordered chronologically (term sequence, then assessment sequence within term)."""
    class_id = _resolve_id(conn, "classes", class_name)
    subject_id = _resolve_id(conn, "subjects", subject)

    sql = """
        SELECT a.id AS assessment_id, te.name AS term, te.sequence AS term_sequence,
               a.name AS assessment_name, a.sequence AS assessment_sequence, a.max_score,
               AVG(100.0 * r.score / a.max_score) AS average_percent,
               COUNT(r.student_id) AS students_counted
        FROM assessments a
        JOIN terms te ON te.id = a.term_id
        LEFT JOIN results r ON r.assessment_id = a.id
        WHERE a.class_id = ? AND a.subject_id = ?
        GROUP BY a.id
        ORDER BY te.sequence, a.sequence
    """
    rows = [dict(r) for r in conn.execute(sql, (class_id, subject_id))]
    for r in rows:
        if r["average_percent"] is not None:
            r["average_percent"] = round(r["average_percent"], 1)
    return rows[-last_n:]


def compare_term_performance(conn: sqlite3.Connection, class_name: str, subject: str) -> list[dict]:
    """Class-average percent score per term for a class+subject, in term order."""
    class_id = _resolve_id(conn, "classes", class_name)
    subject_id = _resolve_id(conn, "subjects", subject)

    sql = """
        SELECT te.name AS term, te.sequence AS term_sequence,
               AVG(100.0 * r.score / a.max_score) AS average_percent,
               COUNT(DISTINCT r.student_id) AS students_counted
        FROM assessments a
        JOIN terms te ON te.id = a.term_id
        JOIN results r ON r.assessment_id = a.id
        WHERE a.class_id = ? AND a.subject_id = ?
        GROUP BY te.id
        ORDER BY te.sequence
    """
    rows = [dict(r) for r in conn.execute(sql, (class_id, subject_id))]
    for r in rows:
        r["average_percent"] = round(r["average_percent"], 1)
    return rows


def rank_teacher_improvement(conn: sqlite3.Connection) -> list[dict]:
    """Rank teachers by how much their classes' average percent score improved from the
    first term on record to the last, across every class+subject they teach."""
    sql = """
        SELECT t.id AS teacher_id, t.name AS teacher_name,
               cl.name AS class_name, su.name AS subject_name,
               te.sequence AS term_sequence, te.name AS term_name,
               AVG(100.0 * r.score / a.max_score) AS average_percent
        FROM class_subject_teacher cst
        JOIN teachers t ON t.id = cst.teacher_id
        JOIN classes cl ON cl.id = cst.class_id
        JOIN subjects su ON su.id = cst.subject_id
        JOIN assessments a ON a.class_id = cst.class_id AND a.subject_id = cst.subject_id
        JOIN terms te ON te.id = a.term_id
        JOIN results r ON r.assessment_id = a.id
        GROUP BY t.id, cl.id, su.id, te.id
        ORDER BY t.id, cl.id, su.id, te.sequence
    """
    rows = [dict(r) for r in conn.execute(sql)]

    # Group by (teacher, class, subject) and compute first-term vs last-term improvement.
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["teacher_id"], r["teacher_name"], r["class_name"], r["subject_name"])
        groups.setdefault(key, []).append(r)

    assignment_improvements = []
    for (teacher_id, teacher_name, class_name, subject_name), terms_rows in groups.items():
        terms_rows.sort(key=lambda x: x["term_sequence"])
        if len(terms_rows) < 2:
            continue
        first, last = terms_rows[0], terms_rows[-1]
        improvement = last["average_percent"] - first["average_percent"]
        assignment_improvements.append({
            "teacher_id": teacher_id,
            "teacher_name": teacher_name,
            "class_name": class_name,
            "subject_name": subject_name,
            "first_term": first["term_name"],
            "first_term_average_percent": round(first["average_percent"], 1),
            "last_term": last["term_name"],
            "last_term_average_percent": round(last["average_percent"], 1),
            "improvement_percentage_points": round(improvement, 1),
        })

    teacher_totals: dict[int, dict] = {}
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
    return ranked


def attendance_summary(
    conn: sqlite3.Connection,
    class_name: str | None = None,
    term: str | None = None,
    chronic_threshold_percent: float = 85.0,
) -> dict:
    """Overall attendance rate plus the list of students at or below chronic_threshold_percent
    present-rate (candidates for attendance intervention)."""
    where = []
    params: list = []
    if class_name:
        where.append("cl.name = ? COLLATE NOCASE")
        params.append(class_name)
    if term:
        where.append("te.name = ? COLLATE NOCASE")
        params.append(term)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    overall_sql = f"""
        SELECT
            100.0 * SUM(CASE WHEN at.status = 'present' THEN 1 ELSE 0 END) / COUNT(*) AS present_rate,
            COUNT(*) AS days_recorded
        FROM attendance at
        JOIN students st ON st.id = at.student_id
        JOIN classes cl ON cl.id = st.class_id
        JOIN terms te ON te.id = at.term_id
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
        JOIN terms te ON te.id = at.term_id
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
    class_name: str | None = None,
    subject: str | None = None,
    bottom_percent: float = 10.0,
) -> list[dict]:
    """Bottom `bottom_percent` of students (by overall average percent score, optionally scoped
    to one class/subject) with their score and attendance data, for building intervention plans."""
    where = []
    params: list = []
    if class_name:
        where.append("cl.name = ? COLLATE NOCASE")
        params.append(class_name)
    if subject:
        where.append("su.name = ? COLLATE NOCASE")
        params.append(subject)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = f"""
        SELECT st.id AS student_id, st.name AS student_name, cl.name AS class_name,
               AVG(100.0 * r.score / a.max_score) AS average_percent
        FROM results r
        JOIN assessments a ON a.id = r.assessment_id
        JOIN students st ON st.id = r.student_id
        JOIN classes cl ON cl.id = st.class_id
        JOIN subjects su ON su.id = a.subject_id
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
            SELECT su.name AS subject_name, AVG(100.0 * r.score / a.max_score) AS average_percent
            FROM results r
            JOIN assessments a ON a.id = r.assessment_id
            JOIN subjects su ON su.id = a.subject_id
            WHERE r.student_id = ?
            GROUP BY su.id
            ORDER BY average_percent ASC
        """, (c["student_id"],)).fetchall()
        c["by_subject"] = [{"subject": s["subject_name"], "average_percent": round(s["average_percent"], 1)}
                            for s in subject_rows]

        att = conn.execute("""
            SELECT 100.0 * SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) / COUNT(*) AS present_rate
            FROM attendance WHERE student_id = ?
        """, (c["student_id"],)).fetchone()
        c["attendance_present_rate_percent"] = round(att["present_rate"], 1) if att["present_rate"] is not None else None

    return candidates


def term_report_data(conn: sqlite3.Connection, class_name: str, term: str) -> dict:
    """Aggregated facts for drafting an end-of-term report for one class."""
    class_id = _resolve_id(conn, "classes", class_name)
    term_id = _resolve_id(conn, "terms", term)

    overall = conn.execute("""
        SELECT AVG(100.0 * r.score / a.max_score) AS average_percent, COUNT(DISTINCT r.student_id) AS students_counted
        FROM results r JOIN assessments a ON a.id = r.assessment_id
        WHERE a.class_id = ? AND a.term_id = ?
    """, (class_id, term_id)).fetchone()

    by_subject = conn.execute("""
        SELECT su.name AS subject, AVG(100.0 * r.score / a.max_score) AS average_percent
        FROM results r
        JOIN assessments a ON a.id = r.assessment_id
        JOIN subjects su ON su.id = a.subject_id
        WHERE a.class_id = ? AND a.term_id = ?
        GROUP BY su.id ORDER BY average_percent DESC
    """, (class_id, term_id)).fetchall()

    per_student = conn.execute("""
        SELECT st.name AS student_name, AVG(100.0 * r.score / a.max_score) AS average_percent
        FROM results r
        JOIN assessments a ON a.id = r.assessment_id
        JOIN students st ON st.id = r.student_id
        WHERE a.class_id = ? AND a.term_id = ?
        GROUP BY st.id ORDER BY average_percent DESC
    """, (class_id, term_id)).fetchall()
    per_student = [dict(r) for r in per_student]
    for r in per_student:
        r["average_percent"] = round(r["average_percent"], 1)

    attendance = attendance_summary(conn, class_name=class_name, term=term)

    return {
        "class_name": class_name,
        "term": term,
        "class_average_percent": round(overall["average_percent"], 1) if overall["average_percent"] is not None else None,
        "students_counted": overall["students_counted"],
        "by_subject": [{"subject": r["subject"], "average_percent": round(r["average_percent"], 1)} for r in by_subject],
        "top_students": per_student[:3],
        "bottom_students": per_student[-3:],
        "attendance": attendance,
    }
