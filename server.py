"""
FastAPI server — wraps SupportAgent behind a /chat endpoint
and serves the browser UI from static/index.html.

Run:  uvicorn server:app --reload
      then open http://127.0.0.1:8000
"""
import pathlib
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent import SupportAgent

app = FastAPI(title="Bookly AI Concierge")

_sessions: dict[str, SupportAgent] = {}
_UI = pathlib.Path(__file__).parent / "static" / "index.html"


class ChatRequest(BaseModel):
    message: str
    session_id: str


@app.get("/", response_class=HTMLResponse)
def index():
    return _UI.read_text(encoding="utf-8")


@app.post("/chat")
def chat(req: ChatRequest):
    agent = _sessions.setdefault(req.session_id, SupportAgent())
    reply = agent.reply(req.message)
    return {
        "reply": reply,
        "tool_calls": agent.last_tool_calls,
        "session_id": req.session_id,
    }


@app.delete("/session/{session_id}")
def reset_session(session_id: str):
    agent = _sessions.pop(session_id, None)
    if agent:
        agent.reset()
    return {"ok": True}
