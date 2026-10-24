# Notes: going from synthetic data to real school data

Scoping/design notes, kept here so a future session doesn't have to
re-derive them. Two real data sources exist now: a non-Modebills school's
own CSV export, and the live [Modebills](https://app.modeschools.com) sync
(`schoolos/ingest/modebills_sync.py`). This file covers what's true of each.

## Modebills: what its real schema actually looks like

Explored directly in the [Modebills repo](https://github.com/modedigicreations/Modebills-)
(`src/db/schema.ts`) when building the sync integration. Three things shape
what SchoolOS can honestly answer from this source, and SchoolOS's schema
was reworked (dropping `terms`, renaming `assessments`/`results` to
`exams`/`exam_results`, switching ids from INTEGER to TEXT) to match:

- **No "term" concept exists anywhere.** An exam is a standalone CBT test
  tied to one class+subject (title, date, total marks) — not CA1/CA2/Exam
  grouped into a term. SchoolOS's tools now order by exam date instead
  (`compare_assessment_performance`'s "last N exams" is chronological, not
  term-scoped; `get_class_report_data` takes an optional date range instead
  of a term).
- **Academic data only covers exams actually run through Modebills' CBT
  module.** There's no general gradebook for paper-based continuous
  assessment. A school that still does CA1/CA2 on paper and only the final
  exam via CBT will show an incomplete picture through this source —
  `get_underperforming_students` etc. will only ever see CBT scores.
- **No per-subject teacher assignment** — a teacher is linked only to a
  class, as its single "class teacher" (`schoolClass.classTeacherId`).
  `rank_teacher_improvement` now means "this class's trend, attributed to
  its class teacher," not "this teacher's own subject." A teacher who is
  class teacher of more than one class gets credited with all of them.
- **Ids are UUIDs, not small integers.** SchoolOS's schema now uses TEXT
  ids throughout, so a real platform's own ids are used directly (sync is a
  plain upsert, no id-translation table needed).

The new `/api/enterprise/schoolos/*` endpoints (added in that repo,
[PR #65](https://github.com/modedigicreations/Modebills-/pull/65), plus a
follow-up) are read-only and shared-secret authenticated — but via their own
`SCHOOLOS_INTER_SERVICE_SECRET`, not Modebills' `MODECBT_INTER_SERVICE_SECRET`
(that one authenticates ModeCBT, a different product entirely — external
examinations, no relationship to SchoolOS). One secret is trusted for any
school's `organizationId`/`organizationSlug`, same blast radius as the
existing `verify-student`/`sso` pattern it's modeled on. A tighter per-school
API key is a possible future hardening, not yet built.

**Not yet verified against live data** — `modebills_sync.py`'s transform
and load logic is tested against mock JSON shaped exactly like the real
endpoint responses (`tests/test_modebills_sync.py`), but nobody has run it
against an actual deployed organization yet. First real test needs a test
school's `organizationId` and the shared secret. (A real attempt at this
got blocked entirely on recovering access to inherited server
infrastructure — not a code problem. See the session notes for that saga;
the short version is: the live server is real and serving real schools, so
don't touch it without knowing exactly what you're doing, and the
multi-tenancy work below doesn't depend on resolving that at all.)

A 7th endpoint, `/api/enterprise/schoolos/organization`, returns a school's
own name/slug — added because the other 6 return rows scoped to an org but
never the org's own display name, which SchoolOS needs now that its
database holds more than one school at once (see below).

## SchoolOS's own database is multi-tenant

One shared SQLite database holds every synced/imported school's data, not
one database per school. Every table carries `organization_id`, every query
function in `schoolos/analytics/queries.py` takes it as an explicit
argument and filters by it directly (not just via a join further up the
chain — a forgotten join elsewhere can never leak one school's data into
another's answer, because the table actually being read is itself scoped).
`modebills_sync.py` can be run once per school, all into the same database;
`load_csv.py` likewise, given a school's own `organization.csv` alongside
its other files.

Which school a conversation is scoped to is resolved once, server-side,
never something the model chooses or sees as a tool argument:
- The **MCP server** is scoped to one school per *process*, via
  `SCHOOLOS_ORGANIZATION_SLUG` (or `_ID`) in its environment.
- The **web chat** resolves it once per *conversation*, from `?school=<slug>`
  in the URL — but that's just a URL parameter today, not an authenticated
  login. Anyone who knows (or guesses) a school's slug can open its chat.
  Real per-school access control is the next real gap before this is safe
  to actually deploy — see the README roadmap.

`tests/test_modebills_sync.py`'s `test_cross_tenant_isolation` is the
isolation proof: two schools built with deliberately identical class,
subject, and student names, synced into one database, then checked that
every query scoped to one never surfaces the other's data (including a
direct attempt to fetch one school's student by id while scoped to the
other, which correctly raises `NotFoundError` rather than 404-ing
politely-but-leakily). The local demo setup mirrors this live: two demo
schools (`greenfield-demo`, `unity-demo`) are generated and loaded by
default specifically so the isolation can be seen working in the actual
running app, not just in a unit test.

## A non-Modebills school's own CSV export

Still relevant for any school not on Modebills.

- **No canonical format.** Every school's spreadsheet is bespoke: different
  column names/order, a logo/title taking up the first few rows, one sheet
  per class rather than one flat file.
- **Wide, not long.** A real result sheet is almost always one row per
  student with one column per subject or exam component (CA1, CA2, Exam,
  Total, Grade, Position all as columns) — the opposite of this project's
  normalized "long" schema. Expect to need a wide-to-long transform per
  school, not to ask schools to produce our exact CSVs. Still open — needs
  an actual sample export to design against.
- **No stable student ID in practice — except the admission number.** Names
  repeat, get misspelled, have no uniqueness guarantee.
  `students.admission_number` exists for this reason — matches the same
  field Modebills itself tracks.
- **Non-numeric score cells.** "AB" (absent), "-", blank, "N/A" are common
  instead of a numeric score. `load_csv` skips and reports these instead of
  crashing or silently corrupting a column — but still open: a real ingest
  will want to treat "absent" specifically (e.g. excluded from the average,
  which is already how SchoolOS treats a null score from Modebills) rather
  than just skipping the row.
- **Attendance is often not kept digitally at all**, or only as a per-term
  total ("32/55 days"), not a day-by-day register. Modebills' own data is
  day-level, which is what SchoolOS's schema assumes; a CSV-only school
  might need a second accepted input shape for period totals. Still open.
- **Multiple arms/streams** ("Primary 5A", "Primary 5B") are just distinct
  `classes.name` values — no schema change needed.
- **Privacy.** Real student data is sensitive (Nigeria's NDPR applies).
  Keep it local-only, get a written data-handling understanding with the
  school before taking any real export or running a real sync, and
  consider whether a demo audience should see anonymized data instead of
  real names.

## Still open

- A per-school CSV adapter (raw wide export → SchoolOS's normalized CSVs) —
  needs a real sample export to design against.
- A second attendance input shape for period/term-level totals, for a
  CSV-only school without day-level records.
- Live verification of `modebills_sync.py` against a real deployed
  organization.
- A tighter, per-school API key for the Modebills integration, if the
  shared-secret-for-everyone pattern turns out not to be acceptable once
  real schools' data is behind it.
