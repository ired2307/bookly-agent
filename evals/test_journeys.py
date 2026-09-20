"""
Trajectory evaluations — two scripted conversations driven by mocked HTTP
responses.  No API key required.

Journey 1: Guest asks "where is my order" → agent asks for details → calls
           lookup_order → returns delivery status.  No write tools called.

Journey 2: Authenticated customer reports damaged delivery → agent calls
           prepare_refund → presents amount → customer confirms → application
           calls initiate_refund (not the model).  Idempotency verified.
"""
import json
import unittest.mock as mock

import pytest
import requests

from context import CustomerContext
from data import PENDING_REFUNDS, REFUNDS_INITIATED
from tools import initiate_refund


# ── Mock helper ───────────────────────────────────────────────────────────────


def _make_response(stop_reason: str, content: list, status_code: int = 200):
    m = mock.Mock()
    m.ok = status_code < 400
    m.status_code = status_code
    m.headers = {}
    m.json.return_value = {"stop_reason": stop_reason, "content": content}
    return m


# ── Journey 1: Guest order tracking ──────────────────────────────────────────


class TestJourney1GuestOrderTracking:
    """Guest customer tracks their order across two turns."""

    def test_agent_asks_before_tool_call(self):
        """Turn 1: vague request produces a clarification, no tool call."""
        from agent import SupportAgent

        ctx = CustomerContext(session_id="j1-session", authenticated=False)
        agent = SupportAgent(ctx=ctx)

        clarify_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "Could you share your order number and email?"}],
        )

        with mock.patch("requests.post", return_value=clarify_resp):
            reply = agent.reply("where is my order")

        assert len(agent.last_tool_calls) == 0
        assert "order" in reply.lower() or "email" in reply.lower()

    def test_full_tracking_journey(self):
        """Two-turn flow: clarify → provide details → lookup_order → status returned."""
        from agent import SupportAgent

        ctx = CustomerContext(session_id="j1-session", authenticated=False)
        agent = SupportAgent(ctx=ctx)

        clarify_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "Could you share your order number and email?"}],
        )
        lookup_resp = _make_response(
            "tool_use",
            [{
                "type": "tool_use",
                "id": "t1",
                "name": "lookup_order",
                "input": {"order_id": "BK-10042", "customer_email": "jane.doe@email.com"},
            }],
        )
        status_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "Your order BK-10042 is shipped and due September 22."}],
        )

        with mock.patch("requests.post") as mp:
            # Turn 1 — vague request
            mp.return_value = clarify_resp
            reply1 = agent.reply("where is my order")
            turn1_calls = list(agent.last_tool_calls)

            # Turn 2 — customer provides details
            mp.side_effect = [lookup_resp, status_resp]
            reply2 = agent.reply("BK-10042, jane.doe@email.com")
            turn2_calls = list(agent.last_tool_calls)

        # Turn 1: no tools
        assert len(turn1_calls) == 0

        # Turn 2: lookup_order was called
        assert len(turn2_calls) > 0
        assert any("looking up" in c.lower() or "lookup" in c.lower() for c in turn2_calls)

        # No write tools across both turns
        all_calls = turn1_calls + turn2_calls
        assert not any(
            "prepare" in c.lower() or "initiate" in c.lower()
            for c in all_calls
        )

        # State: no REFUNDS_INITIATED, no PENDING_REFUNDS
        assert len(REFUNDS_INITIATED) == 0
        assert len(PENDING_REFUNDS) == 0


# ── Journey 2: Authenticated damaged delivery refund ─────────────────────────


class TestJourney2AuthenticatedDamagedDelivery:
    """Authenticated customer gets a refund for a damaged book."""

    def test_prepare_refund_called_and_pending_created(self):
        """Agent calls prepare_refund; a pending token is stored in PENDING_REFUNDS."""
        from agent import SupportAgent

        ctx = CustomerContext(session_id="j2-session", authenticated=True, customer_id="CUST-001")
        agent = SupportAgent(ctx=ctx)

        prepare_resp = _make_response(
            "tool_use",
            [{
                "type": "tool_use",
                "id": "t1",
                "name": "prepare_refund",
                "input": {"order_id": "BK-10042"},
            }],
        )
        ask_confirm_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "A refund of $33.98 has been prepared. Please confirm."}],
        )

        with mock.patch("requests.post", side_effect=[prepare_resp, ask_confirm_resp]):
            reply = agent.reply("my book arrived damaged, I want a refund")

        assert len(PENDING_REFUNDS) == 1
        token = list(PENDING_REFUNDS.keys())[0]
        assert PENDING_REFUNDS[token]["order_id"] == "BK-10042"
        assert PENDING_REFUNDS[token]["session_id"] == "j2-session"
        assert PENDING_REFUNDS[token]["consumed"] is False
        assert "confirm" in reply.lower()

    def test_application_initiates_refund_after_confirmation(self):
        """After customer confirms, the application (not the model) calls initiate_refund."""
        from agent import SupportAgent

        ctx = CustomerContext(session_id="j2-session", authenticated=True, customer_id="CUST-001")
        agent = SupportAgent(ctx=ctx)

        prepare_resp = _make_response(
            "tool_use",
            [{
                "type": "tool_use",
                "id": "t1",
                "name": "prepare_refund",
                "input": {"order_id": "BK-10042"},
            }],
        )
        ask_confirm_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "Refund of $33.98 ready. Please confirm."}],
        )
        confirmed_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "Thank you for confirming."}],
        )

        with mock.patch("requests.post") as mp:
            # Turn 1: agent prepares refund
            mp.side_effect = [prepare_resp, ask_confirm_resp]
            agent.reply("my book arrived damaged, I want a refund")

            # Turn 2: customer confirms (agent just acknowledges)
            mp.side_effect = None
            mp.return_value = confirmed_resp
            agent.reply("yes please confirm")

        # Application-controlled confirmation (simulates /confirm endpoint)
        assert len(PENDING_REFUNDS) == 1
        token = list(PENDING_REFUNDS.keys())[0]
        result = json.loads(initiate_refund("BK-10042", ctx, token))

        assert result["success"] is True
        assert "BK-10042" in REFUNDS_INITIATED
        assert result["amount"] == 33.98

    def test_no_duplicate_refund_on_retry(self):
        """Calling initiate_refund a second time returns already_initiated."""
        from agent import SupportAgent

        ctx = CustomerContext(session_id="j2-session", authenticated=True, customer_id="CUST-001")
        agent = SupportAgent(ctx=ctx)

        prepare_resp = _make_response(
            "tool_use",
            [{"type": "tool_use", "id": "t1", "name": "prepare_refund", "input": {"order_id": "BK-10042"}}],
        )
        end_resp = _make_response(
            "end_turn",
            [{"type": "text", "text": "Refund prepared."}],
        )

        with mock.patch("requests.post", side_effect=[prepare_resp, end_resp]):
            agent.reply("damaged book, refund please")

        token = list(PENDING_REFUNDS.keys())[0]

        # First initiation
        r1 = json.loads(initiate_refund("BK-10042", ctx, token))
        assert r1["success"] is True

        # Retry — same order
        r2 = json.loads(initiate_refund("BK-10042", ctx, token))
        assert r2["status"] == "already_initiated"
        assert len(REFUNDS_INITIATED) == 1  # no duplicate
