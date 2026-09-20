# Bookly AI Concierge

**Aria** is a conversational customer support agent for Bookly, a fictional online bookstore. It handles order tracking, refund preparation, and policy questions via natural language — in a browser chat UI and a command-line REPL.

> Built with **direct HTTP calls to the Anthropic Messages API** — no SDK, no agent framework.
> Every model interaction is a raw `requests.post()` to `api.anthropic.com/v1/messages`.

---

## Quick start

**CLI**
```bash
git clone <repo-url> && cd bookly-agent
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY
python cli.py
```

**Browser UI**
```bash
python -m uvicorn server:app --reload
# open http://127.0.0.1:8000
```

**Offline tests** (no API key required)
```bash
pytest -v
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  CHANNEL LAYER                                           │
│  Browser UI  (FastAPI + static HTML, session-per-tab)    │
│  CLI REPL    (single-session interactive terminal)       │
└────────────────────────┬─────────────────────────────────┘
                         │  user message
┌────────────────────────▼─────────────────────────────────┐
│  ORCHESTRATION LAYER                                     │
│  SupportAgent  — system prompt, tool dispatch, logging   │
│  ConversationClient  — raw HTTP agentic loop             │
│    POST /v1/messages                                     │
│      stop_reason == tool_use  → execute → append → POST  │
│      stop_reason == end_turn  → return text              │
│      stop_reason == max_tokens→ safe fallback            │
│      stop_reason == refusal   → surface safely           │
│  Multi-turn history · MAX_STEPS ceiling (10)             │
│  Bounded HTTP retries (MAX_HTTP_RETRIES=2, 1s/2s backoff)│
└────────────────────────┬─────────────────────────────────┘
                         │  tool calls (JSON schema — no ctx fields)
┌────────────────────────▼─────────────────────────────────┐
│  TOOL LAYER                                              │
│  lookup_order · prepare_refund · search_policies         │
│  escalate_to_human                                       │
│  initiate_refund  (application-only — not model-callable)│
│  Business rules enforced in Python — not in the prompt   │
│  ctx injected by orchestration layer, never by LLM       │
└────────────────────────┬─────────────────────────────────┘
                         │  reads / writes
┌────────────────────────▼─────────────────────────────────┐
│  DATA LAYER                                              │
│  ORDERS · POLICIES · CUSTOMERS                           │
│  REFUNDS_INITIATED · PENDING_REFUNDS                     │
└──────────────────────────────────────────────────────────┘
```

---

## Key design decisions

### Direct Anthropic HTTP integration

There is no Anthropic SDK. Every request is:

```python
requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={"x-api-key": ..., "anthropic-version": "2023-06-01"},
    json={"model": ..., "messages": history, "tools": tool_schemas, ...},
)
```

This keeps the dependency surface minimal and makes the loop transparent.

### Bounded tool loop (MAX_STEPS = 10)

The agentic loop has a hard ceiling: if the model has not produced a final `end_turn` response after 10 tool calls, the loop exits and returns a safe human-handoff message. This prevents runaway loops from runaway model behaviour.

The HTTP retry budget is separate: up to `MAX_HTTP_RETRIES = 2` retries per API call on transient failures (429, 5xx, ConnectionError, Timeout), with 1 s / 2 s exponential backoff. Non-retryable errors (400, 401, 422) raise immediately without retry.

### Clarification before tool calls

The system prompt instructs Aria to ask for both order ID and email in a single focused question before calling `lookup_order`. The model does not call any tool until it has both pieces of information.

### Guest order matching (order ID + email)

`lookup_order` requires both an order ID and a matching email address. The same generic error message is returned for an unknown order ID and for a known order ID with the wrong email — this prevents enumeration attacks that probe whether a given order ID exists.

### Authenticated refund authority (server-controlled CustomerContext)

Every tool function accepts a `CustomerContext` that is injected by the orchestration layer, not by the model. The `ctx` fields (`session_id`, `authenticated`, `customer_id`) are absent from all tool schemas sent to the model — the model cannot forge or escalate authority.

`prepare_refund` rejects calls when `ctx.authenticated` is False. The `CUSTOMERS` dict maps customer IDs to their order IDs; ownership is checked server-side, not by inspecting what the model passes.

### Application-controlled confirmation (not a model tool)

In the old design, the model called `record_confirmation` to "prove consent" before a refund. A model-callable tool cannot prove consent — the model can call it without any real customer input.

The new design:

1. Model calls `prepare_refund` → a short-lived confirmation token is stored in `PENDING_REFUNDS`.
2. Model presents the refund details to the customer and asks them to confirm.
3. Customer confirms via the `POST /confirm/{session_id}` endpoint.
4. The **application** (not the model) calls `initiate_refund` with the stored token.

The confirmation token is never sent to the model or returned in any tool response. It is application-controlled end-to-end.

### True idempotency (stores result, returns original on retry)

On success, `initiate_refund` stores the full result dict in `REFUNDS_INITIATED[order_id]`. Any subsequent call for the same order returns the original result with `status: "already_initiated"`. This makes retries safe without double-refunding.

### Tool and provider error handling

- All tool implementations are wrapped in `try/except`; exceptions return a safe JSON error — no stack traces or internal details are exposed.
- `execute_tool` validates tool names and required fields before dispatch.
- When `on_tool_call` raises an exception in the client loop, the tool result block is sent with `is_error: true` so the model can handle it gracefully.
- Errors from the Anthropic API are sanitised before raising — raw response bodies and credentials are never propagated.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Browser chat UI |
| `POST` | `/session` | Create a new session (returns `session_id`) |
| `POST` | `/chat` | Send a message (requires valid `session_id`) |
| `POST` | `/confirm/{session_id}` | Confirm pending refund (application-controlled) |
| `DELETE` | `/session/{session_id}` | Reset / end a session |

Clients must call `POST /session` before `POST /chat`. Arbitrary client-provided session IDs are not accepted.

---

## Test orders

| Order ID | Email | Status | Refund eligible | Customer |
|---|---|---|---|---|
| BK-10042 | jane.doe@email.com | Shipped | Yes | CUST-001 |
| BK-9871 | john.smith@email.com | Processing | No — cancel instead | CUST-002 |
| BK-8823 | alex.j@email.com | Delivered | No — outside 30-day window | CUST-003 |
| BK-10105 | sarah.lee@email.com | Delivered | Yes | CUST-004 |

---

## Tests

```bash
pytest -v            # runs unit tests + trajectory evaluations
pytest test_tools.py # tool layer only (~45 tests)
pytest evals/        # journey evaluations only
```

All tests run offline with no API key. The HTTP layer is mocked via `unittest.mock.patch("requests.post")`.

**Coverage:**
- Tool business logic (lookup, prepare, initiate, search, escalate)
- Enumeration prevention (identical errors for unknown vs mismatched)
- Guest/auth authority enforcement
- Confirmation token lifecycle (missing, expired, wrong session, consumed)
- True idempotency (returns original result on retry)
- Client loop: end_turn, tool_use, max_tokens, refusal, unknown stop reason
- Transient HTTP retry (retried) vs permanent failure (not retried) vs retry limit
- Tool error bubbling with `is_error: true`
- Two full trajectory evaluations with mocked responses

---

## File structure

```
bookly-agent/
├── agent.py            # SupportAgent — system prompt, tool dispatch
├── client.py           # ConversationClient — raw HTTP + agentic loop
├── context.py          # CustomerContext dataclass
├── tools.py            # Tool schemas (sent to model) + implementations
├── data.py             # Mock orders, policies, customers, in-memory logs
├── config.py           # Settings loaded from .env
├── server.py           # FastAPI server — /session, /chat, /confirm, static UI
├── cli.py              # Interactive REPL
├── test_tools.py       # ~45 offline unit tests
├── conftest.py         # pytest path setup + state-reset fixture
├── evals/
│   ├── __init__.py
│   └── test_journeys.py  # 5 trajectory evaluations
├── pyproject.toml      # ruff + pytest config
├── .github/
│   └── workflows/
│       └── ci.yml      # GitHub Actions CI
├── requirements.txt
├── LICENSE
└── static/
    └── index.html      # Browser chat UI (dark theme, tool call chips)
```

---

## Prototype limitations

| Limitation | Production requirement |
|---|---|
| **In-memory sessions** | Conversation history is stored in-memory and lost on restart. Production requires a durable store (database, Redis) |
| **In-memory data** | ORDERS, REFUNDS_INITIATED, PENDING_REFUNDS are Python dicts. Production requires persistent storage with ACID guarantees |
| **No real authentication** | The CLI sets `authenticated=True` for demo. Production receives a verified session token from the identity layer |
| **Single-process** | `_sessions` and `_contexts` dicts are not shared across workers. Production requires a distributed session store |
| **No token expiry sweep** | Expired PENDING_REFUNDS entries accumulate in memory. Production requires a background sweep or TTL store |

## Production extension points

- Replace `CustomerContext(authenticated=False)` with a real JWT/session validation step in `create_session`.
- Replace ORDERS with a database query; replace REFUNDS_INITIATED / PENDING_REFUNDS with transactional DB rows.
- Add structured logging (token counts, latencies, tool call outcomes) for observability.
- Add an evaluation pipeline: deterministic tool-call graders + LLM-as-judge scoring on every prompt or model change.
- Add circuit-breaker logic around `_post` for graceful degradation under sustained API failures.
