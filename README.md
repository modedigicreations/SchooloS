# SchoolOS

An AI operating/integration layer for schools. Not another AI tutor: a
principal or administrator asks a natural-language question and gets an
answer computed directly from the school's own data (students, assessment
results, attendance, teachers) — never a guess.

> **Status: MVP v0.** Multi-tenant — one shared database can serve every
> registered school, each strictly isolated from the others. Runs against
> two synthetic demo schools locally. See
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

**Multi-tenant by design:** every table carries `organization_id`, and every
query function takes it as an explicit argument and filters by it directly —
not just via a join further up the chain, so a forgotten join elsewhere can
never leak one school's data into another's answer. Which school a
conversation is about is resolved once per session — from the MCP server's
own process config, or, in the web chat, from a Modebills SSO token (see
below) — and is never something the model chooses or sees as a tool
argument. See [`tests/test_modebills_sync.py`](tests/test_modebills_sync.py)'s
`test_cross_tenant_isolation` for the isolation proof (two schools, deliberately
identical class/subject names, synced into one database).

**Access is by real login, not a guessable URL.** The web chat's actual
access path is an SSO token signed by Modebills' `/api/enterprise/schoolos/sso`
— a staff member (owner/admin/teacher) clicks "SchoolOS Analytics" from
their own Modebills admin portal while already logged in, and lands in the
chat already scoped to their school and signed in as themselves. See
[`schoolos/webchat/sso.py`](schoolos/webchat/sso.py) for the token
verification and [`tests/test_sso.py`](tests/test_sso.py) for the proof
(including a Python reimplementation of the exact signing algorithm from
Modebills' `src/server/jwt.helpers.ts`, so the test is real cross-language
interop, not just self-agreement). `?school=<slug>` still works as a
local/demo convenience — but only when `SCHOOLOS_SSO_SECRET` isn't
configured; once it is, that path is refused outright.

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

Generate the synthetic demo datasets (two schools, so the demo also proves
isolation) and load them both into the same shared SQLite database:

```bash
python -m schoolos.data.generate_demo_data             # writes data/demo/<slug>/*.csv for each demo school
python -m schoolos.ingest.load_csv data/demo/greenfield-demo
python -m schoolos.ingest.load_csv data/demo/unity-demo
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
export SCHOOLOS_INTER_SERVICE_SECRET=...  # SchoolOS's own secret — independent of Modebills' ModeCBT integration
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

The MCP server is scoped to exactly one school per process, set via
`SCHOOLOS_ORGANIZATION_SLUG` (or `_ID`) in its environment — the database
underneath can hold many schools, but one running server only ever answers
about the one it's configured for.

**Claude Code:** a project-scoped [`.mcp.json`](.mcp.json) is already checked in,
pointing at this repo's `.venv` with `SCHOOLOS_ORGANIZATION_SLUG` set to the
`greenfield-demo` demo school. Open a new Claude Code session in this
project (or restart your current one) and approve the `schoolos` MCP server
when prompted — no API key needed, it uses your existing Claude session.

**Claude Desktop** (the standalone chat app) or any other MCP client: add the
same server to its config manually, e.g. in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "schoolos": {
      "command": "/absolute/path/to/SchoolOS/.venv/bin/python",
      "args": ["-m", "schoolos.mcp_server.server"],
      "env": { "SCHOOLOS_ORGANIZATION_SLUG": "greenfield-demo" }
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

Without `SCHOOLOS_SSO_SECRET` set (local/demo), open
`http://127.0.0.1:8000/?school=greenfield-demo` (or `unity-demo`) — a required
dev-only slug; without it the page refuses to let you ask anything. Try the
same question against both demo schools to see the isolation directly:
identical class names, genuinely different answers.

With `SCHOOLOS_SSO_SECRET` set (matching Modebills' own value — see
`.env.example` there), that slug path is refused and the only way in is a
token from Modebills' "SchoolOS Analytics" link in its admin portal —
`?token=<jwt>`, scoped to whichever school and staff member actually clicked
it.

Each answer has a collapsible "Data used" section showing exactly which tool
calls backed it — useful for a demo audience that wants to see the
grounding, not just trust it.

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
- MCP server access is still per-process config (`SCHOOLOS_ORGANIZATION_SLUG`),
  not SSO — fine for a developer's own Claude Code/Desktop, not yet something
  a school's own staff would set up themselves.
- The SSO token is long-lived (8h) with no revocation — fine for a first
  pass, but there's no way to kill a session early (e.g. a staff member
  leaving) short of rotating `SCHOOLOS_SSO_SECRET`, which invalidates every
  session at once.
- Persistent hosting (Render/Fly.io/Railway) with a real Postgres database
  instead of a local SQLite file, and a scheduled sync job — today both the
  sync and the servers are run manually/locally
- Scheduled/automatic Modebills sync across every registered school (today
  it's a manual, one-school-at-a-time command)
