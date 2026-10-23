"""Tool-use agent loop: turns a user question into Claude tool calls against the
analytics layer, and a final answer grounded in what those tools returned.
"""

import json
import os

import anthropic

from schoolos.analytics.tools import TOOLS, call_tool
from schoolos.db.connection import connect

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """You are SchoolOS, an assistant for school administrators and principals.

You have tools that query the school's own student, assessment, attendance, and \
teacher data directly. For ANY factual claim about students, scores, attendance, or \
teachers, you MUST call the relevant tool and base your answer only on what it \
returns — never guess, estimate, or recall a number from general knowledge.

If a class or subject name in the question doesn't match what a tool \
returns, call list_classes / list_subjects to find the exact names \
before trying again. There is no "term" in this data — exams are standalone, \
ordered by date. When asked to draft a report or an intervention plan, first \
call the data tool for the relevant facts, then write the narrative yourself from \
those facts — don't invent figures that weren't returned.

Keep answers concise and concrete: name the students/classes/numbers involved \
rather than speaking in generalities."""

ANTHROPIC_TOOLS = [
    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
    for t in TOOLS
]


def ask(history: list[dict], message: str) -> dict:
    """Run one user turn through the tool-use loop.

    `history` is prior turns as [{"role": "user"|"assistant", "text": str}, ...].
    Returns {"reply": str, "tool_calls": [{"name", "input", "result"}, ...]}.
    """
    client = anthropic.Anthropic()
    messages = [{"role": h["role"], "content": h["text"]} for h in history]
    messages.append({"role": "user", "content": message})

    tool_calls_made = []

    with connect() as conn:
        for _ in range(MAX_TOOL_ROUNDS):
            response = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                tools=ANTHROPIC_TOOLS,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                final_text = "".join(b.text for b in response.content if b.type == "text")
                return {"reply": final_text, "tool_calls": tool_calls_made}

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    result = call_tool(conn, block.name, block.input)
                    result_text = json.dumps(result)
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - surface any failure to the model
                    result_text = f"error: {exc}"
                    result = None
                    is_error = True
                tool_calls_made.append({"name": block.name, "input": block.input, "result": result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

    return {
        "reply": "I made too many tool calls trying to answer that — please rephrase or narrow the question.",
        "tool_calls": tool_calls_made,
    }
