# Specification: `mcp-studio-tasks` (4-Layer SDD Contract)

---

## Layer 1 — Architectural Constitution (Non-Negotiable Invariants)

1. **`INV-ADV-01` (Stateless Horizontal Scalability):** Core `POST /mcp` execution MUST succeed across fresh server instances (`replica=1` vs. `replica=2`) with zero affinity to in-memory session state.
2. **`INV-ADV-02` (Handle ≠ Auth):** Presenting a valid server-returned cart/workflow/task handle MUST still verify caller ownership (`owner_sub`); possessing an ID alone MUST NEVER bypass authorization.
3. **`INV-ADV-03` (Untrusted Round-Trip State):** Client-carried opaque state MUST be HMAC-authenticated and validated through strict Pydantic schemas (`extra="forbid"`) before use.
4. **`INV-ADV-04` (Secrets Never Enter Form Elicitation):** Passwords, API keys, and payment details MUST require URL-mode elicitation (`mode="url"`) and MUST be rejected from form schemas.
5. **`INV-ADV-05` (Durable Tasks Survive Restarts):** Active and paused (`input_required`) task states MUST persist in SQLite so polling/resumption works across full server process restarts.

---

## Layer 2 — Requirement IDs (`REQ-ADV-*`)

| ID | Requirement | Target Module | Verification Test(s) |
| :--- | :--- | :--- | :--- |
| **`REQ-ADV-01`** | Stateless `2026-07-28` multi-replica execution vs. legacy `Mcp-Session-Id` affinity failure, signed opaque client state, and handle ownership enforcement | `server.py`, `state.py` | `tests/test_stateless_and_handles.py` |
| **`REQ-ADV-02`** | Form elicitation (`input_required`, `accept`/`decline`/`cancel`), URL elicitation for sensitive credentials/billing, and graceful capability fallback | `elicitation.py`, `server.py` | `tests/test_elicitation.py` |
| **`REQ-ADV-03`** | Tasks extension negotiation, `tasks/get`, `tasks/update`, `tasks/cancel`, SQLite persistence across restarts, `notifications/progress`, and `list_changed` + per-session tool disabling | `tasks.py`, `server.py` | `tests/test_tasks_durable_and_notifications.py` |
| **`REQ-ADV-04`** | `completion/complete` context-aware filtering (without skipping server validation) plus `text`, `structuredContent`, `image`, `audio`, `resource_link`, and embedded `resource` outputs | `content.py`, `server.py` | `tests/test_completions_and_content.py` |
| **`REQ-ADV-05`** | End-to-end chat agent (`McpChatAgent`) executing LLM-style tool choice, elicitation retry loop, async task polling, progress surfacing, and error handling | `agent_client.py`, `main.py` | `tests/test_chat_agent_e2e.py` |
