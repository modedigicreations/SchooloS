import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from schoolos.webchat.agent import ask

app = FastAPI(title="SchoolOS")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class HistoryItem(BaseModel):
    role: str
    text: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryItem] = []


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse(
            status_code=500,
            content={"error": "ANTHROPIC_API_KEY is not set on the server."},
        )
    result = ask([h.model_dump() for h in req.history], req.message)
    return result
