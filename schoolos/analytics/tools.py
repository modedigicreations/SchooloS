"""Tool definitions shared by the MCP server and the web chat backend.

Each entry pairs a JSON Schema (for the LLM / MCP client) with the analytics
function it calls. Keeping this in one place means the MCP server and the
direct-Anthropic-API web chat expose exactly the same capabilities.
"""

from dataclasses import dataclass
from typing import Any, Callable

import sqlite3

from schoolos.analytics import queries as q


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]


def _no_args_schema() -> dict:
    return {"type": "object", "properties": {}}


TOOLS: list[Tool] = [
    Tool(
        name="list_classes",
        description="List every class in the school (e.g. Primary 1 .. Primary 6).",
        input_schema=_no_args_schema(),
        handler=lambda conn, **_: q.list_classes(conn),
    ),
    Tool(
        name="list_subjects",
        description="List every subject taught in the school.",
        input_schema=_no_args_schema(),
        handler=lambda conn, **_: q.list_subjects(conn),
    ),
    Tool(
        name="list_terms",
        description="List every academic term on record, in chronological order.",
        input_schema=_no_args_schema(),
        handler=lambda conn, **_: q.list_terms(conn),
    ),
    Tool(
        name="get_underperforming_students",
        description=(
            "Find students whose average percent score is at or below a threshold. "
            "Optionally scope to one class, one subject, and/or one term."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "class_name": {"type": "string", "description": "e.g. 'Primary 5'. Omit for all classes."},
                "subject": {"type": "string", "description": "e.g. 'Mathematics'. Omit for all subjects."},
                "term": {"type": "string", "description": "e.g. '2025/2026 Term 2'. Omit for all terms."},
                "threshold_percent": {"type": "number", "description": "Default 50.0."},
            },
        },
        handler=lambda conn, **kw: q.underperforming_students(conn, **kw),
    ),
    Tool(
        name="compare_assessment_performance",
        description=(
            "Compare a class's performance on its most recent assessments for one subject "
            "(e.g. 'the last three assessments'), in chronological order."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "class_name": {"type": "string"},
                "subject": {"type": "string"},
                "last_n": {"type": "integer", "description": "How many recent assessments to compare. Default 3."},
            },
            "required": ["class_name", "subject"],
        },
        handler=lambda conn, **kw: q.compare_assessment_performance(conn, **kw),
    ),
    Tool(
        name="compare_term_performance",
        description="Compare a class's average percent score across terms for one subject, in term order.",
        input_schema={
            "type": "object",
            "properties": {
                "class_name": {"type": "string"},
                "subject": {"type": "string"},
            },
            "required": ["class_name", "subject"],
        },
        handler=lambda conn, **kw: q.compare_term_performance(conn, **kw),
    ),
    Tool(
        name="rank_teacher_improvement",
        description=(
            "Rank teachers by how much their classes' average scores improved from the first "
            "term on record to the most recent one, across every class and subject they teach, "
            "most-improved first. Pass top_n (e.g. 3) to return only the top N instead of everyone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "Return only the top N most-improved teachers. Omit for all."},
            },
        },
        handler=lambda conn, **kw: q.rank_teacher_improvement(conn, **kw),
    ),
    Tool(
        name="get_attendance_summary",
        description=(
            "Get the overall attendance (present) rate and the list of students at or below a "
            "chronic-absence threshold. Optionally scope to one class and/or term."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "class_name": {"type": "string"},
                "term": {"type": "string"},
                "chronic_threshold_percent": {"type": "number", "description": "Default 85.0."},
            },
        },
        handler=lambda conn, **kw: q.attendance_summary(conn, **kw),
    ),
    Tool(
        name="get_intervention_candidates",
        description=(
            "Get the bottom N percent of students by overall average score, with their per-subject "
            "breakdown and attendance rate, as the factual basis for drafting intervention plans. "
            "This tool returns data only — write the actual intervention plan narrative yourself "
            "from the returned facts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "class_name": {"type": "string"},
                "subject": {"type": "string"},
                "bottom_percent": {"type": "number", "description": "Default 10.0."},
            },
        },
        handler=lambda conn, **kw: q.intervention_candidates(conn, **kw),
    ),
    Tool(
        name="get_term_report_data",
        description=(
            "Get the aggregated facts (class average, per-subject breakdown, top/bottom students, "
            "attendance) needed to draft an end-of-term report for one class and term. "
            "This tool returns data only — write the actual report narrative yourself from the "
            "returned facts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "class_name": {"type": "string"},
                "term": {"type": "string"},
            },
            "required": ["class_name", "term"],
        },
        handler=lambda conn, **kw: q.term_report_data(conn, **kw),
    ),
    Tool(
        name="find_student",
        description=(
            "Find students by a partial, case-insensitive name match. Returns every match "
            "so an ambiguous name can be disambiguated by student_id before calling "
            "get_student_profile."
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=lambda conn, **kw: q.find_student(conn, **kw),
    ),
    Tool(
        name="get_student_profile",
        description=(
            "Get one student's full profile: overall and per-subject average scores, "
            "attendance rate, and class. Look up the student_id with find_student first."
        ),
        input_schema={
            "type": "object",
            "properties": {"student_id": {"type": "integer"}},
            "required": ["student_id"],
        },
        handler=lambda conn, **kw: q.get_student_profile(conn, **kw),
    ),
    Tool(
        name="compare_classes_on_subject",
        description=(
            "Compare every class's average percent score on one subject, strongest first "
            "(e.g. 'which class is best at Mathematics school-wide'). Optionally scope to "
            "one term; otherwise averages across all terms on record."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "term": {"type": "string"},
            },
            "required": ["subject"],
        },
        handler=lambda conn, **kw: q.compare_classes_on_subject(conn, **kw),
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def call_tool(conn: sqlite3.Connection, name: str, arguments: dict) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    return tool.handler(conn, **(arguments or {}))
