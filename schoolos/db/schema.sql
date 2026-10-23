-- SchoolOS normalized schema. One school per database for the MVP.

CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    subject_specialty TEXT
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    form_teacher_id INTEGER REFERENCES teachers(id)
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS class_subject_teacher (
    class_id INTEGER NOT NULL REFERENCES classes(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    PRIMARY KEY (class_id, subject_id)
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    gender TEXT
);

CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sequence INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    term_id INTEGER NOT NULL REFERENCES terms(id),
    name TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    max_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    assessment_id INTEGER NOT NULL REFERENCES assessments(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    score REAL NOT NULL,
    PRIMARY KEY (assessment_id, student_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    student_id INTEGER NOT NULL REFERENCES students(id),
    date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('present', 'absent', 'late')),
    term_id INTEGER NOT NULL REFERENCES terms(id),
    PRIMARY KEY (student_id, date)
);

CREATE INDEX IF NOT EXISTS idx_results_student ON results(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_assessments_class_subject_term ON assessments(class_id, subject_id, term_id);
