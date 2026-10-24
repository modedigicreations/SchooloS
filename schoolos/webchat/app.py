import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from schoolos.analytics import queries as q
from schoolos.db.connection import connect
from schoolos.webchat.agent import ask

app = FastAPI(title="SchoolOS")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class HistoryItem(BaseModel):
    role: str
    text: str


class ChatRequest(BaseModel):
    message: str
    school: str  # the school's slug, e.g. "the-word-of-promise-high-school"
    history: list[HistoryItem] = []


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/school")
def school_info(slug: str):
    """Resolve a school slug to its display name, so the chat UI can confirm
    which school it's talking to before any question is asked."""
    try:
        with connect() as conn:
            organization_id = q.resolve_organization_id(conn, slug)
            return q.get_organization(conn, organization_id)
    except q.NotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse(
            status_code=500,
            content={"error": "ANTHROPIC_API_KEY is not set on the server."},
        )
    try:
        with connect() as conn:
            organization_id = q.resolve_organization_id(conn, req.school)
    except q.NotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    result = ask([h.model_dump() for h in req.history], req.message, organization_id)
    return result
