"""SchoolOS MCP server: exposes the analytics layer as MCP tools over stdio,
so it plugs straight into Claude Desktop / Claude Code (and any other MCP
client) without a separate backend.

Run directly, or point an MCP client config at:
    python -m schoolos.mcp_server.server
"""

from mcp.server.mcpserver import MCPServer

from schoolos.analytics import queries as q
from schoolos.db.connection import connect

mcp = MCPServer(
    "schoolos",
    instructions=(
        "Tools for querying a school's own data (students, assessments, attendance, "
        "teachers). Every number comes from a direct query over ingested data — never "
        "guess or estimate a figure that one of these tools can answer."
    ),
)


@mcp.tool()
def list_classes() -> list[dict]:
    """List every class in the school (e.g. Primary 1 .. Primary 6)."""
    with connect() as conn:
        return q.list_classes(conn)


@mcp.tool()
def list_subjects() -> list[dict]:
    """List every subject taught in the school."""
    with connect() as conn:
        return q.list_subjects(conn)


@mcp.tool()
def list_terms() -> list[dict]:
    """List every academic term on record, in chronological order."""
    with connect() as conn:
        return q.list_terms(conn)


@mcp.tool()
def get_underperforming_students(
    class_name: str | None = None,
    subject: str | None = None,
    term: str | None = None,
    threshold_percent: float = 50.0,
) -> list[dict]:
    """Find students whose average percent score is at or below threshold_percent.

    Optionally scope to one class, one subject, and/or one term.
    """
    with connect() as conn:
        return q.underperforming_students(conn, class_name, subject, term, threshold_percent)


@mcp.tool()
def compare_assessment_performance(class_name: str, subject: str, last_n: int = 3) -> list[dict]:
    """Compare a class's performance on its most recent `last_n` assessments for one
    subject (e.g. "the last three assessments"), in chronological order.
    """
    with connect() as conn:
        return q.compare_assessment_performance(conn, class_name, subject, last_n)


@mcp.tool()
def compare_term_performance(class_name: str, subject: str) -> list[dict]:
    """Compare a class's average percent score across terms for one subject, in term order."""
    with connect() as conn:
        return q.compare_term_performance(conn, class_name, subject)


@mcp.tool()
def rank_teacher_improvement() -> list[dict]:
    """Rank teachers by how much their classes' average scores improved from the first
    term on record to the most recent one, across every class and subject they teach.
    """
    with connect() as conn:
        return q.rank_teacher_improvement(conn)


@mcp.tool()
def get_attendance_summary(
    class_name: str | None = None,
    term: str | None = None,
    chronic_threshold_percent: float = 85.0,
) -> dict:
    """Get the overall attendance (present) rate and the students at or below a
    chronic-absence threshold. Optionally scope to one class and/or term.
    """
    with connect() as conn:
        return q.attendance_summary(conn, class_name, term, chronic_threshold_percent)


@mcp.tool()
def get_intervention_candidates(
    class_name: str | None = None,
    subject: str | None = None,
    bottom_percent: float = 10.0,
) -> list[dict]:
    """Get the bottom `bottom_percent` of students by overall average score, with their
    per-subject breakdown and attendance rate — the factual basis for an intervention
    plan. Returns data only; write the actual plan narrative yourself from these facts.
    """
    with connect() as conn:
        return q.intervention_candidates(conn, class_name, subject, bottom_percent)


@mcp.tool()
def get_term_report_data(class_name: str, term: str) -> dict:
    """Get the aggregated facts (class average, per-subject breakdown, top/bottom
    students, attendance) needed to draft an end-of-term report. Returns data only;
    write the actual report narrative yourself from these facts.
    """
    with connect() as conn:
        return q.term_report_data(conn, class_name, term)


if __name__ == "__main__":
    mcp.run(transport="stdio")
