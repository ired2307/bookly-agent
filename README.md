# Bookly AI Concierge

**Aria** is a conversational customer support agent for Bookly, a fictional online bookstore. It handles order tracking, refunds, and policy questions via natural language — in a browser chat UI and a command-line REPL.

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

**Automated scenarios** (no interactive input)
```bash
python test_scenarios.py      # 7 scripted flows, printed turn-by-turn
```

**Offline tests** (no API key required)
```bash
pytest test_tools.py -v       # 29 unit tests, ~0.1 s
```

---

## Architecture

The agent is structured in four layers, matching the pattern used in production enterprise support systems:

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
│      stop_reason == tool_use → execute → append → POST   │
│      stop_reason == end_turn → return text               │
│  Multi-turn history · MAX_STEPS ceiling (default 10)     │
└────────────────────────┬─────────────────────────────────┘
                         │  tool calls (JSON schema)
┌────────────────────────▼─────────────────────────────────┐
│  TOOL LAYER                                              │
│  lookup_order · record_confirmation · initiate_refund    │
│  search_policies · escalate_to_human                     │
│  Business rules enforced in Python — not in the prompt   │
└────────────────────────┬─────────────────────────────────┘
                         │  reads / writes
┌────────────────────────▼─────────────────────────────────┐
│  DATA LAYER                                              │
│  ORDERS · POLICIES · REFUNDS_INITIATED · CONFIRMATIONS   │
└──────────────────────────────────────────────────────────┘
```

**Cross-cutting controls** — applied at every layer:

| Concern | Implementation |
|---|---|
| Identity | Order ID + matching email required for all read and write operations |
| Safety | Business rules in Python `if` statements — model cannot override |
| Consent gate | `record_confirmation` must be called before `initiate_refund` will execute |
| Idempotency | `sha256(order_id:email)[:16]` key — duplicates rejected without model involvement |
| Human handoff | `escalate_to_human` tool for fraud, legal, account issues, or explicit customer request |

---

## The agentic loop (`client.py`)

No framework. The loop is six lines of logic:

```python
for _ in range(self._max_steps):
    response = self._post(tools)          # POST to api.anthropic.com/v1/messages
    if response["stop_reason"] == "tool_use":
        # batch all tool calls from this turn into one user message
        tool_results = [on_tool_call(b["name"], b["input"]) for b in content if b["type"] == "tool_use"]
        self.history.append({"role": "user", "content": tool_results})
    else:
        return extract_text(response)     # end_turn — return to caller
```

Each iteration appends the model's tool call and the tool result to `self.history` before re-posting. The full conversation grows with each step; `MAX_STEPS` is the ceiling.

---

## Why controls live in tools, not the system prompt

The system prompt tells the agent *what to do*. The tools enforce *whether it can*.

A prompt instruction can be overridden by a persuasive message, misread on an edge case, or silently broken by a model update. A Python `if` statement cannot.

Every refund passes four sequential checks before execution:

```python
if not order or order["customer_email"].lower() != email:   # 1. identity
    return error("Order not found or email mismatch")
if key in REFUNDS_INITIATED:                                 # 2. idempotency
    return error("Refund already submitted")
if not order.get("eligible_for_return"):                     # 3. eligibility
    return error(order["ineligible_reason"])
if CONFIRMATIONS_RECORDED.get(key) != email:                 # 4. consent
    return error("Customer confirmation not recorded")
```

Behaviour is deterministic, independently testable, and auditable regardless of what the model says.

---

## Tools

| Tool | Purpose | Authority level |
|---|---|---|
| `lookup_order` | Retrieve order details | Read — requires order ID + matching email |
| `record_confirmation` | Record customer consent | Write-gate — must precede any refund |
| `initiate_refund` | Submit a refund | Write — passes all four checks |
| `search_policies` | Retrieve policy text | Read — keyword-matched, no identity required |
| `escalate_to_human` | Structured handoff | Terminal — returns `reason` + `summary` fields |

**Progressive authority:** read actions require identity verification. Write actions additionally require eligibility, a recorded consent, and an idempotency check. Risk is proportional to authority granted.

---

## Test orders

| Order ID | Email | Status | Refund eligible |
|---|---|---|---|
| BK-10042 | jane.doe@email.com | Shipped | Yes |
| BK-9871 | john.smith@email.com | Processing | No — cancel instead |
| BK-8823 | alex.j@email.com | Delivered | No — outside 30-day window |
| BK-10105 | sarah.lee@email.com | Delivered | Yes |

---

## Demo scenarios (`test_scenarios.py`)

Seven scripted flows — no interactive input, printed turn-by-turn:

1. **Vague request** — agent asks for order ID and email before acting
2. **Multi-turn lookup** — customer provides ID and email across separate messages
3. **Refund flow (damaged item)** — full path: lookup → show details → confirm → `record_confirmation` → `initiate_refund`
4. **Ineligible (30-day window)** — tool rejects with specific reason; agent relays it clearly
5. **Ineligible (order processing)** — tool suggests cancellation as the next best option
6. **Policy question** — `search_policies` with no order data needed
7. **Escalation** — structured handoff with reason and conversation summary

---

## File structure

```
bookly-agent/
├── agent.py            # SupportAgent — system prompt, tool dispatch
├── client.py           # ConversationClient — raw HTTP + agentic loop
├── tools.py            # Tool schemas (sent to model) + implementations
├── data.py             # Mock orders, policy documents, in-memory write logs
├── config.py           # Settings loaded from .env
├── server.py           # FastAPI server — /chat, /session, static UI
├── cli.py              # Interactive REPL
├── test_scenarios.py   # 7 automated demo scenarios
├── test_tools.py       # 29 offline unit tests
├── conftest.py         # pytest path setup + state-reset fixture
├── requirements.txt
├── .env.example
└── static/
    └── index.html      # Browser chat UI (dark theme, tool call chips)
```

---

## What this deliberately does not include

| Gap | What production requires |
|---|---|
| **Authentication** | Identity is verified by order ID + email match. Production receives a verified session token from the channel layer — the agent should not be the first line of identity verification |
| **Persistent sessions** | Conversation history is in-memory. Production requires a durable store (Redis, DB-backed) so sessions survive restarts and are isolated per customer |
| **Evaluation pipeline** | 29 unit tests cover tool correctness, not response quality. Production requires deterministic tool-call graders, LLM-as-judge scoring, and regression detection on every prompt or model change |
| **Observability** | No structured logging, no distributed tracing, no token cost tracking. You cannot operate this safely at scale without knowing when it is slow, expensive, or wrong |
| **Resilience** | No tool timeouts, no retry with backoff, no circuit breakers. `MAX_STEPS` is a last-resort ceiling, not a graceful degradation strategy |
