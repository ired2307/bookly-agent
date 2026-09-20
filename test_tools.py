"""
Offline unit tests for tool implementations and the HTTP client loop.
No API key required — all assertions run against in-memory mock data.

Run:  pytest test_tools.py -v
"""
import datetime
import json
import unittest.mock as mock

import pytest
import requests

from context import CustomerContext
from data import PENDING_REFUNDS, REFUNDS_INITIATED
from tools import (
    escalate_to_human,
    execute_tool,
    initiate_refund,
    lookup_order,
    prepare_refund,
    search_policies,
)

# ── Shared test contexts ───────────────────────────────────────────────────────

GUEST_CTX = CustomerContext(session_id="test-session", authenticated=False)
AUTH_CTX = CustomerContext(session_id="test-session", authenticated=True, customer_id="CUST-001")
AUTH_CTX_CUST004 = CustomerContext(
    session_id="test-session", authenticated=True, customer_id="CUST-004"
)


def _make_response(stop_reason: str, content: list, status_code: int = 200):
    """Return a mock requests.Response with the given Anthropic API shape."""
    m = mock.Mock()
    m.ok = status_code < 400
    m.status_code = status_code
    m.headers = {}
    m.json.return_value = {"stop_reason": stop_reason, "content": content}
    return m


# ── lookup_order ──────────────────────────────────────────────────────────────


class TestLookupOrder:
    def test_correct_credentials_return_order(self):
        result = json.loads(lookup_order("BK-10042", "jane.doe@email.com", GUEST_CTX))
        assert result["found"] is True
        assert result["order"]["id"] == "BK-10042"
        assert result["order"]["status"] == "shipped"

    def test_order_id_and_email_are_case_insensitive(self):
        result = json.loads(lookup_order("bk-10042", "Jane.Doe@Email.COM", GUEST_CTX))
        assert result["found"] is True

    def test_unknown_order_id_returns_generic_error(self):
        result = json.loads(lookup_order("BK-XXXX", "anyone@example.com", GUEST_CTX))
        assert result["found"] is False
        assert "could not find" in result["error"].lower()

    def test_wrong_email_returns_generic_error(self):
        result = json.loads(lookup_order("BK-10042", "wrong@email.com", GUEST_CTX))
        assert result["found"] is False
        assert "could not find" in result["error"].lower()

    def test_unknown_order_and_wrong_email_indistinguishable(self):
        """Enumeration prevention: both unknown order and email mismatch return identical messages."""
        unknown = json.loads(lookup_order("BK-XXXX", "anyone@example.com", GUEST_CTX))
        mismatch = json.loads(lookup_order("BK-10042", "wrong@email.com", GUEST_CTX))
        assert unknown["error"] == mismatch["error"]

    def test_internal_customer_name_field_not_exposed(self):
        result = json.loads(lookup_order("BK-10042", "jane.doe@email.com", GUEST_CTX))
        assert "customer_name" not in result["order"]

    def test_refund_status_shown_if_already_initiated(self):
        REFUNDS_INITIATED["BK-10042"] = {
            "success": True,
            "status": "initiated",
            "idempotency_key": "abc123",
        }
        result = json.loads(lookup_order("BK-10042", "jane.doe@email.com", GUEST_CTX))
        assert "refund_status" in result["order"]

    def test_ineligible_reason_included_for_blocked_orders(self):
        result = json.loads(lookup_order("BK-8823", "alex.j@email.com", GUEST_CTX))
        assert result["found"] is True
        assert "return_ineligible_reason" in result["order"]


# ── prepare_refund ────────────────────────────────────────────────────────────


class TestPrepareRefund:
    def test_guest_cannot_prepare_refund(self):
        result = json.loads(prepare_refund("BK-10042", GUEST_CTX))
        assert result["success"] is False
        assert "authentication" in result["error"].lower()

    def test_authenticated_can_prepare_refund(self):
        result = json.loads(prepare_refund("BK-10042", AUTH_CTX))
        assert result["success"] is True
        assert result["amount"] == 33.98
        assert len(PENDING_REFUNDS) == 1

    def test_prepare_refund_creates_pending_token(self):
        prepare_refund("BK-10042", AUTH_CTX)
        assert len(PENDING_REFUNDS) == 1
        token = list(PENDING_REFUNDS.keys())[0]
        pending = PENDING_REFUNDS[token]
        assert pending["order_id"] == "BK-10042"
        assert pending["customer_id"] == "CUST-001"
        assert pending["session_id"] == "test-session"
        assert pending["consumed"] is False

    def test_prepare_refund_wrong_customer(self):
        """CUST-001 cannot prepare a refund for CUST-002's order."""
        result = json.loads(prepare_refund("BK-9871", AUTH_CTX))
        assert result["success"] is False

    def test_prepare_refund_ineligible_order(self):
        """CUST-003 owns BK-8823 but it is outside the return window."""
        ctx = CustomerContext(session_id="test-session", authenticated=True, customer_id="CUST-003")
        result = json.loads(prepare_refund("BK-8823", ctx))
        assert result["success"] is False
        assert "30-day" in result["error"]

    def test_prepare_refund_eligible_order_cust004(self):
        result = json.loads(prepare_refund("BK-10105", AUTH_CTX_CUST004))
        assert result["success"] is True
        assert result["amount"] == 15.99


# ── initiate_refund ───────────────────────────────────────────────────────────


class TestInitiateRefund:
    def _prepare(self, ctx: CustomerContext = AUTH_CTX, order_id: str = "BK-10042") -> str:
        """Helper: prepare a refund and return the token."""
        prepare_refund(order_id, ctx)
        return list(PENDING_REFUNDS.keys())[0]

    def test_initiate_refund_without_token_fails(self):
        result = json.loads(initiate_refund("BK-10042", AUTH_CTX, ""))
        assert result["success"] is False

    def test_initiate_refund_with_invalid_token_fails(self):
        result = json.loads(initiate_refund("BK-10042", AUTH_CTX, "nonexistent_token"))
        assert result["success"] is False

    def test_initiate_refund_with_expired_token_fails(self):
        expired_token = "expired_token_123"
        PENDING_REFUNDS[expired_token] = {
            "session_id": "test-session",
            "customer_id": "CUST-001",
            "order_id": "BK-10042",
            "amount": 33.98,
            "payment_destination": "original payment method",
            "expires_at": (
                datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
            ).isoformat(),
            "consumed": False,
        }
        result = json.loads(initiate_refund("BK-10042", AUTH_CTX, expired_token))
        assert result["success"] is False
        assert "expired" in result["error"].lower()

    def test_initiate_refund_with_wrong_session_fails(self):
        wrong_session_token = "wrong_session_token"
        PENDING_REFUNDS[wrong_session_token] = {
            "session_id": "other-session",
            "customer_id": "CUST-001",
            "order_id": "BK-10042",
            "amount": 33.98,
            "payment_destination": "original payment method",
            "expires_at": (
                datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
            ).isoformat(),
            "consumed": False,
        }
        result = json.loads(initiate_refund("BK-10042", AUTH_CTX, wrong_session_token))
        assert result["success"] is False

    def test_initiate_refund_succeeds_with_valid_token(self):
        token = self._prepare()
        result = json.loads(initiate_refund("BK-10042", AUTH_CTX, token))
        assert result["success"] is True
        assert result["refund_id"].startswith("REF-BK-10042")
        assert "BK-10042" in REFUNDS_INITIATED

    def test_token_marked_consumed_after_success(self):
        token = self._prepare()
        initiate_refund("BK-10042", AUTH_CTX, token)
        assert PENDING_REFUNDS[token]["consumed"] is True

    def test_idempotent_refund_returns_original_result(self):
        """Second call with the same order returns 'already_initiated', no new refund."""
        token = self._prepare()
        result1 = json.loads(initiate_refund("BK-10042", AUTH_CTX, token))
        assert result1["success"] is True

        result2 = json.loads(initiate_refund("BK-10042", AUTH_CTX, token))
        assert result2["status"] == "already_initiated"
        assert len(REFUNDS_INITIATED) == 1

    def test_idempotent_refund_different_session_returns_already_initiated(self):
        """Even from a different session, the same order returns already_initiated."""
        token = self._prepare()
        initiate_refund("BK-10042", AUTH_CTX, token)

        # Different session, same order
        other_ctx = CustomerContext(session_id="other-session", authenticated=True, customer_id="CUST-001")
        result = json.loads(initiate_refund("BK-10042", other_ctx, "any_token"))
        assert result["status"] == "already_initiated"


# ── search_policies ───────────────────────────────────────────────────────────


class TestSearchPolicies:
    def test_shipping_keyword_returns_shipping_section(self):
        result = search_policies("how long does shipping take", GUEST_CTX)
        assert "Standard" in result

    def test_refund_keyword_returns_returns_section(self):
        result = search_policies("can I get a refund", GUEST_CTX)
        assert "30-day" in result

    def test_cancel_keyword_returns_cancellation_section(self):
        result = search_policies("how do I cancel my order", GUEST_CTX)
        assert "Cancel" in result or "cancellation" in result.lower()

    def test_password_keyword_returns_password_section(self):
        result = search_policies("I forgot my password", GUEST_CTX)
        assert "Forgot Password" in result or "password" in result.lower()

    def test_unrecognised_query_returns_no_match(self):
        result = search_policies("xyzabc123 completely unknown", GUEST_CTX)
        data = json.loads(result)
        assert data["found"] is False
        assert "No matching policy" in data["message"]


# ── escalate_to_human ─────────────────────────────────────────────────────────


class TestEscalateToHuman:
    def test_escalated_flag_is_true(self):
        result = json.loads(escalate_to_human("fraud", "Customer reports unauthorised charge", GUEST_CTX))
        assert result["escalated"] is True

    def test_ticket_contains_both_reason_and_summary(self):
        result = json.loads(
            escalate_to_human("account_hacked", "user locked out of account", GUEST_CTX)
        )
        assert "account_hacked" in result["ticket"]
        assert "user locked out" in result["ticket"]


# ── execute_tool validation ───────────────────────────────────────────────────


class TestExecuteToolValidation:
    def test_unknown_tool_name_returns_error(self):
        result = json.loads(execute_tool("nonexistent_tool", {}, GUEST_CTX))
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_missing_required_field_returns_error(self):
        result = json.loads(execute_tool("lookup_order", {"order_id": "BK-10042"}, GUEST_CTX))
        assert result["success"] is False
        assert "customer_email" in result["error"]

    def test_valid_dispatch_works(self):
        result = json.loads(
            execute_tool(
                "lookup_order",
                {"order_id": "BK-10042", "customer_email": "jane.doe@email.com"},
                GUEST_CTX,
            )
        )
        assert result["found"] is True


# ── HTTP client loop tests (mocked) ──────────────────────────────────────────


class TestConversationClientLoop:
    """Tests for client.py using mocked requests.post — no API key required."""

    def _make_client(self, max_steps: int = 10):
        from client import ConversationClient

        return ConversationClient(
            api_key="test-key",
            system="test system",
            model="test-model",
            max_tokens=100,
            max_steps=max_steps,
        )

    def test_end_turn_returns_response(self):
        client = self._make_client()
        resp = _make_response("end_turn", [{"type": "text", "text": "Hello there"}])
        with mock.patch("requests.post", return_value=resp):
            result = client.chat("hi", [], lambda n, i: "")
        assert result == "Hello there"

    def test_tool_use_executes_and_loops(self):
        client = self._make_client()
        tool_resp = _make_response("tool_use", [{
            "type": "tool_use",
            "id": "t1",
            "name": "lookup_order",
            "input": {"order_id": "BK-10042", "customer_email": "jane.doe@email.com"},
        }])
        end_resp = _make_response("end_turn", [{"type": "text", "text": "Your order is shipped."}])

        called = []

        def on_tool(name, inputs):
            called.append(name)
            return json.dumps({"found": True, "order": {"status": "shipped"}})

        with mock.patch("requests.post", side_effect=[tool_resp, end_resp]):
            result = client.chat("where is my order", [], on_tool)

        assert "lookup_order" in called
        assert "shipped" in result

    def test_multiple_tools_in_one_response(self):
        client = self._make_client()
        two_tools = _make_response("tool_use", [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "lookup_order",
                "input": {"order_id": "BK-10042", "customer_email": "jane.doe@email.com"},
            },
            {
                "type": "tool_use",
                "id": "t2",
                "name": "search_policies",
                "input": {"query": "return policy"},
            },
        ])
        end_resp = _make_response("end_turn", [{"type": "text", "text": "Done."}])

        called = []

        def on_tool(name, inputs):
            called.append(name)
            return json.dumps({"ok": True})

        with mock.patch("requests.post", side_effect=[two_tools, end_resp]) as mp:
            client.chat("help", [], on_tool)

        assert called == ["lookup_order", "search_policies"]
        # Both tool results should be in a single user message
        tool_result_msg = client.history[-2]
        assert tool_result_msg["role"] == "user"
        assert len(tool_result_msg["content"]) == 2

    def test_tool_error_returns_to_model(self):
        """When on_tool_call raises, is_error:True is sent back to the model."""
        client = self._make_client()
        tool_resp = _make_response("tool_use", [{
            "type": "tool_use",
            "id": "t1",
            "name": "lookup_order",
            "input": {"order_id": "BK-10042", "customer_email": "test@test.com"},
        }])
        end_resp = _make_response("end_turn", [{"type": "text", "text": "Sorry about that."}])

        def failing_tool(name, inputs):
            raise RuntimeError("Tool exploded!")

        with mock.patch("requests.post", side_effect=[tool_resp, end_resp]) as mp:
            client.chat("look up my order", [], failing_tool)

        # The second _post call's "messages" is a reference to client.history.
        # After chat() returns, history = [user, assistant, user(tool_result), assistant(end_turn)].
        # The tool_result user message was at index -1 during the second call, but is now at -2.
        second_call_kwargs = mp.call_args_list[1][1]
        messages = second_call_kwargs["json"]["messages"]
        tool_result_msg = messages[-2]  # user message containing the tool_result block
        assert tool_result_msg["role"] == "user"
        tool_result_block = tool_result_msg["content"][0]
        assert tool_result_block.get("is_error") is True

    def test_unknown_tool_name_handled(self):
        """Model requesting an unknown tool name must not crash the loop."""
        client = self._make_client()
        tool_resp = _make_response("tool_use", [{
            "type": "tool_use",
            "id": "t1",
            "name": "totally_fake_tool",
            "input": {},
        }])
        end_resp = _make_response("end_turn", [{"type": "text", "text": "OK."}])

        with mock.patch("requests.post", side_effect=[tool_resp, end_resp]):
            result = client.chat("do something", [], lambda n, i: execute_tool(n, i, GUEST_CTX))

        assert result == "OK."

    def test_max_tokens_handled_safely(self):
        client = self._make_client()
        resp = _make_response("max_tokens", [{"type": "text", "text": "partial..."}])
        with mock.patch("requests.post", return_value=resp):
            result = client.chat("give me a long answer", [], lambda n, i: "")
        assert "human agent" in result.lower() or "more space" in result.lower()

    def test_tool_iteration_limit(self):
        """When the loop hits max_steps, a safe handoff message is returned."""
        client = self._make_client(max_steps=3)
        always_tool = _make_response("tool_use", [{
            "type": "tool_use",
            "id": "t1",
            "name": "lookup_order",
            "input": {"order_id": "BK-10042", "customer_email": "jane.doe@email.com"},
        }])

        with mock.patch("requests.post", return_value=always_tool) as mp:
            result = client.chat("loop forever", [], lambda n, i: json.dumps({"found": True}))

        assert mp.call_count == 3
        assert "human agent" in result.lower() or "unable" in result.lower()

    def test_transient_failure_retried(self):
        """A Timeout on the first attempt is retried and succeeds on the second."""
        client = self._make_client()
        success = _make_response("end_turn", [{"type": "text", "text": "Hello"}])

        with mock.patch("requests.post", side_effect=[requests.Timeout(), success]) as mp, \
                mock.patch("time.sleep"):
            result = client.chat("hi", [], lambda n, i: "")

        assert result == "Hello"
        assert mp.call_count == 2

    def test_permanent_failure_not_retried(self):
        """A 401 is non-retryable — only one attempt should be made."""
        client = self._make_client()
        auth_error = _make_response("end_turn", [], status_code=401)

        with mock.patch("requests.post", return_value=auth_error) as mp, \
                mock.patch("time.sleep"):
            with pytest.raises(RuntimeError):
                client.chat("hi", [], lambda n, i: "")

        assert mp.call_count == 1

    def test_retry_limit_respected(self):
        """After MAX_HTTP_RETRIES+1 Timeouts, RuntimeError is raised."""
        from config import settings

        client = self._make_client()
        total_attempts = settings.MAX_HTTP_RETRIES + 1

        with mock.patch("requests.post", side_effect=[requests.Timeout()] * total_attempts) as mp, \
                mock.patch("time.sleep"):
            with pytest.raises(RuntimeError):
                client.chat("hi", [], lambda n, i: "")

        assert mp.call_count == total_attempts


# ── Endpoint tests ────────────────────────────────────────────────────────────


class TestEndpoints:
    """FastAPI endpoint integration tests — no API key, no network access."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        import server
        with mock.patch.object(server.SupportAgent, "reply", return_value="I can help with that."):
            with TestClient(server.app) as c:
                yield c

    def test_session_creation_returns_session_id(self, client):
        res = client.post("/session", json={"mode": "guest"})
        assert res.status_code == 200
        assert len(res.json()["session_id"]) > 0

    def test_sarah_demo_session_is_authenticated(self, client):
        from server import _contexts
        res = client.post("/session", json={"mode": "sarah_demo"})
        sid = res.json()["session_id"]
        assert _contexts[sid].authenticated is True
        assert _contexts[sid].customer_id == "CUST-004"

    def test_chat_rejects_unknown_session(self, client):
        res = client.post("/chat", json={"message": "hello", "session_id": "fake-id"})
        assert res.status_code == 404

    def test_chat_rejects_oversized_message(self, client):
        sid = client.post("/session", json={"mode": "guest"}).json()["session_id"]
        res = client.post("/chat", json={"message": "x" * 4001, "session_id": sid})
        assert res.status_code == 400

    def test_chat_returns_reply_and_pending_refund_field(self, client):
        sid = client.post("/session", json={"mode": "guest"}).json()["session_id"]
        res = client.post("/chat", json={"message": "hello", "session_id": sid})
        assert res.status_code == 200
        data = res.json()
        assert "reply" in data
        assert "pending_refund" in data
        assert data["pending_refund"] is None

    def test_confirm_returns_404_without_pending_refund(self, client):
        sid = client.post("/session", json={"mode": "sarah_demo"}).json()["session_id"]
        assert client.post(f"/confirm/{sid}").status_code == 404

    def test_confirm_succeeds_with_pending_refund(self, client):
        import datetime
        from server import _contexts
        sid = client.post("/session", json={"mode": "sarah_demo"}).json()["session_id"]
        PENDING_REFUNDS["tok-1"] = {
            "session_id": sid,
            "customer_id": "CUST-004",
            "order_id": "BK-10105",
            "amount": 15.99,
            "payment_destination": "original payment method",
            "expires_at": (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat(),
            "consumed": False,
        }
        res = client.post(f"/confirm/{sid}")
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert "BK-10105" in REFUNDS_INITIATED

    def test_session_deletion_removes_session(self, client):
        from server import _sessions
        sid = client.post("/session", json={"mode": "guest"}).json()["session_id"]
        assert sid in _sessions
        client.delete(f"/session/{sid}")
        assert sid not in _sessions
