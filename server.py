"""
FastAPI server — wraps SupportAgent behind a /chat endpoint
and serves the browser UI from static/index.html.

Run:  uvicorn server:app --reload
      then open http://127.0.0.1:8000

Prototype note: sessions are stored in-memory and lost on restart.
Production deployments require a durable session store.
"""
import json
import pathlib
import secrets

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agent import SupportAgent
from config import settings
from context import CustomerContext
from data import PENDING_REFUNDS
from tools import initiate_refund

app = FastAPI(title="Bookly AI Concierge")

# In-memory session stores — prototype only
_sessions: dict[str, SupportAgent] = {}
_contexts: dict[str, CustomerContext] = {}

_UI = pathlib.Path(__file__).parent / "static" / "index.html"


class ChatRequest(BaseModel):
    message: str
    session_id: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _UI.read_text(encoding="utf-8")


@app.post("/session")
def create_session() -> dict:
    """Create a new server-generated session.  Clients must call this before /chat."""
    session_id = secrets.token_urlsafe(16)
    ctx = CustomerContext(session_id=session_id, authenticated=False)
    _contexts[session_id] = ctx
    _sessions[session_id] = SupportAgent(ctx=ctx)
    return {"session_id": session_id}


@app.post("/chat")
def chat(req: ChatRequest) -> JSONResponse | dict:
    # Validate message length
    if len(req.message) > settings.MAX_MESSAGE_LENGTH:
        return JSONResponse(
            status_code=400,
            content={"error": f"Message exceeds the {settings.MAX_MESSAGE_LENGTH} character limit."},
        )

    # Require a valid server-generated session
    if req.session_id not in _sessions:
        return JSONResponse(
            status_code=404,
            content={"error": "Session not found. Call POST /session first."},
        )

    agent = _sessions[req.session_id]

    # Validate conversation turn count (each turn = 2 history entries: user + assistant)
    if len(agent._client.history) >= settings.MAX_CONVERSATION_TURNS * 2:
        return JSONResponse(
            status_code=400,
            content={"error": "Conversation limit reached. Please start a new session."},
        )

    reply = agent.reply(req.message)
    return {
        "reply": reply,
        "tool_calls": agent.last_tool_calls,
        "session_id": req.session_id,
    }


@app.post("/confirm/{session_id}")
def confirm_refund(session_id: str) -> JSONResponse | dict:
    """Application-controlled confirmation endpoint.

    Looks up the pending refund for the session and calls initiate_refund with
    the stored token.  The token is never exposed to or passed by the LLM.
    """
    ctx = _contexts.get(session_id)
    if not ctx:
        return JSONResponse(status_code=404, content={"error": "Session not found."})

    # Find a non-consumed pending refund for this session
    token: str | None = None
    pending_data: dict | None = None
    for t, data in list(PENDING_REFUNDS.items()):
        if data["session_id"] == session_id and not data.get("consumed"):
            token = t
            pending_data = data
            break

    if not token or not pending_data:
        return JSONResponse(
            status_code=404,
            content={"error": "No pending refund found for this session."},
        )

    result = json.loads(initiate_refund(pending_data["order_id"], ctx, token))
    return result


@app.delete("/session/{session_id}")
def reset_session(session_id: str) -> dict:
    agent = _sessions.pop(session_id, None)
    _contexts.pop(session_id, None)
    if agent:
        agent.reset()
    return {"ok": True}
