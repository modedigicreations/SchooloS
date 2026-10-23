# SchoolOS

An AI operating/integration layer for schools. Not another AI tutor: a
principal or administrator asks a natural-language question and gets an
answer computed directly from the school's own data (students, assessment
results, attendance, teachers) — never a guess.

> **Status: MVP v0.** Runs against a synthetic demo school. See
> [Roadmap](#roadmap--whats-deliberately-out-of-scope) for what's intentionally not built yet.

## How it works

```
CSV exports  ─┐
               ├─▶  normalized SQLite DB  ─▶  analytics queries (ground truth)  ─▶  tools
Modebills API ─┘                                                                       │
                                                        ┌──────────────────────────────┼──────────────────────────────┐
                                                        │                                                              │
                                            MCP server (stdio)                                               web chat (FastAPI)
                                   plugs into Claude Desktop / Claude Code                              Anthropic tool-use loop + a
                                        or any other MCP client                                          single-page chat UI
```

Two ways data gets into the local database: CSV import (for a school with no
other system), or syncing from [Modebills](https://app.modeschools.com) (MODE
Digital's own school platform), which schools already use for exams and
attendance — no manual export needed. Both paths load into the exact same
schema, so the analytics layer and both interfaces don't care which one fed
them.

Every number the assistant states comes from a direct SQL query in
[`schoolos/analytics/queries.py`](schoolos/analytics/queries.py) — the LLM's job is
to decide which tool to call and to write the narrative (e.g. a report or an
intervention plan) *from* the returned facts, never to invent a figure itself.

## Project layout

```
schoolos/
├── data/generate_demo_data.py   synthetic demo school (CSV generator)
├── db/schema.sql, connection.py SQLite schema + connection helper
├── ingest/load_csv.py           CSV → SQLite loader
├── ingest/modebills_sync.py     Modebills API → SQLite sync
├── analytics/queries.py         ground-truth query functions
├── analytics/tools.py           tool definitions (JSON schema + dispatch) shared by both interfaces
├── mcp_server/server.py         MCP server (stdio) — for Claude Desktop/Code
└── webchat/                     FastAPI app + chat UI, using the Anthropic API directly
data/demo/                       generated synthetic CSVs (tracked in git for easy demoing)
tests/test_modebills_sync.py     sync transform/load tests (mocked Modebills responses)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Generate the synthetic demo dataset and load it into SQLite:

```bash
python -m schoolos.data.generate_demo_data   # writes data/demo/*.csv
python -m schoolos.ingest.load_csv data/demo  # loads into data/schoolos.db
```

To load a non-Modebills school's own exports instead, point `load_csv` at a
directory with the same CSV shape (see the files under `data/demo/` for the
exact columns each table expects). The loader validates and coerces every
row rather than aborting the whole import on the first bad cell — it loads
what it can and returns a report of which rows it skipped and why (bad
number, missing reference, etc.), so a human can go fix the source file.

A school's own export will very likely NOT already be in this normalized
shape — most schools keep one row per student with one column per
subject/exam ("wide" format), not our normalized "long" tables. Turning a
specific school's actual export into these CSVs (a per-school adapter) is
still an open item — see [docs/real-data-ingest-notes.md](docs/real-data-ingest-notes.md).

### Syncing from Modebills instead

For a school already on [Modebills](https://app.modeschools.com), there's
nothing to export — `modebills_sync` pulls directly from its
`/api/enterprise/schoolos/*` endpoints:

```bash
export MODECBT_INTER_SERVICE_SECRET=...   # shared secret, matches Modebills' own env
export MODEBILLS_ORGANIZATION_SLUG=...    # e.g. the-word-of-promise-high-school
# (or MODEBILLS_ORGANIZATION_ID=... if you have the raw uuid instead)
python -m schoolos.ingest.modebills_sync
```

Re-running it is a plain upsert (ids are the platform's own UUIDs, used
directly — no id-translation layer), so it's safe to run on a schedule or
on demand. Two real gaps to know about, since they shape what SchoolOS can
honestly answer from this source (see
[docs/real-data-ingest-notes.md](docs/real-data-ingest-notes.md) for the
full detail): academic data only covers exams actually run through
Modebills' CBT module, and a "teacher" in this data means a class's single
class teacher — there's no per-subject teacher assignment.

## Running it

### Option A — MCP server (Claude Desktop / Claude Code)

**Claude Code:** a project-scoped [`.mcp.json`](.mcp.json) is already checked in,
pointing at this repo's `.venv`. Open a new Claude Code session in this
project (or restart your current one) and approve the `schoolos` MCP server
when prompted — no API key needed, it uses your existing Claude session.

**Claude Desktop** (the standalone chat app) or any other MCP client: add the
same server to its config manually, e.g. in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "schoolos": {
      "command": "/absolute/path/to/SchoolOS/.venv/bin/python",
      "args": ["-m", "schoolos.mcp_server.server"]
    }
  }
}
```

Then ask Claude things like *"Which Primary 5 students are below expectations in
Mathematics?"* or *"Which teachers have the largest improvement in student
outcomes?"* — it will call the tools directly.

### Option B — standalone web chat

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn schoolos.webchat.app:app --reload
```

Open http://127.0.0.1:8000 and ask questions in the browser. Each answer has a
collapsible "Data used" section showing exactly which tool calls backed it —
useful for a demo audience that wants to see the grounding, not just trust it.

## Example questions

- "Which students are performing below expectations?"
- "Compare Primary 5 mathematics performance across the last three assessments"
- "Which teachers have the largest improvement in student outcomes?"
- "Draft an end-of-term report for Primary 6"
- "Generate an intervention plan for the bottom 10% of Primary 3"
- "Tell me about Kelechi Chukwu's overall performance"
- "Which class is strongest in Mathematics school-wide?"

## Roadmap / what's deliberately out of scope (for now)

Kept out of this pass on purpose, to keep the MVP's scope controlled:

- **Finance module** (fees, budgets, financial literacy tie-ins)
- **Behaviour records module**
- **MODE Quest integration** (gamified learning, longer-term "education intelligence platform" vision)
- A per-school CSV adapter for non-Modebills schools with their own "wide"-format exports
- Multi-school / multi-tenant support (SchoolOS is still single-tenant: one local
  database synced from exactly one Modebills organization, or one CSV source),
  auth & roles, production deployment
- Scheduled/automatic Modebills sync (today it's a manual or cron-it-yourself command)
