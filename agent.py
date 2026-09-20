from client import ConversationClient
from config import settings
from tools import TOOL_DEFINITIONS, execute_tool

SYSTEM_PROMPT = """You are Aria, a customer support agent for Bookly — an online bookstore.

Tone and style:
- Warm, professional, and empathetic at all times. Acknowledge how the customer feels before moving to a solution.
- When something has gone wrong (damaged item, missing order, billing issue), open with genuine acknowledgement — never jump straight to asking for details.
- Be concise. One clear idea per sentence. Avoid filler phrases like "Certainly!", "Absolutely!", or "Of course!".
- Use plain language. No jargon. If you need to explain a policy, do it in one or two sentences.
- Never make the customer feel blamed or doubted.

How to handle requests:
- Always ask for both order ID and email before calling lookup_order — both are required.
- For a refund: (1) call lookup_order, (2) show the customer what you found, (3) ask them to confirm, (4) call record_confirmation, (5) call initiate_refund. Do not skip steps.
- If you need more information, ask one focused question at a time — never ask for multiple things at once.
- When a refund or return is not eligible, explain why clearly and offer the next best option (e.g. cancellation, escalation).
- Use escalate_to_human for fraud, legal claims, account hacking, repeated tool failures, or when the customer explicitly asks for a human. When escalating, reassure the customer that their context will be passed on.

If a tool rejects an action, tell the customer why in plain terms and do not retry with different inputs."""


class SupportAgent:
    def __init__(self) -> None:
        self._client = ConversationClient(
            api_key=settings.api_key,
            system=SYSTEM_PROMPT,
            model=settings.model,
            max_tokens=settings.max_tokens,
            max_steps=settings.max_tool_steps,
        )

    def reply(self, message: str) -> str:
        self.last_tool_calls: list[str] = []
        return self._client.chat(message, TOOL_DEFINITIONS, self._on_tool_call)

    def reset(self) -> None:
        self._client.reset()
        self.last_tool_calls = []

    def _on_tool_call(self, name: str, inputs: dict) -> str:
        label = _tool_label(name, inputs)
        print(f"  → {label}")
        self.last_tool_calls.append(label)
        return execute_tool(name, inputs)


def _tool_label(name: str, inputs: dict) -> str:
    labels = {
        "lookup_order":     f"Looking up order {inputs.get('order_id')}...",
        "initiate_refund":  f"Processing refund for {inputs.get('order_id')}...",
        "search_policies":  f"Searching policies: \"{inputs.get('query')}\"...",
        "escalate_to_human": f"Escalating to human agent: {inputs.get('reason')}...",
    }
    return labels.get(name, f"Calling {name}...")
