"""SchoolOS MCP server: exposes the analytics layer as MCP tools over stdio,
so it plugs straight into Claude Desktop / Claude Code (and any other MCP
client) without a separate backend.

Run directly, or point an MCP client config at:
    python -m schoolos.mcp_server.server
"""

from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

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


def _query(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a query function and turn an unmatched class/subject/term name into a
    clean tool error the model can read and recover from (e.g. by calling
    list_classes), instead of an opaque "Error executing tool" crash."""
    try:
        with connect() as conn:
            return fn(conn, *args, **kwargs)
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
def list_terms() -> list[dict]:
    """List every academic term on record, in chronological order."""
    return _query(q.list_terms)


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
    return _query(q.underperforming_students, class_name, subject, term, threshold_percent)


@mcp.tool()
def compare_assessment_performance(class_name: str, subject: str, last_n: int = 3) -> list[dict]:
    """Compare a class's performance on its most recent `last_n` assessments for one
    subject (e.g. "the last three assessments"), in chronological order.
    """
    return _query(q.compare_assessment_performance, class_name, subject, last_n)


@mcp.tool()
def compare_term_performance(class_name: str, subject: str) -> list[dict]:
    """Compare a class's average percent score across terms for one subject, in term order."""
    return _query(q.compare_term_performance, class_name, subject)


@mcp.tool()
def rank_teacher_improvement(top_n: int | None = None) -> list[dict]:
    """Rank teachers by how much their classes' average scores improved from the first
    term on record to the most recent one, across every class and subject they teach,
    most-improved first. Pass top_n (e.g. 3) to return only the top N instead of everyone.
    """
    return _query(q.rank_teacher_improvement, top_n)


@mcp.tool()
def get_attendance_summary(
    class_name: str | None = None,
    term: str | None = None,
    chronic_threshold_percent: float = 85.0,
) -> dict:
    """Get the overall attendance (present) rate and the students at or below a
    chronic-absence threshold. Optionally scope to one class and/or term.
    """
    return _query(q.attendance_summary, class_name, term, chronic_threshold_percent)


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
def get_term_report_data(class_name: str, term: str) -> dict:
    """Get the aggregated facts (class average, per-subject breakdown, top/bottom
    students, attendance) needed to draft an end-of-term report. Returns data only;
    write the actual report narrative yourself from these facts.
    """
    return _query(q.term_report_data, class_name, term)


@mcp.tool()
def find_student(name: str) -> list[dict]:
    """Find students by a partial, case-insensitive name match. Returns every match
    so an ambiguous name can be disambiguated by student_id before calling
    get_student_profile.
    """
    return _query(q.find_student, name)


@mcp.tool()
def get_student_profile(student_id: int) -> dict:
    """Get one student's full profile: overall and per-subject average scores,
    attendance rate, and class. Look up the student_id with find_student first.
    """
    return _query(q.get_student_profile, student_id)


@mcp.tool()
def compare_classes_on_subject(subject: str, term: str | None = None) -> list[dict]:
    """Compare every class's average percent score on one subject, strongest first
    (e.g. "which class is best at Mathematics school-wide"). Optionally scope to
    one term; otherwise averages across all terms on record.
    """
    return _query(q.compare_classes_on_subject, subject, term)


if __name__ == "__main__":
    mcp.run(transport="stdio")
