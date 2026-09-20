"""
Tool definitions and implementations for the Bookly support agent.

Tool SCHEMAS (TOOL_DEFINITIONS) are sent to the model — they must NOT include
ctx, authenticated, customer_id, or session_id fields.  Those are injected by
the orchestration layer via execute_tool().
"""

import datetime
import hashlib
import json
import secrets

from context import CustomerContext
from data import CUSTOMERS, ORDERS, PENDING_REFUNDS, POLICIES, REFUNDS_INITIATED

# ── Tool definitions sent to the model ───────────────────────────────────────
# ctx is NOT in any schema — it is injected server-side, never by the LLM.

TOOL_DEFINITIONS = [
    {
        "name": "lookup_order",
        "description": (
            "Look up order status and details. Requires both the order ID and the customer's email "
            "address — returns only information scoped to that customer. Never answer questions "
            "about a specific order without calling this first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID, e.g. BK-10042",
                },
                "customer_email": {
                    "type": "string",
                    "description": "The customer's email address",
                },
            },
            "required": ["order_id", "customer_email"],
        },
    },
    {
        "name": "prepare_refund",
        "description": (
            "Prepare a refund for an eligible order. Validates order ownership and return eligibility, "
            "then generates a confirmation token for the customer to approve. Present the refund "
            "amount and payment destination to the customer and ask them to confirm — do not call "
            "this until you have the order ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to refund, e.g. BK-10042",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "search_policies",
        "description": (
            "Search Bookly's policy and FAQ documents. Use when a customer asks about "
            "shipping times, return eligibility, password reset, payment, or order cancellation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic to search, e.g. 'return policy', 'shipping cost', 'cancel order'",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand off to a human support agent with full context. "
            "Use for: fraud, legal claims, account hacking, tool failures, "
            "or any situation the customer explicitly requests a human."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why escalation is needed",
                },
                "summary": {
                    "type": "string",
                    "description": "Conversation summary for the human agent",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]

# ── Tool implementations ──────────────────────────────────────────────────────

_GENERIC_LOOKUP_ERROR = (
    "We could not find an order matching those details. "
    "Please check the order number and email."
)


def lookup_order(order_id: str, customer_email: str, ctx: CustomerContext) -> str:
    """Return order details when both order_id and email match.

    Uses the same generic error for unknown order AND email mismatch to prevent
    order enumeration attacks.
    """
    try:
        key = order_id.strip().upper()
        email = customer_email.strip().lower()
        order = ORDERS.get(key)

        if not order or order["customer_email"].lower() != email:
            return json.dumps({"found": False, "error": _GENERIC_LOOKUP_ERROR})

        safe_fields = [
            "id", "status", "order_date", "shipped_date", "estimated_delivery",
            "delivered_date", "tracking_number", "carrier", "items", "total",
            "eligible_for_return", "return_ineligible_reason",
        ]
        result = {k: order[k] for k in safe_fields if k in order}

        if key in REFUNDS_INITIATED:
            result["refund_status"] = "Refund already initiated — processing in 5–7 business days."

        return json.dumps({"found": True, "order": result})
    except Exception:
        return json.dumps({"success": False, "error": "Unable to look up the order right now."})


def prepare_refund(order_id: str, ctx: CustomerContext) -> str:
    """Prepare a refund for an eligible order.

    Requires ctx.authenticated == True (injected by orchestration layer).
    Validates that the order belongs to the authenticated customer and is
    eligible for return.  Stores a short-lived confirmation token in
    PENDING_REFUNDS; the token is consumed by initiate_refund.
    """
    try:
        if not ctx.authenticated:
            return json.dumps({
                "success": False,
                "error": "Authentication required to prepare a refund.",
            })

        order_key = order_id.strip().upper()

        if not ctx.customer_id or ctx.customer_id not in CUSTOMERS:
            return json.dumps({"success": False, "error": "Customer account not found."})

        if order_key not in CUSTOMERS.get(ctx.customer_id, []):
            return json.dumps({
                "success": False,
                "error": "This order is not associated with your account.",
            })

        order = ORDERS.get(order_key)
        if not order:
            return json.dumps({"success": False, "error": "Order not found."})

        if not order.get("eligible_for_return"):
            reason = order.get("return_ineligible_reason", "This order is not eligible for a return.")
            return json.dumps({"success": False, "error": reason})

        token = secrets.token_urlsafe(16)
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
        amount = order["total"]
        payment_destination = order.get("payment_method", "original payment method")

        PENDING_REFUNDS[token] = {
            "session_id": ctx.session_id,
            "customer_id": ctx.customer_id,
            "order_id": order_key,
            "amount": amount,
            "payment_destination": payment_destination,
            "expires_at": expires_at.isoformat(),
            "consumed": False,
        }

        return json.dumps({
            "success": True,
            "amount": amount,
            "payment_destination": payment_destination,
            "confirmation_required": True,
            "message": (
                f"Refund of ${amount:.2f} prepared for order {order_key} to "
                f"{payment_destination}. Please ask the customer to confirm to proceed."
            ),
        })
    except Exception:
        return json.dumps({"success": False, "error": "Unable to prepare the refund right now."})


def initiate_refund(order_id: str, ctx: CustomerContext, confirmation_token: str) -> str:
    """Submit a confirmed refund.

    Called by the application (e.g. the /confirm endpoint), NOT by the LLM.
    Validates the confirmation token before executing the refund.
    Returns the original result on idempotent retry (status: "already_initiated").
    """
    try:
        order_key = order_id.strip().upper()

        # 1. Idempotency — return original result if already initiated
        if order_key in REFUNDS_INITIATED:
            original = dict(REFUNDS_INITIATED[order_key])
            original["status"] = "already_initiated"
            return json.dumps(original)

        # 2. Token presence
        if not confirmation_token:
            return json.dumps({"success": False, "error": "Confirmation token is required."})

        pending = PENDING_REFUNDS.get(confirmation_token)
        if not pending:
            return json.dumps({"success": False, "error": "Invalid or expired confirmation token."})

        # 3. Token expiry
        expires_at = datetime.datetime.fromisoformat(pending["expires_at"])
        if datetime.datetime.utcnow() > expires_at:
            return json.dumps({
                "success": False,
                "error": "Confirmation token has expired. Please start the refund process again.",
            })

        # 4. Token scope — session, customer, order must all match
        if pending["session_id"] != ctx.session_id:
            return json.dumps({
                "success": False,
                "error": "This confirmation token is not valid for your session.",
            })

        if pending["customer_id"] != ctx.customer_id:
            return json.dumps({
                "success": False,
                "error": "This confirmation token is not valid for your account.",
            })

        if pending["order_id"] != order_key:
            return json.dumps({
                "success": False,
                "error": "This confirmation token is not for this order.",
            })

        # 5. Token consumed check
        if pending.get("consumed"):
            return json.dumps({
                "success": False,
                "error": "This confirmation token has already been used.",
            })

        # All checks passed — execute
        idem_key = hashlib.sha256(
            f"{ctx.customer_id}:{order_key}".encode()
        ).hexdigest()[:16]

        result = {
            "success": True,
            "refund_id": f"REF-{order_key}-{idem_key[:6].upper()}",
            "amount": pending["amount"],
            "payment_destination": pending["payment_destination"],
            "status": "initiated",
            "message": (
                f"Refund of ${pending['amount']:.2f} for order {order_key} has been submitted. "
                f"Funds will appear within 5–7 business days."
            ),
            "idempotency_key": idem_key,
        }

        REFUNDS_INITIATED[order_key] = result

        # Mark token consumed (keep entry for audit trail)
        updated = dict(pending)
        updated["consumed"] = True
        PENDING_REFUNDS[confirmation_token] = updated

        return json.dumps(result)
    except Exception:
        return json.dumps({
            "success": False,
            "error": "An unexpected error occurred processing the refund.",
        })


def search_policies(query: str, ctx: CustomerContext) -> str:
    """Search policy documents by keyword.  Returns no-match if nothing found."""
    try:
        q = query.lower()
        keyword_map = {
            "shipping": ["ship", "deliver", "arrival", "transit", "how long", "tracking"],
            "returns": ["return", "refund", "exchange", "send back", "money back"],
            "password": ["password", "login", "sign in", "reset", "locked", "access"],
            "payment": ["payment", "billing", "charge", "credit card", "invoice", "pay"],
            "cancellation": ["cancel", "cancellation", "stop order"],
        }
        matched = [POLICIES[k] for k, kws in keyword_map.items() if any(w in q for w in kws)]
        if matched:
            return "\n\n---\n\n".join(matched)
        return json.dumps({
            "found": False,
            "message": "No matching policy found for that topic. Please contact support for further assistance.",
        })
    except Exception:
        return json.dumps({"success": False, "error": "Unable to search policies right now."})


def escalate_to_human(reason: str, summary: str, ctx: CustomerContext) -> str:
    """Hand off to a human support agent."""
    try:
        return json.dumps({
            "escalated": True,
            "ticket": f"[ESCALATED] Reason: {reason} | Summary: {summary}",
        })
    except Exception:
        return json.dumps({"success": False, "error": "Unable to escalate right now."})


# ── Dispatch ──────────────────────────────────────────────────────────────────

_REQUIRED_FIELDS: dict[str, list[str]] = {
    "lookup_order": ["order_id", "customer_email"],
    "prepare_refund": ["order_id"],
    "search_policies": ["query"],
    "escalate_to_human": ["reason", "summary"],
}

_KNOWN_TOOLS = set(_REQUIRED_FIELDS)


def execute_tool(name: str, inputs: dict, ctx: CustomerContext) -> str:
    """Validate and dispatch a tool call.  ctx is always injected here, never from the LLM."""
    try:
        if name not in _KNOWN_TOOLS:
            return json.dumps({"success": False, "error": f"Unknown tool '{name}'."})

        missing = [f for f in _REQUIRED_FIELDS[name] if f not in inputs]
        if missing:
            return json.dumps({
                "success": False,
                "error": f"Missing required field(s): {', '.join(missing)}.",
            })

        if name == "lookup_order":
            return lookup_order(
                str(inputs["order_id"]),
                str(inputs["customer_email"]),
                ctx,
            )
        if name == "prepare_refund":
            return prepare_refund(str(inputs["order_id"]), ctx)
        if name == "search_policies":
            return search_policies(str(inputs["query"]), ctx)
        if name == "escalate_to_human":
            return escalate_to_human(str(inputs["reason"]), str(inputs["summary"]), ctx)

        # Should not be reached given _KNOWN_TOOLS guard above
        return json.dumps({"success": False, "error": "Tool dispatch error."})
    except Exception:
        return json.dumps({"success": False, "error": "An unexpected error occurred."})
