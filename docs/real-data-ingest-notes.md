# Notes: going from synthetic data to a real school's exports

Scoping notes for the next real step after the MVP (see [README](../README.md)
roadmap). Not yet implemented — captured here so the next session doesn't
have to re-derive it.

## What's likely to actually be true of a real school's data

- **No canonical format.** Every school's spreadsheet is bespoke: different
  column names/order, a logo/title taking up the first few rows, one sheet
  per class or per term rather than one flat file.
- **Wide, not long.** A real result sheet is almost always one row per
  student with one column per subject or assessment component (CA1, CA2,
  Exam, Total, Grade, Position all as columns) — the opposite of this
  project's normalized "long" schema (`results` = one row per
  student × assessment). Expect to need a wide-to-long transform per school,
  not to ask schools to produce our exact CSVs.
- **No stable student ID in practice — except the admission number.** Names
  repeat, get misspelled across terms, and have no uniqueness guarantee.
  `students.admission_number` (added in this pass) exists for this reason:
  a real ingest should match/update students by admission number, not name.
- **Non-numeric score cells.** "AB" (absent), "-", blank, "N/A" are common
  instead of a numeric score. The hardened `load_csv` now skips and reports
  these instead of crashing or silently corrupting a column — but a real
  ingest will want to treat "absent" specifically (e.g. as a zero, or as
  excluded from the average) rather than just skipping it.
- **Attendance is often not kept digitally at all**, or only as a per-term
  total ("32/55 days"), not a day-by-day register. The current schema
  assumes day-level rows. A real ingest likely needs a second accepted input
  shape — `days_present` / `days_total` per student per term — that gets
  reconciled into (or replaces) the day-level table.
- **Multiple arms/streams** ("Primary 5A", "Primary 5B") are just distinct
  `classes.name` values already — no schema change needed, just a naming
  convention to agree with the school, and query tools should handle a
  natural-language "Primary 5" resolving to several arms (ask which, or
  aggregate across them) rather than erroring.
- **Privacy.** Real student data is sensitive (Nigeria's NDPR applies).
  Even for a pilot: keep it local-only, get a written data-handling
  understanding with the school before taking any real export, and consider
  whether a demo audience should see anonymized data instead of real names.

## What this pass already did about it

- Added `students.admission_number` as the durable identifier.
- Rewrote `schoolos/ingest/load_csv.py` to validate/coerce row-by-row and
  return a `LoadReport` (loaded/skipped counts + per-row reasons) instead of
  aborting the whole import on the first bad cell.

## What's still open (needs a real sample export to design against)

- A per-school "adapter" step: raw export → our normalized CSVs. This can't
  be built generically without seeing at least one real school's actual
  sheet — the wide-to-long mapping is inherently school-specific (which
  columns are which assessment, where the header row actually is, etc.).
- A second attendance input shape for term-level totals.
- A documented convention for "absent" vs "missing" in a score cell, and
  whether absent should count as 0, be excluded from the average, or be
  tracked separately from a true missing value.
