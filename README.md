# SchoolOS

An AI operating/integration layer for schools. Not another AI tutor: a
principal or administrator asks a natural-language question and gets an
answer computed directly from the school's own data (students, assessment
results, attendance, teachers) — never a guess.

> **Status: MVP v0.** Runs against a synthetic demo school. See
> [Roadmap](#roadmap--whats-deliberately-out-of-scope) for what's intentionally not built yet.

## How it works

```
CSV exports  →  normalized SQLite DB  →  analytics queries (ground truth)  →  tools
                                                                                 │
                                                        ┌────────────────────────┼────────────────────────┐
                                                        │                                                  │
                                            MCP server (stdio)                                   web chat (FastAPI)
                                   plugs into Claude Desktop / Claude Code                  Anthropic tool-use loop + a
                                        or any other MCP client                              single-page chat UI
```

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
├── analytics/queries.py         ground-truth query functions
├── analytics/tools.py           tool definitions (JSON schema + dispatch) shared by both interfaces
├── mcp_server/server.py         MCP server (stdio) — for Claude Desktop/Code
└── webchat/                     FastAPI app + chat UI, using the Anthropic API directly
data/demo/                       generated synthetic CSVs (tracked in git for easy demoing)
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

To load a real school's own exports instead, point `load_csv` at a directory
with the same CSV shape (see the files under `data/demo/` for the exact
columns each table expects).

## Running it

### Option A — MCP server (Claude Desktop / Claude Code)

Add to your MCP client config (e.g. Claude Desktop's `claude_desktop_config.json`):

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

## Roadmap / what's deliberately out of scope (for now)

Kept out of this pass on purpose, to keep the MVP's scope controlled:

- **Finance module** (fees, budgets, financial literacy tie-ins)
- **Behaviour records module**
- **MODE Quest integration** (gamified learning, longer-term "education intelligence platform" vision)
- Real SIS/API integrations — the MVP assumes CSV/spreadsheet import, since that's
  the realistic day-one data source for most schools
- Multi-school / multi-tenant support, auth & roles, production deployment
