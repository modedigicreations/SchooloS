import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from schoolos.analytics import queries as q
from schoolos.db.connection import connect
from schoolos.webchat.agent import ask
from schoolos.webchat.sso import InvalidTokenError, sso_required, verify_sso_token

app = FastAPI(title="SchoolOS")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class SessionError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


def resolve_session(conn: sqlite3.Connection, token: str | None, school: str | None) -> dict:
    """Resolve which school (and, via SSO, which staff member) a request is
    for. The token is the real access path — issued by Modebills'
    /api/enterprise/schoolos/sso only to an already-authenticated staff
    member. ?school=<slug> is a local/demo fallback only: once
    SCHOOLOS_SSO_SECRET is configured on this deployment, it's refused
    outright, closing the "anyone who knows the slug" gap that prompted
    building this at all.
    """
    if token:
        try:
            claims = verify_sso_token(token)
        except InvalidTokenError as exc:
            raise SessionError(401, f"invalid session: {exc}") from exc
        return {
            "organization_id": claims["organizationId"],
            "organization_name": claims.get("organizationName"),
            "user_name": claims.get("userName"),
            "role": claims.get("role"),
        }

    if sso_required():
        raise SessionError(401, "sign-in required — open this page from your Modebills staff portal")

    if not school:
        raise SessionError(400, "missing ?school= or ?token=")

    try:
        organization_id = q.resolve_organization_id(conn, school)
        org = q.get_organization(conn, organization_id)
    except q.NotFoundError as exc:
        raise SessionError(404, str(exc)) from exc
    return {"organization_id": organization_id, "organization_name": org["name"], "user_name": None, "role": None}


class HistoryItem(BaseModel):
    role: str
    text: str


class ChatRequest(BaseModel):
    message: str
    school: str | None = None  # dev/demo fallback only — see resolve_session
    token: str | None = None  # the real access path, from Modebills SSO
    history: list[HistoryItem] = []


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/session")
def session_info(token: str | None = None, school: str | None = None):
    """Resolve a token or (dev-only) school slug to who's asking and which
    school it's about, so the chat UI can confirm before any question is
    asked."""
    try:
        with connect() as conn:
            return resolve_session(conn, token, school)
    except SessionError as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse(
            status_code=500,
            content={"error": "ANTHROPIC_API_KEY is not set on the server."},
        )
    try:
        with connect() as conn:
            info = resolve_session(conn, req.token, req.school)
    except SessionError as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    result = ask([h.model_dump() for h in req.history], req.message, info["organization_id"])
    return result
