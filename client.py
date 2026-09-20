"""
Raw HTTP wrapper around the Anthropic Messages API.
No SDK — one dependency: requests.

Retry policy: up to MAX_HTTP_RETRIES retries on transient failures (429, 5xx,
ConnectionError, Timeout) with exponential backoff (1 s, 2 s).  Non-retryable
errors (400, 401, 422) raise immediately.  Errors are sanitised before raising
— raw API bodies and credentials are never exposed.
"""
import json
import time
from typing import Callable

import requests

from config import settings

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

NON_RETRYABLE_STATUS_CODES = {400, 401, 422}
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 529}


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
        """Append user_message to history, run the tool loop, return final text.

        on_tool_call(tool_name, tool_input) -> tool_result_string
        Called once per tool invocation; side-effects (logging, DB writes) go here.
        """
        self.history.append({"role": "user", "content": user_message})

        for _ in range(self._max_steps):
            response = self._post(tools)
            stop_reason = response["stop_reason"]
            content = response["content"]

            if stop_reason == "tool_use":
                self.history.append({"role": "assistant", "content": content})

                tool_results = []
                for block in content:
                    if block["type"] != "tool_use":
                        continue
                    try:
                        result_content = on_tool_call(block["name"], block["input"])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": result_content,
                        })
                    except Exception:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": json.dumps({"error": "Tool execution failed."}),
                            "is_error": True,
                        })

                self.history.append({"role": "user", "content": tool_results})

            elif stop_reason == "end_turn":
                text = next(
                    (b["text"] for b in content if b.get("type") == "text"), ""
                )
                self.history.append({"role": "assistant", "content": content})
                return text

            elif stop_reason == "max_tokens":
                self.history.append({"role": "assistant", "content": content})
                return (
                    "I need more space to finish my response. "
                    "Let me connect you with a human agent."
                )

            elif stop_reason == "refusal":
                text = next(
                    (b["text"] for b in content if b.get("type") == "text"),
                    "I'm unable to help with that request.",
                )
                self.history.append({"role": "assistant", "content": content})
                return text

            else:
                # Unknown stop reason — safe handoff
                self.history.append({"role": "assistant", "content": content})
                return (
                    "Something unexpected happened. "
                    "Let me connect you with a human agent who can help."
                )

        # Tool loop limit reached
        return (
            "I wasn't able to resolve this within the allowed steps. "
            "Let me connect you with a human agent."
        )

    def _post(self, tools: list[dict]) -> dict:
        """POST to the Anthropic Messages API with bounded retry and backoff."""
        last_error: Exception | None = None

        for attempt in range(settings.MAX_HTTP_RETRIES + 1):
            try:
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

                if response.status_code in NON_RETRYABLE_STATUS_CODES:
                    raise RuntimeError(
                        f"Request could not be completed (status {response.status_code})."
                    )

                if response.ok:
                    return response.json()

                # Non-OK, potentially transient
                if response.status_code in TRANSIENT_STATUS_CODES and attempt < settings.MAX_HTTP_RETRIES:
                    delay = self._backoff(attempt, response.headers.get("Retry-After"))
                    time.sleep(delay)
                    continue

                raise RuntimeError("API request failed. Please try again later.")

            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                if attempt < settings.MAX_HTTP_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                # Final attempt exhausted
                break

        raise RuntimeError(
            "Service temporarily unavailable. Please try again later."
        ) from last_error

    @staticmethod
    def _backoff(attempt: int, retry_after_header: str | None) -> float:
        """Return delay in seconds, respecting Retry-After when present."""
        if retry_after_header:
            try:
                return min(float(retry_after_header), 30.0)
            except ValueError:
                pass
        return float(2 ** attempt)  # 1 s, 2 s
