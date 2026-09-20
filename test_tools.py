"""
Offline unit tests for tool implementations.
No API key required — all assertions run against in-memory mock data.

Run:  pytest test_tools.py -v
"""
import json
import pytest

from data import REFUNDS_INITIATED, CONFIRMATIONS_RECORDED
from tools import (
    lookup_order,
    record_confirmation,
    initiate_refund,
    search_policies,
    escalate_to_human,
)


@pytest.fixture(autouse=True)
def reset_write_logs():
    """Clear in-memory write logs before and after every test."""
    REFUNDS_INITIATED.clear()
    CONFIRMATIONS_RECORDED.clear()
    yield
    REFUNDS_INITIATED.clear()
    CONFIRMATIONS_RECORDED.clear()


# ── lookup_order ──────────────────────────────────────────────────────────────

class TestLookupOrder:
    def test_unknown_order_id_returns_not_found(self):
        result = json.loads(lookup_order("BK-XXXX", "anyone@example.com"))
        assert result["found"] is False
        assert "BK-XXXX" in result["error"]

    def test_wrong_email_is_rejected(self):
        result = json.loads(lookup_order("BK-10042", "wrong@email.com"))
        assert result["found"] is False
        assert "do not match" in result["error"]

    def test_correct_credentials_return_order(self):
        result = json.loads(lookup_order("BK-10042", "jane.doe@email.com"))
        assert result["found"] is True
        assert result["order"]["id"] == "BK-10042"
        assert result["order"]["status"] == "shipped"

    def test_order_id_and_email_are_case_insensitive(self):
        result = json.loads(lookup_order("bk-10042", "Jane.Doe@Email.COM"))
        assert result["found"] is True

    def test_internal_customer_name_field_not_exposed(self):
        result = json.loads(lookup_order("BK-10042", "jane.doe@email.com"))
        # customer_name is not in safe_fields — must never be returned
        assert "customer_name" not in result["order"]

    def test_refund_status_shown_if_already_initiated(self):
        REFUNDS_INITIATED["BK-10042"] = {
            "reason": "damaged_item", "status": "initiated", "idempotency_key": "abc123"
        }
        result = json.loads(lookup_order("BK-10042", "jane.doe@email.com"))
        assert "refund_status" in result["order"]

    def test_ineligible_reason_included_for_blocked_orders(self):
        result = json.loads(lookup_order("BK-8823", "alex.j@email.com"))
        assert result["found"] is True
        assert "return_ineligible_reason" in result["order"]


# ── record_confirmation ───────────────────────────────────────────────────────

class TestRecordConfirmation:
    def test_records_confirmation_for_valid_identity(self):
        result = json.loads(record_confirmation("BK-10105", "sarah.lee@email.com"))
        assert result["success"] is True
        assert CONFIRMATIONS_RECORDED.get("BK-10105") == "sarah.lee@email.com"

    def test_wrong_email_fails_identity_check(self):
        result = json.loads(record_confirmation("BK-10105", "wrong@email.com"))
        assert result["success"] is False
        assert "identity" in result["error"].lower()

    def test_unknown_order_fails(self):
        result = json.loads(record_confirmation("BK-FAKE", "anyone@email.com"))
        assert result["success"] is False


# ── initiate_refund ───────────────────────────────────────────────────────────

class TestInitiateRefund:
    """
    initiate_refund enforces four sequential checks.
    Each test isolates one check by satisfying all prior ones.
    """

    def _record(self, order_id: str, email: str) -> None:
        CONFIRMATIONS_RECORDED[order_id.upper()] = email.lower()

    # Check 1 — identity
    def test_wrong_email_blocked_at_identity_check(self):
        result = json.loads(initiate_refund("BK-10105", "wrong@email.com", "damaged_item"))
        assert result["success"] is False
        assert "identity" in result["error"].lower()

    # Check 2 — idempotency
    def test_duplicate_submission_is_rejected(self):
        self._record("BK-10105", "sarah.lee@email.com")
        initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item")
        result = json.loads(initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item"))
        assert result["success"] is False
        assert "already been submitted" in result["error"]

    # Check 3 — eligibility: outside return window
    def test_order_outside_30_day_window_is_rejected(self):
        self._record("BK-8823", "alex.j@email.com")
        result = json.loads(initiate_refund("BK-8823", "alex.j@email.com", "changed_mind"))
        assert result["success"] is False
        assert "30-day" in result["error"]

    # Check 3 — eligibility: order still processing
    def test_processing_order_rejected_with_cancel_suggestion(self):
        self._record("BK-9871", "john.smith@email.com")
        result = json.loads(initiate_refund("BK-9871", "john.smith@email.com", "changed_mind"))
        assert result["success"] is False
        assert "processing" in result["error"].lower()

    # Check 4 — confirmation gate
    def test_refund_blocked_when_confirmation_not_recorded(self):
        # Identity, idempotency, eligibility all pass — only confirmation missing
        result = json.loads(initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item"))
        assert result["success"] is False
        assert "confirmation" in result["error"].lower()

    # Happy path
    def test_all_checks_pass_returns_refund_id(self):
        self._record("BK-10105", "sarah.lee@email.com")
        result = json.loads(initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item"))
        assert result["success"] is True
        assert result["refund_id"].startswith("REF-BK-10105")
        assert "BK-10105" in REFUNDS_INITIATED

    def test_idempotency_key_is_deterministic(self):
        """Same order + email always produces the same key regardless of when it runs."""
        self._record("BK-10105", "sarah.lee@email.com")
        initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item")
        key1 = REFUNDS_INITIATED["BK-10105"]["idempotency_key"]

        REFUNDS_INITIATED.clear()
        self._record("BK-10105", "sarah.lee@email.com")
        initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item")
        key2 = REFUNDS_INITIATED["BK-10105"]["idempotency_key"]

        assert key1 == key2

    def test_second_eligible_order_can_be_refunded_independently(self):
        self._record("BK-10042", "jane.doe@email.com")
        result = json.loads(initiate_refund("BK-10042", "jane.doe@email.com", "wrong_item"))
        assert result["success"] is True


# ── search_policies ───────────────────────────────────────────────────────────

class TestSearchPolicies:
    def test_shipping_keyword_returns_shipping_section(self):
        result = search_policies("how long does shipping take")
        assert "Standard" in result

    def test_refund_keyword_returns_returns_section(self):
        result = search_policies("can I get a refund")
        assert "30-day" in result

    def test_cancel_keyword_returns_cancellation_section(self):
        result = search_policies("how do I cancel my order")
        assert "Cancel" in result or "cancellation" in result.lower()

    def test_password_keyword_returns_password_section(self):
        result = search_policies("I forgot my password")
        assert "Forgot Password" in result or "password" in result.lower()

    def test_unrecognised_query_returns_all_policies(self):
        result = search_policies("xyzabc123 completely unknown")
        assert "shipping" in result.lower()
        assert "return" in result.lower()
        assert "password" in result.lower()


# ── escalate_to_human ─────────────────────────────────────────────────────────

class TestEscalateToHuman:
    def test_escalated_flag_is_true(self):
        result = json.loads(escalate_to_human("fraud", "Customer reports unauthorised charge"))
        assert result["escalated"] is True

    def test_ticket_contains_both_reason_and_summary(self):
        result = json.loads(escalate_to_human("account_hacked", "user locked out of account"))
        assert "account_hacked" in result["ticket"]
        assert "user locked out" in result["ticket"]


# ── Confirmation gate: end-to-end ─────────────────────────────────────────────

class TestConfirmationGateEndToEnd:
    """
    The two-step write pattern:
    record_confirmation must precede initiate_refund — enforced in code, not prompt.
    """

    def test_refund_rejected_before_confirmation(self):
        result = json.loads(initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item"))
        assert result["success"] is False

    def test_refund_succeeds_after_confirmation(self):
        record_confirmation("BK-10105", "sarah.lee@email.com")
        result = json.loads(initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item"))
        assert result["success"] is True

    def test_confirming_order_a_does_not_unlock_order_b(self):
        record_confirmation("BK-10042", "jane.doe@email.com")
        result = json.loads(initiate_refund("BK-10105", "sarah.lee@email.com", "damaged_item"))
        assert result["success"] is False

    def test_confirmation_scoped_to_email_not_just_order_id(self):
        # Confirm with correct email, then attempt refund with a different one
        record_confirmation("BK-10105", "sarah.lee@email.com")
        result = json.loads(initiate_refund("BK-10105", "wrong@email.com", "damaged_item"))
        assert result["success"] is False
