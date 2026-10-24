"""SchoolOS MCP server: exposes the analytics layer as MCP tools over stdio,
so it plugs straight into Claude Desktop / Claude Code (and any other MCP
client) without a separate backend.

This server process is scoped to exactly one school, set via
SCHOOLOS_ORGANIZATION_SLUG (or SCHOOLOS_ORGANIZATION_ID) in its environment
— never something the LLM passes as a tool argument. The underlying
database can hold many schools (see schoolos/db/schema.sql), but this
server only ever answers about the one it's configured for.

Run directly, or point an MCP client config at:
    python -m schoolos.mcp_server.server
"""

import os
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from schoolos.analytics import queries as q
from schoolos.db.connection import connect

mcp = MCPServer(
    "schoolos",
    instructions=(
        "Tools for querying a school's own data (students, exams, attendance, "
        "teachers). Every number comes from a direct query over ingested data — never "
        "guess or estimate a figure that one of these tools can answer. There is no "
        "'term' concept in this data — exams are standalone, ordered by date."
    ),
)


def _server_organization_id(conn) -> str:
    org_id = os.environ.get("SCHOOLOS_ORGANIZATION_ID")
    if org_id:
        return org_id
    slug = os.environ.get("SCHOOLOS_ORGANIZATION_SLUG")
    if not slug:
        raise ToolError(
            "SCHOOLOS_ORGANIZATION_SLUG (or SCHOOLOS_ORGANIZATION_ID) is not set — "
            "this server doesn't know which school to answer about"
        )
    return q.resolve_organization_id(conn, slug)


def _query(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a query function, scoped to this server's configured school, and
    turn an unmatched class/subject name into a clean tool error the model
    can read and recover from (e.g. by calling list_classes), instead of an
    opaque "Error executing tool" crash."""
    try:
        with connect() as conn:
            organization_id = _server_organization_id(conn)
            return fn(conn, organization_id, *args, **kwargs)
    except q.NotFoundError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def list_classes() -> list[dict]:
    """List every class in the school (e.g. Primary 1 .. Primary 6)."""
    return _query(q.list_classes)


@mcp.tool()
def list_subjects() -> list[dict]:
    """List every subject taught in the school."""
    return _query(q.list_subjects)


@mcp.tool()
def get_underperforming_students(
    class_name: str | None = None,
    subject: str | None = None,
    threshold_percent: float = 50.0,
) -> list[dict]:
    """Find students whose average percent score is at or below threshold_percent.

    Optionally scope to one class and/or one subject.
    """
    return _query(q.underperforming_students, class_name, subject, threshold_percent)


@mcp.tool()
def compare_assessment_performance(class_name: str, subject: str, last_n: int = 3) -> list[dict]:
    """Compare a class's performance on its most recent `last_n` exams for one
    subject (e.g. "the last three exams"), in chronological order.
    """
    return _query(q.compare_assessment_performance, class_name, subject, last_n)


@mcp.tool()
def rank_teacher_improvement(top_n: int | None = None) -> list[dict]:
    """Rank teachers by how much their class's average scores improved from its
    earliest graded exam on record to its most recent, most-improved first.
    Pass top_n (e.g. 3) to return only the top N instead of everyone. A "teacher"
    here is a class's single class teacher — there's no per-subject assignment.
    """
    return _query(q.rank_teacher_improvement, top_n)


@mcp.tool()
def get_attendance_summary(
    class_name: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    chronic_threshold_percent: float = 85.0,
) -> dict:
    """Get the overall attendance (present) rate and the students at or below a
    chronic-absence threshold. Optionally scope to one class and/or a date range
    (YYYY-MM-DD, inclusive).
    """
    return _query(q.attendance_summary, class_name, from_date, to_date, chronic_threshold_percent)


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
    return _query(q.intervention_candidates, class_name, subject, bottom_percent)


@mcp.tool()
def get_class_report_data(
    class_name: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Get the aggregated facts (class average, per-subject breakdown, top/bottom
    students, attendance) needed to draft a report for one class. Covers every
    graded exam on record by default; optionally scope to a date range
    (YYYY-MM-DD, inclusive). Returns data only; write the actual report narrative
    yourself from these facts.
    """
    return _query(q.get_class_report_data, class_name, from_date, to_date)


@mcp.tool()
def find_student(name: str) -> list[dict]:
    """Find students by a partial, case-insensitive name match. Returns every match
    so an ambiguous name can be disambiguated by student_id before calling
    get_student_profile.
    """
    return _query(q.find_student, name)


@mcp.tool()
def get_student_profile(student_id: str) -> dict:
    """Get one student's full profile: overall and per-subject average scores,
    attendance rate, and class. Look up the student_id with find_student first.
    """
    return _query(q.get_student_profile, student_id)


@mcp.tool()
def compare_classes_on_subject(subject: str) -> list[dict]:
    """Compare every class's average percent score on one subject, strongest first
    (e.g. "which class is best at Mathematics school-wide"), across all graded
    exams on record.
    """
    return _query(q.compare_classes_on_subject, subject)


if __name__ == "__main__":
    mcp.run(transport="stdio")
