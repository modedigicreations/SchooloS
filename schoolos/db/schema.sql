-- SchoolOS normalized schema. Multi-tenant: one shared database holds many
-- schools' data, every table scoped by organization_id, so one deployment
-- can serve every registered school rather than one deployment per school.
-- Every query in schoolos/analytics/queries.py takes organization_id and
-- filters by it directly (not just via a join) — defense in depth so a
-- forgotten join clause can never leak one school's data into another's
-- answer.
--
-- IDs are TEXT, not INTEGER: when data is synced from a real school platform
-- (e.g. Modebills), its own UUID ids are used directly as these ids, so a
-- re-sync is a plain upsert with no id-translation layer. The CSV/demo-data
-- path just generates its own string ids the same way.
--
-- Shaped to match what a real platform actually tracks (see
-- docs/real-data-ingest-notes.md): there is no "term" concept — an exam is a
-- standalone test tied to one class+subject, ordered by date, not grouped
-- into CA1/CA2/Exam-per-term. A teacher is linked only to a class, as its
-- single "class teacher" — there is no per-subject teacher assignment.

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS teachers (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    employee_number TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS classes (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    class_teacher_id TEXT REFERENCES teachers(id),
    UNIQUE (organization_id, name)
);

CREATE TABLE IF NOT EXISTS subjects (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    code TEXT,
    UNIQUE (organization_id, name)
);

CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    class_id TEXT NOT NULL REFERENCES classes(id),
    gender TEXT,
    status TEXT,
    -- A school's own admission/registration number — unique within a school,
    -- not globally (two schools can both have admission number "0001").
    admission_number TEXT,
    UNIQUE (organization_id, admission_number)
);

CREATE TABLE IF NOT EXISTS exams (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    class_id TEXT NOT NULL REFERENCES classes(id),
    subject_id TEXT NOT NULL REFERENCES subjects(id),
    title TEXT NOT NULL,
    exam_date TEXT NOT NULL,
    total_marks REAL NOT NULL,
    status TEXT
);

CREATE TABLE IF NOT EXISTS exam_results (
    exam_id TEXT NOT NULL REFERENCES exams(id),
    student_id TEXT NOT NULL REFERENCES students(id),
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    -- Nullable: a real exam can exist with nobody graded (or nobody attempted)
    -- yet. A row with a null score means "no result recorded", not zero.
    score REAL,
    attempt_status TEXT,
    PRIMARY KEY (exam_id, student_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    student_id TEXT NOT NULL REFERENCES students(id),
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    class_id TEXT NOT NULL REFERENCES classes(id),
    date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('present', 'absent', 'late', 'excused')),
    PRIMARY KEY (student_id, date)
);

CREATE INDEX IF NOT EXISTS idx_teachers_org ON teachers(organization_id);
CREATE INDEX IF NOT EXISTS idx_classes_org ON classes(organization_id);
CREATE INDEX IF NOT EXISTS idx_subjects_org ON subjects(organization_id);
CREATE INDEX IF NOT EXISTS idx_students_org ON students(organization_id);
CREATE INDEX IF NOT EXISTS idx_exams_org ON exams(organization_id);
CREATE INDEX IF NOT EXISTS idx_exam_results_org ON exam_results(organization_id);
CREATE INDEX IF NOT EXISTS idx_attendance_org ON attendance(organization_id);

CREATE INDEX IF NOT EXISTS idx_exam_results_student ON exam_results(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_exams_class_subject ON exams(class_id, subject_id);
