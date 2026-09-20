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

from fastapi import Body, FastAPI
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


class SessionRequest(BaseModel):
    mode: str = "guest"


class ChatRequest(BaseModel):
    message: str
    session_id: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _UI.read_text(encoding="utf-8")


@app.post("/session")
def create_session(req: SessionRequest = Body(default=SessionRequest())) -> dict:
    """Create a new server-generated session.  Clients must call this before /chat."""
    session_id = secrets.token_urlsafe(16)
    if req.mode == "sarah_demo":
        ctx = CustomerContext(session_id=session_id, authenticated=True, customer_id="CUST-004")
    else:
        ctx = CustomerContext(session_id=session_id, authenticated=False)
    _contexts[session_id] = ctx
    _sessions[session_id] = SupportAgent(ctx=ctx)
    return {"session_id": session_id, "mode": req.mode}


@app.post("/chat", response_model=None)
def chat(req: ChatRequest):
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

    # Check for a newly created pending refund for this session
    pending_refund_info = None
    for token, data in PENDING_REFUNDS.items():
        if data["session_id"] == req.session_id and not data.get("consumed"):
            pending_refund_info = {
                "amount": data["amount"],
                "payment_destination": data["payment_destination"],
                "order_id": data["order_id"],
            }
            break

    return {
        "reply": reply,
        "tool_calls": agent.last_tool_calls,
        "session_id": req.session_id,
        "pending_refund": pending_refund_info,
    }


@app.post("/confirm/{session_id}", response_model=None)
def confirm_refund(session_id: str):
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

    # Build a customer-friendly response
    if result.get("success") or result.get("status") == "already_initiated":
        amount = result.get("amount", pending_data.get("amount"))
        refund_id = result.get("refund_id", "")
        return {
            "success": True,
            "message": (
                f"Your refund of ${amount:.2f} has been submitted. "
                f"You'll receive confirmation within 5–7 business days."
            ),
            "refund_id": refund_id,
            "amount": amount,
        }

    return JSONResponse(status_code=400, content=result)


@app.delete("/session/{session_id}")
def reset_session(session_id: str) -> dict:
    agent = _sessions.pop(session_id, None)
    _contexts.pop(session_id, None)
    if agent:
        agent.reset()
    return {"ok": True}
