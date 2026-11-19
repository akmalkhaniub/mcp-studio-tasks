# Protocol Evolution Guide: `2024-11-05` Stateful Sessions → `2026-07-28` Stateless MCP (`SEP-2577`)

This document summarizes the exact protocol transition demonstrated side-by-side inside `mcp-studio-tasks`.

---

## 1. Why Server State Changed on HTTP

| Aspect | Legacy Model (`2024-11-05`) | Modern Stateless Core (`2026-07-28`) |
| :--- | :--- | :--- |
| **Session Startup** | Requires `initialize` → `notifications/initialized` + sticky `Mcp-Session-Id` header | No connection handshake required; protocol version & capabilities ride per-request in `params._meta` |
| **Load Balancers / Serverless** | Requires sticky sessions or shared distributed session cache | Any replica handles any request safely (`replica=1` → `replica=2` seamlessly) |
| **Workflow Continuity** | In-memory connection state | Server-returned durable **handle** (authorized via caller identity) or **HMAC-signed opaque client state** |

---

## 2. Why `sampling` and `roots` Were Deprecated (`SEP-2577`)

In earlier MCP drafts, servers could issue **server-initiated requests** back to the client (`sampling/createMessage`, `roots/list`, and server-driven callbacks mid-execution):
1. **Backchannel Fragility:** Server-to-client RPC requires persistent bidirectional streams or long-held SSE connections that break easily behind API gateways, serverless functions, and horizontal autoscalers.
2. **Low / Inconsistent Client Support:** Hosts rarely granted servers unrestricted sampling budgets or filesystem root discovery reliably.
3. **Replacement Pattern:**
   - **Roots** → Replaced by explicit tool arguments (`workspace_uri`, `target_scope`) and `resources`.
   - **Sampling** → Replaced by server-side LLM calls where needed, or multi-round-trip **`input-required`** results + **Tasks** when user confirmation/input is needed cleanly without holding an open RPC callback.

---

## 3. Handling Long-Running Work & Human Input Without Blocking

- **Multi-Round-Trip Elicitation (`input-required`):** A tool call immediately returns an `input_required` result containing a form schema (or safe out-of-band `url`). The client collects user input (`accept`, `decline`, or `cancel`) and retries the call cleanly over standard stateless HTTP.
- **Tasks Extension (`tasks/get`, `tasks/update`, `tasks/cancel`):** For jobs lasting seconds to hours, the tool returns a persistent **Task handle** stored in SQLite, letting clients poll or subscribe to `notifications/progress` across server restarts.
