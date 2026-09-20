import json
import hashlib
from data import ORDERS, REFUNDS_INITIATED, CONFIRMATIONS_RECORDED, POLICIES

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
                    "description": "The customer's email address — must match the order to return details",
                },
            },
            "required": ["order_id", "customer_email"],
        },
    },
    {
        "name": "record_confirmation",
        "description": (
            "Record that the customer has explicitly confirmed they want to proceed with a refund. "
            "Call this ONLY after: (1) you have shown the customer the order details via lookup_order, "
            "and (2) the customer has said yes in their own words. "
            "initiate_refund will reject execution unless this has been called first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID the customer confirmed",
                },
                "customer_email": {
                    "type": "string",
                    "description": "The customer's email — used to scope the confirmation",
                },
            },
            "required": ["order_id", "customer_email"],
        },
    },
    {
        "name": "initiate_refund",
        "description": (
            "Submit a refund for an eligible order. The tool enforces: customer identity match, "
            "return eligibility, recorded confirmation, and idempotency. "
            "It will reject execution if any check fails — do not attempt to work around rejections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to refund",
                },
                "customer_email": {
                    "type": "string",
                    "description": "Must match the email on the order",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the refund",
                    "enum": ["changed_mind", "damaged_item", "wrong_item", "not_as_described", "other"],
                },
            },
            "required": ["order_id", "customer_email", "reason"],
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


# ── Tool implementations ─────────────────────────────────────────────────────

def lookup_order(order_id: str, customer_email: str) -> str:
    key = order_id.strip().upper()
    email = customer_email.strip().lower()
    order = ORDERS.get(key)

    if not order:
        return json.dumps({"found": False, "error": f"No order found with ID '{key}'."})

    # Identity check — order number alone is not sufficient
    if order["customer_email"].lower() != email:
        return json.dumps({
            "found": False,
            "error": "Order ID and email do not match. Please check both and try again.",
        })

    # Return only customer-scoped fields (no internal flags)
    safe_fields = [
        "id", "status", "order_date", "shipped_date", "estimated_delivery",
        "delivered_date", "tracking_number", "carrier", "items", "total",
        "eligible_for_return", "return_ineligible_reason",
    ]
    result = {k: order[k] for k in safe_fields if k in order}
    if key in REFUNDS_INITIATED:
        result["refund_status"] = "Refund already initiated — processing in 5–7 business days."

    return json.dumps({"found": True, "order": result})


def record_confirmation(order_id: str, customer_email: str) -> str:
    key = order_id.strip().upper()
    email = customer_email.strip().lower()
    order = ORDERS.get(key)

    if not order or order["customer_email"].lower() != email:
        return json.dumps({"success": False, "error": "Cannot record confirmation — identity check failed."})

    CONFIRMATIONS_RECORDED[key] = email
    return json.dumps({"success": True, "message": f"Confirmation recorded for order {key}."})


def initiate_refund(order_id: str, customer_email: str, reason: str) -> str:
    key = order_id.strip().upper()
    email = customer_email.strip().lower()
    order = ORDERS.get(key)

    # 1. Identity check
    if not order or order["customer_email"].lower() != email:
        return json.dumps({"success": False, "error": "Identity check failed — order ID and email do not match."})

    # 2. Idempotency — reject duplicate submissions
    if key in REFUNDS_INITIATED:
        return json.dumps({"success": False, "error": f"A refund for {key} has already been submitted."})

    # 3. Eligibility check (business logic enforced in code, not prompt)
    if not order.get("eligible_for_return"):
        msg = order.get("return_ineligible_reason", "This order is not eligible for a return.")
        return json.dumps({"success": False, "error": msg})

    # 4. Confirmation check — must be recorded before execution
    if CONFIRMATIONS_RECORDED.get(key) != email:
        return json.dumps({
            "success": False,
            "error": "No confirmation recorded for this order. Ask the customer to confirm before retrying.",
        })

    # All checks passed — execute
    idempotency_key = hashlib.sha256(f"{key}:{email}".encode()).hexdigest()[:16]
    REFUNDS_INITIATED[key] = {"reason": reason, "status": "initiated", "idempotency_key": idempotency_key}

    return json.dumps({
        "success": True,
        "refund_id": f"REF-{key}-{idempotency_key[:6].upper()}",
        "message": (
            f"Refund for order {key} has been submitted. "
            f"Confirmation will be sent to {order['customer_email']}. "
            f"Funds will appear within 5–7 business days."
        ),
    })


def search_policies(query: str) -> str:
    q = query.lower()
    keyword_map = {
        "shipping":     ["ship", "deliver", "arrival", "transit", "how long", "tracking"],
        "returns":      ["return", "refund", "exchange", "send back", "money back"],
        "password":     ["password", "login", "sign in", "reset", "locked", "access"],
        "payment":      ["payment", "billing", "charge", "credit card", "invoice", "pay"],
        "cancellation": ["cancel", "cancellation", "stop order"],
    }
    matched = [POLICIES[k] for k, kws in keyword_map.items() if any(w in q for w in kws)]
    return "\n\n---\n\n".join(matched) if matched else "\n\n---\n\n".join(POLICIES.values())


def escalate_to_human(reason: str, summary: str) -> str:
    return json.dumps({
        "escalated": True,
        "ticket": f"[ESCALATED] Reason: {reason} | Summary: {summary}",
    })


def execute_tool(name: str, inputs: dict) -> str:
    dispatch = {
        "lookup_order":       lambda: lookup_order(inputs["order_id"], inputs["customer_email"]),
        "record_confirmation": lambda: record_confirmation(inputs["order_id"], inputs["customer_email"]),
        "initiate_refund":    lambda: initiate_refund(inputs["order_id"], inputs["customer_email"], inputs["reason"]),
        "search_policies":    lambda: search_policies(inputs["query"]),
        "escalate_to_human":  lambda: escalate_to_human(inputs["reason"], inputs["summary"]),
    }
    fn = dispatch.get(name)
    return fn() if fn else json.dumps({"error": f"Unknown tool: {name}"})
