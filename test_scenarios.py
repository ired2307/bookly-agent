"""
Automated demo scenarios — requires ANTHROPIC_API_KEY in .env.
Run: python test_scenarios.py
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from context import CustomerContext
from data import PENDING_REFUNDS, REFUNDS_INITIATED
from tools import initiate_refund


def _make_agent(authenticated=False, customer_id=None):
    from agent import SupportAgent
    import secrets
    session_id = secrets.token_urlsafe(8)
    ctx = CustomerContext(
        session_id=session_id,
        authenticated=authenticated,
        customer_id=customer_id,
    )
    return SupportAgent(ctx=ctx), session_id


def scenario(title, turns, authenticated=False, customer_id=None):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)
    agent, session_id = _make_agent(authenticated=authenticated, customer_id=customer_id)
    for msg in turns:
        print(f"\nYou:  {msg}")
        reply = agent.reply(msg)
        print(f"Aria: {reply}")
    return agent, session_id


if __name__ == "__main__":
    # 1. Clarifying question
    scenario(
        "Clarifying question: vague refund request",
        ["I want to return something."],
    )

    # 2. Multi-turn order lookup (guest)
    scenario(
        "Multi-turn: order status across two messages",
        [
            "Can you check on my order?",
            "It's BK-10042, email is jane.doe@email.com",
        ],
    )

    # 3. Authenticated refund — prepare_refund + app-controlled confirmation
    print(f"\n{'=' * 60}")
    print("  Authenticated refund: damaged delivery (Sarah Lee / CUST-004)")
    print("=" * 60)
    agent, session_id = _make_agent(authenticated=True, customer_id="CUST-004")

    print("\nYou:  My book arrived damaged. Order BK-10105.")
    reply = agent.reply("My book arrived damaged. Order BK-10105.")
    print(f"Aria: {reply}")

    # Simulate customer confirmation via application (not model)
    if PENDING_REFUNDS:
        token = next(
            t for t, d in PENDING_REFUNDS.items()
            if d["session_id"] == session_id and not d.get("consumed")
        )
        ctx = agent._ctx
        result = json.loads(initiate_refund("BK-10105", ctx, token))
        print(f"\n[Application] Confirmation received — calling initiate_refund...")
        print(f"[Application] Result: {result}")
        if result.get("success"):
            print(f"\nYou:  [Customer confirmed via UI button]")
            reply = agent.reply("I confirmed the refund.")
            print(f"Aria: {reply}")
    else:
        print("\n[No pending refund found — agent may need more info]")

    # 4. Ineligible return — outside 30-day window
    scenario(
        "Ineligible return: outside 30-day window",
        ["Can I return order BK-8823? My email is alex.j@email.com."],
    )

    # 5. Ineligible return — order still processing
    scenario(
        "Ineligible return: order not yet shipped",
        ["I want to return BK-9871. Email is john.smith@email.com."],
    )

    # 6. Policy question
    scenario(
        "Policy question: shipping times",
        ["How long does standard shipping take?"],
    )

    # 7. Escalation
    scenario(
        "Escalation: suspected account compromise",
        ["I think someone hacked my account and placed orders without my permission."],
    )
