# MCP Studio Tasks — Advanced MCP Features & 2026 Stateless Workbench

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/downloads/)
[![MCP Spec](https://img.shields.io/badge/MCP-2026--07--28_Stateless_%7C_Tasks-0284c7)](https://modelcontextprotocol.io/)
[![SDD](https://img.shields.io/badge/Methodology-Spec--Driven_Development-0f766e)](./SPECIFICATION.md)

A self-contained, runnable reference implementation for **Model Context Protocol: Advanced Features**, aligned with the stateless `2026-07-28` MCP specification (`SEP-2577`, Multi Round-Trip `input-required` Elicitation, durable Tasks v2, context-aware Completions, Multimodal outputs, and an end-to-end Chat Agent UI).

---

## Capabilities Implemented

| Feature Area | Implemented Behavior | Key Module |
| :--- | :--- | :--- |
| **Stateless Core vs. Legacy Sessions** | Stateless `POST /mcp` (per-request `_meta`, round-robin worker scaling) contrasted against sticky `POST /mcp/legacy` (`Mcp-Session-Id`) demonstrating replica failure on non-sticky load balancers | [`src/mcp_studio_tasks/server.py`](./src/mcp_studio_tasks/server.py) |
| **Server Handles & Opaque State** | SQLite cart/workflow handles paired with owner authorization (`handle ≠ auth`) + HMAC-SHA256 signed opaque client state tokens rejecting tampering | [`src/mcp_studio_tasks/state.py`](./src/mcp_studio_tasks/state.py) |
| **Form & URL Elicitation (`input-required`)** | Multi-round-trip `input_required` tool flow with strict JSON Schema (`accept`/`decline`/`cancel`), URL-mode elicitation for secrets/payments, and graceful fallback when clients lack elicitation capability | [`src/mcp_studio_tasks/elicitation.py`](./src/mcp_studio_tasks/elicitation.py) |
| **Durable Tasks Extension & Notifications** | SQLite-persisted Tasks (`tasks/get`, `tasks/update`, `tasks/cancel`) surviving server restarts, mid-task `input_required` pauses, SSE `notifications/progress`, and dynamic `tools/list_changed` / `resources/list_changed` | [`src/mcp_studio_tasks/tasks.py`](./src/mcp_studio_tasks/tasks.py) |
| **Completions & All 6 Content Types** | Context-aware `completion/complete` (backed by strict server validation) plus tools emitting `text`, `structuredContent`, PNG `image`, WAV `audio`, `resource_link`, and embedded `resource` with token-cost metrics | [`src/mcp_studio_tasks/content.py`](./src/mcp_studio_tasks/content.py) |
| **End-to-End Chat Agent & Console UI** | SDK-style async chat client handling tool selection, interactive elicitation prompts, background task polling, live SSE progress, and inline media/link rendering in a Light/Dark UI | [`src/mcp_studio_tasks/agent_client.py`](./src/mcp_studio_tasks/agent_client.py) |

---

## Quickstart

```bash
cd mcp-studio-tasks
uv sync --extra dev
uv run uvicorn mcp_studio_tasks.main:app --reload --port 8012
```

Open **`http://127.0.0.1:8012`** for the interactive Chat & Studio Workbench, or run the full automated test suite:

```bash
uv run pytest -v
```

---

## Documentation

- [`SPECIFICATION.md`](./SPECIFICATION.md) — Constitution invariants (`INV-ADV-*`) and traceable requirements (`REQ-ADV-*`)
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — Protocol sequence diagram and module responsibilities
- [`docs/SPEC_MIGRATION_2026.md`](./docs/SPEC_MIGRATION_2026.md) — Why `Mcp-Session-Id`, `sampling`, and `roots` (`SEP-2577`) shifted to stateless `_meta`, `input-required`, and Tasks v2
