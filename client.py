"""
Raw HTTP wrapper around the Anthropic Messages API.
No SDK — one dependency: requests.
"""
from typing import Callable
import requests

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class ConversationClient:
    """Manages conversation history and the tool-use agentic loop."""

    def __init__(
        self,
        api_key: str,
        system: str,
        model: str,
        max_tokens: int,
        max_steps: int = 10,
    ) -> None:
        self._api_key = api_key
        self._system = system
        self._model = model
        self._max_tokens = max_tokens
        self._max_steps = max_steps
        self.history: list[dict] = []

    def reset(self) -> None:
        self.history.clear()

    def chat(
        self,
        user_message: str,
        tools: list[dict],
        on_tool_call: Callable[[str, dict], str],
    ) -> str:
        """
        Append user_message to history, run the tool loop, return final text.

        on_tool_call(tool_name, tool_input) -> tool_result_string
        Called once per tool invocation; side-effects (logging, DB writes) go here.
        """
        self.history.append({"role": "user", "content": user_message})

        for _ in range(self._max_steps):
            response = self._post(tools)
            stop_reason = response["stop_reason"]
            content = response["content"]  # list of content blocks

            if stop_reason == "tool_use":
                self.history.append({"role": "assistant", "content": content})

                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": on_tool_call(block["name"], block["input"]),
                    }
                    for block in content
                    if block["type"] == "tool_use"
                ]
                self.history.append({"role": "user", "content": tool_results})

            else:  # end_turn
                text = next(
                    (b["text"] for b in content if b.get("type") == "text"), ""
                )
                self.history.append({"role": "assistant", "content": content})
                return text

        return "I'm unable to resolve this right now. Let me connect you with a human agent."

    def _post(self, tools: list[dict]) -> dict:
        response = requests.post(
            API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": self._system,
                "tools": tools,
                "messages": self.history,
            },
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(
                f"Anthropic API error {response.status_code}: {response.text}"
            )
        return response.json()
