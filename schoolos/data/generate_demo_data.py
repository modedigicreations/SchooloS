"""Generate a synthetic demo dataset for a fictional Nigerian primary school.

Deliberately engineers a few signal patterns (an improving teacher, a declining
teacher, a consistent bottom-10% of students, a handful of chronic absentees)
so that analytics queries over the data produce meaningful, demo-able answers
instead of pure noise.

Usage: python -m schoolos.data.generate_demo_data [output_dir]
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 42

CLASSES = ["Primary 1", "Primary 2", "Primary 3", "Primary 4", "Primary 5", "Primary 6"]
SUBJECTS = ["Mathematics", "English Language", "Basic Science", "Social Studies"]
TERMS = [("2025/2026 Term 1", 1), ("2025/2026 Term 2", 2), ("2025/2026 Term 3", 3)]
ASSESSMENT_PLAN = [("CA1", 1, 20.0), ("CA2", 2, 20.0), ("Exam", 3, 60.0)]
STUDENTS_PER_CLASS = 25
SCHOOL_DAYS_PER_TERM = 55

FIRST_NAMES_M = [
    "Adebayo", "Chinedu", "Emeka", "Oluwaseun", "Ibrahim", "Tunde", "Chukwuemeka",
    "Usman", "Kelechi", "Segun", "Yusuf", "Obinna", "Femi", "Musa", "Chidi",
    "Abubakar", "Damilola", "Ikechukwu", "Lukman", "Olumide",
]
FIRST_NAMES_F = [
    "Ngozi", "Amina", "Folake", "Chiamaka", "Aisha", "Bimpe", "Blessing", "Zainab",
    "Ifeoma", "Temitope", "Hauwa", "Adaeze", "Fatima", "Yetunde", "Nkem",
    "Rahama", "Oluwatobi", "Chinyere", "Maryam", "Simisola",
]
LAST_NAMES = [
    "Okafor", "Adeyemi", "Mohammed", "Eze", "Balogun", "Nwosu", "Abdullahi",
    "Okonkwo", "Bello", "Afolabi", "Chukwu", "Suleiman", "Ogunleye", "Nwankwo",
    "Danjuma", "Olawale", "Ibekwe", "Garba", "Adesanya", "Umeh",
]


def make_name(rng: random.Random) -> tuple[str, str]:
    gender = rng.choice(["M", "F"])
    first = rng.choice(FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F)
    last = rng.choice(LAST_NAMES)
    return f"{first} {last}", gender


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def generate(out_dir: Path) -> None:
    rng = random.Random(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- teachers -----------------------------------------------------
    teachers = []
    for i in range(1, 11):
        name, _ = make_name(rng)
        teachers.append({"id": i, "name": f"Mrs. {name}" if i % 2 else f"Mr. {name}",
                          "subject_specialty": rng.choice(SUBJECTS)})

    # Pick two specific teachers to carry an engineered trend:
    improving_teacher_id = teachers[0]["id"]
    declining_teacher_id = teachers[1]["id"]

    # --- classes --------------------------------------------------------
    classes = []
    form_teacher_cycle = teachers[:]
    rng.shuffle(form_teacher_cycle)
    for i, cname in enumerate(CLASSES, start=1):
        classes.append({"id": i, "name": cname,
                         "form_teacher_id": form_teacher_cycle[i % len(form_teacher_cycle)]["id"]})

    # --- subjects ---------------------------------------------------------
    subjects = [{"id": i, "name": s} for i, s in enumerate(SUBJECTS, start=1)]

    # --- class_subject_teacher: assign a teacher to each class+subject ----
    class_subject_teacher = []
    for c in classes:
        for s in subjects:
            # Force the improving/declining signal onto Primary 5 Mathematics
            # and Primary 3 English respectively; randomize the rest.
            if c["name"] == "Primary 5" and s["name"] == "Mathematics":
                teacher_id = improving_teacher_id
            elif c["name"] == "Primary 3" and s["name"] == "English Language":
                teacher_id = declining_teacher_id
            else:
                teacher_id = rng.choice(teachers)["id"]
            class_subject_teacher.append({"class_id": c["id"], "subject_id": s["id"], "teacher_id": teacher_id})

    # --- terms --------------------------------------------------------------
    terms = [{"id": i, "name": name, "sequence": seq} for i, (name, seq) in enumerate(TERMS, start=1)]

    # --- students -------------------------------------------------------------
    students = []
    student_id = 1
    # Reserve a consistent "bottom 10%" per class with a low ability baseline,
    # and a handful of chronic absentees (overlapping only partially with the
    # low performers, since that's realistic and more interesting to query).
    student_profile = {}  # student_id -> dict(ability, chronic_absentee)
    for c in classes:
        class_student_ids = []
        for _ in range(STUDENTS_PER_CLASS):
            name, gender = make_name(rng)
            students.append({"id": student_id, "name": name, "class_id": c["id"], "gender": gender})
            class_student_ids.append(student_id)
            student_id += 1

        n_low = max(2, round(STUDENTS_PER_CLASS * 0.10))
        low_ids = set(rng.sample(class_student_ids, n_low))
        n_chronic = max(1, round(STUDENTS_PER_CLASS * 0.08))
        chronic_ids = set(rng.sample(class_student_ids, n_chronic))

        for sid in class_student_ids:
            if sid in low_ids:
                ability = rng.uniform(0.30, 0.45)
            else:
                ability = rng.uniform(0.55, 0.92)
            student_profile[sid] = {
                "ability": ability,
                "chronic_absentee": sid in chronic_ids,
            }

    # --- assessments -----------------------------------------------------------
    assessments = []
    assessment_id = 1
    # assessment lookup for result generation: (class_id, subject_id, term_id) -> [(id, max_score, seq), ...]
    assessments_by_group: dict[tuple[int, int, int], list[tuple[int, float, int]]] = {}
    for c in classes:
        for s in subjects:
            for t in terms:
                for name, seq, max_score in ASSESSMENT_PLAN:
                    assessments.append({"id": assessment_id, "class_id": c["id"], "subject_id": s["id"],
                                         "term_id": t["id"], "name": name, "sequence": seq, "max_score": max_score})
                    assessments_by_group.setdefault((c["id"], s["id"], t["id"]), []).append(
                        (assessment_id, max_score, seq))
                    assessment_id += 1

    # --- results ----------------------------------------------------------------
    results = []
    cst_lookup = {(row["class_id"], row["subject_id"]): row["teacher_id"] for row in class_subject_teacher}
    for c in classes:
        class_students = [st for st in students if st["class_id"] == c["id"]]
        for s in subjects:
            teacher_id = cst_lookup[(c["id"], s["id"])]
            for t in terms:
                # Term-level trend multiplier driven by the engineered teacher signal.
                if teacher_id == improving_teacher_id:
                    trend = 1.0 + 0.10 * (t["sequence"] - 1)   # +0%, +10%, +20%
                elif teacher_id == declining_teacher_id:
                    trend = 1.0 - 0.10 * (t["sequence"] - 1)   # 0%, -10%, -20%
                else:
                    trend = 1.0 + rng.uniform(-0.03, 0.03)

                for assess_id, max_score, _seq in assessments_by_group[(c["id"], s["id"], t["id"])]:
                    for st in class_students:
                        ability = student_profile[st["id"]]["ability"]
                        noise = rng.uniform(-0.08, 0.08)
                        fraction = clamp(ability * trend + noise, 0.05, 1.0)
                        score = round(fraction * max_score, 1)
                        results.append({"assessment_id": assess_id, "student_id": st["id"], "score": score})

    # --- attendance -----------------------------------------------------------------
    attendance = []
    for t in terms:
        start = date(2025, 1 + 4 * (t["sequence"] - 1), 6)
        day_count = 0
        current = start
        while day_count < SCHOOL_DAYS_PER_TERM:
            if current.weekday() < 5:  # Mon-Fri only
                for st in students:
                    profile = student_profile[st["id"]]
                    absence_rate = 0.25 if profile["chronic_absentee"] else 0.04
                    late_rate = 0.05
                    roll = rng.random()
                    if roll < absence_rate:
                        status = "absent"
                    elif roll < absence_rate + late_rate:
                        status = "late"
                    else:
                        status = "present"
                    attendance.append({"student_id": st["id"], "date": current.isoformat(),
                                        "status": status, "term_id": t["id"]})
                day_count += 1
            current += timedelta(days=1)

    _write_csv(out_dir / "teachers.csv", teachers, ["id", "name", "subject_specialty"])
    _write_csv(out_dir / "classes.csv", classes, ["id", "name", "form_teacher_id"])
    _write_csv(out_dir / "subjects.csv", subjects, ["id", "name"])
    _write_csv(out_dir / "class_subject_teacher.csv", class_subject_teacher,
               ["class_id", "subject_id", "teacher_id"])
    _write_csv(out_dir / "students.csv", students, ["id", "name", "class_id", "gender"])
    _write_csv(out_dir / "terms.csv", terms, ["id", "name", "sequence"])
    _write_csv(out_dir / "assessments.csv", assessments,
               ["id", "class_id", "subject_id", "term_id", "name", "sequence", "max_score"])
    _write_csv(out_dir / "results.csv", results, ["assessment_id", "student_id", "score"])
    _write_csv(out_dir / "attendance.csv", attendance, ["student_id", "date", "status", "term_id"])

    print(f"Wrote demo dataset to {out_dir} "
          f"({len(students)} students, {len(results)} results, {len(attendance)} attendance records)")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "data" / "demo"
    generate(target)
