"""
Automated scenario tests — no interactive input required.
Usage: python test_scenarios.py  (requires ANTHROPIC_API_KEY in .env or environment)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from agent import SupportAgent


def scenario(title: str, turns: list[str]) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)
    agent = SupportAgent()
    for msg in turns:
        print(f"\nYou:  {msg}")
        reply = agent.reply(msg)
        print(f"Aria: {reply}")


if __name__ == "__main__":
    # 1. Clarifying question — agent asks for order ID and email before acting
    scenario(
        "Clarifying question: vague refund request",
        ["I want to return something."],
    )

    # 2. Multi-turn order lookup — customer provides ID and email across two messages
    scenario(
        "Multi-turn: order status across two messages",
        [
            "Can you check on my order?",
            "It's BK-10042, email is jane.doe@email.com",
        ],
    )

    # 3. Full refund flow — agent calls lookup_order, shows details, requires explicit
    #    confirmation (record_confirmation) before initiate_refund will execute
    scenario(
        "Refund flow: damaged item — confirmation gate enforced in code",
        [
            "My book arrived damaged. Order BK-10105, email sarah.lee@email.com.",
            "Yes, please go ahead with the refund.",
        ],
    )

    # 4. Ineligible return — tool rejects it; business logic lives in code not prompt
    scenario(
        "Ineligible return: outside 30-day window",
        ["Can I return order BK-8823? My email is alex.j@email.com."],
    )

    # 5. Ineligible return — order still processing, suggest cancellation instead
    scenario(
        "Ineligible return: order not yet shipped",
        ["I want to return BK-9871. Email is john.smith@email.com."],
    )

    # 6. Policy lookup — no order data needed
    scenario(
        "Policy question: shipping times",
        ["How long does standard shipping take?"],
    )

    # 7. Out of scope — tool escalates with structured reason + summary
    scenario(
        "Escalation: suspected account compromise",
        ["I think someone hacked my account and placed orders without my permission."],
    )
