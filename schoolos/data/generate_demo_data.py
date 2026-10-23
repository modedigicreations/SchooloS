"""Generate a synthetic demo dataset for a fictional Nigerian primary school.

Shaped to match a real school platform's data (see
docs/real-data-ingest-notes.md): string ids (as a real platform's own UUIDs
would be, used directly rather than through an id-translation layer), no
"term" concept (exams are standalone, ordered by date), and teachers linked
only to a class as its single class teacher (no per-subject assignment).

Deliberately engineers a few signal patterns (an improving class, a
declining class, a consistent bottom-10% of students, a handful of chronic
absentees) so that analytics queries over the data produce meaningful,
demo-able answers instead of pure noise.

Usage: python -m schoolos.data.generate_demo_data [output_dir]
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 42

CLASSES = ["Primary 1", "Primary 2", "Primary 3", "Primary 4", "Primary 5", "Primary 6"]
SUBJECTS = [("Mathematics", "MTH"), ("English Language", "ENG"), ("Basic Science", "BSC"), ("Social Studies", "SOS")]
STUDENTS_PER_CLASS = 25
# Three rounds of exams across the school year (no "term" label — just dates).
ROUNDS = [date(2025, 1, 13), date(2025, 5, 12), date(2025, 9, 8)]
EXAMS_PER_ROUND = 3
SCHOOL_DAYS_PER_ROUND = 55

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
    gender = rng.choice(["male", "female"])
    first = rng.choice(FIRST_NAMES_M if gender == "male" else FIRST_NAMES_F)
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
        teachers.append({
            "id": f"teacher-{i}",
            "name": f"Mrs. {name}" if i % 2 else f"Mr. {name}",
            "employee_number": f"EMP-{i:03d}",
            "status": "active",
        })

    # Pick two specific teachers to carry an engineered trend (as class teachers).
    improving_teacher_id = teachers[0]["id"]
    declining_teacher_id = teachers[1]["id"]

    # --- classes: assign class teachers, forcing the trend onto Primary 5 / 3 --
    classes = []
    # Exclude the two engineered teachers so the random assignment below can't
    # coincidentally hand them a second class, which would muddy the signal.
    form_teacher_cycle = [t for t in teachers if t["id"] not in (improving_teacher_id, declining_teacher_id)]
    rng.shuffle(form_teacher_cycle)
    for i, cname in enumerate(CLASSES, start=1):
        if cname == "Primary 5":
            class_teacher_id = improving_teacher_id
        elif cname == "Primary 3":
            class_teacher_id = declining_teacher_id
        else:
            class_teacher_id = form_teacher_cycle[i % len(form_teacher_cycle)]["id"]
        classes.append({"id": f"class-{i}", "name": cname, "class_teacher_id": class_teacher_id})

    # --- subjects ---------------------------------------------------------
    subjects = [{"id": f"subject-{i}", "name": name, "code": code}
                for i, (name, code) in enumerate(SUBJECTS, start=1)]

    # --- students -------------------------------------------------------------
    students = []
    student_seq = 1
    student_profile = {}  # student_id -> dict(ability, chronic_absentee)
    for c in classes:
        class_student_ids = []
        for _ in range(STUDENTS_PER_CLASS):
            name, gender = make_name(rng)
            sid = f"student-{student_seq:04d}"
            students.append({
                "id": sid, "name": name, "class_id": c["id"], "gender": gender,
                "status": "active", "admission_number": f"DEM/2025/{student_seq:04d}",
            })
            class_student_ids.append(sid)
            student_seq += 1

        n_low = max(2, round(STUDENTS_PER_CLASS * 0.10))
        low_ids = set(rng.sample(class_student_ids, n_low))
        n_chronic = max(1, round(STUDENTS_PER_CLASS * 0.08))
        chronic_ids = set(rng.sample(class_student_ids, n_chronic))

        for sid in class_student_ids:
            ability = rng.uniform(0.30, 0.45) if sid in low_ids else rng.uniform(0.55, 0.92)
            student_profile[sid] = {"ability": ability, "chronic_absentee": sid in chronic_ids}

    # --- exams + exam_results ----------------------------------------------
    exams = []
    exam_results = []
    exam_seq = 1
    class_teacher_by_class = {c["id"]: c["class_teacher_id"] for c in classes}

    for c in classes:
        class_students = [st for st in students if st["class_id"] == c["id"]]
        teacher_id = class_teacher_by_class[c["id"]]
        for s in subjects:
            for round_num, round_start in enumerate(ROUNDS, start=1):
                if teacher_id == improving_teacher_id:
                    trend = 1.0 + 0.10 * (round_num - 1)   # +0%, +10%, +20%
                elif teacher_id == declining_teacher_id:
                    trend = 1.0 - 0.10 * (round_num - 1)   # 0%, -10%, -20%
                else:
                    trend = 1.0 + rng.uniform(-0.03, 0.03)

                for n in range(EXAMS_PER_ROUND):
                    exam_id = f"exam-{exam_seq:04d}"
                    exam_date = round_start + timedelta(days=n * 14)
                    total_marks = 100.0 if n == EXAMS_PER_ROUND - 1 else 20.0
                    exams.append({
                        "id": exam_id, "class_id": c["id"], "subject_id": s["id"],
                        "title": f"{s['name']} Round {round_num} Test {n + 1}",
                        "exam_date": exam_date.isoformat(), "total_marks": total_marks,
                        "status": "completed",
                    })
                    for st in class_students:
                        ability = student_profile[st["id"]]["ability"]
                        noise = rng.uniform(-0.08, 0.08)
                        fraction = clamp(ability * trend + noise, 0.05, 1.0)
                        score = round(fraction * total_marks, 1)
                        exam_results.append({
                            "exam_id": exam_id, "student_id": st["id"],
                            "score": score, "attempt_status": "graded",
                        })
                    exam_seq += 1

    # --- attendance -----------------------------------------------------------------
    attendance = []
    for round_start in ROUNDS:
        day_count = 0
        current = round_start
        while day_count < SCHOOL_DAYS_PER_ROUND:
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
                    attendance.append({
                        "student_id": st["id"], "class_id": st["class_id"],
                        "date": current.isoformat(), "status": status,
                    })
                day_count += 1
            current += timedelta(days=1)

    _write_csv(out_dir / "teachers.csv", teachers, ["id", "name", "employee_number", "status"])
    _write_csv(out_dir / "classes.csv", classes, ["id", "name", "class_teacher_id"])
    _write_csv(out_dir / "subjects.csv", subjects, ["id", "name", "code"])
    _write_csv(out_dir / "students.csv", students,
               ["id", "name", "class_id", "gender", "status", "admission_number"])
    _write_csv(out_dir / "exams.csv", exams,
               ["id", "class_id", "subject_id", "title", "exam_date", "total_marks", "status"])
    _write_csv(out_dir / "exam_results.csv", exam_results, ["exam_id", "student_id", "score", "attempt_status"])
    _write_csv(out_dir / "attendance.csv", attendance, ["student_id", "class_id", "date", "status"])

    print(f"Wrote demo dataset to {out_dir} "
          f"({len(students)} students, {len(exam_results)} exam results, {len(attendance)} attendance records)")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "data" / "demo"
    generate(target)
