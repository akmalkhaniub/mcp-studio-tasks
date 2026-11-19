# Architecture & Protocol Sequence (`mcp-studio-tasks`)

## Stateless Runtime + Durable Tasks + Elicitation Flow

```mermaid
sequenceDiagram
    participant UI as Chat UI / Agent Client
    participant LB as Any Stateless Replica (/mcp)
    participant DB as SQLite Task & Cart Store

    UI->>LB: 1. tools/list (_meta.protocolVersion=2026-07-28)
    LB-->>UI: Tools list (respecting session tool enable/disable toggles)

    UI->>LB: 2. tools/call publish_media_brief (missing fields)
    LB-->>UI: status="input_required" + requestedSchema (form/url)
    UI->>UI: Prompt user (accept / decline / cancel)
    UI->>LB: 3. tools/call publish_media_brief + elicitation reply
    LB-->>UI: status="published" (structuredContent)

    UI->>LB: 4. tools/call render_briefing_video (capabilities.tasks=true)
    LB->>DB: Persist Task row (working / input_required)
    LB-->>UI: Task handle (taskId)

    loop Poll / Stream Progress
        UI->>LB: tasks/get (or GET /mcp/events SSE)
        LB->>DB: Read latest durable progress/status
        LB-->>UI: status="working" / "completed" + structuredContent
    end
```

## Module Responsibilities

- [`state.py`](../src/mcp_studio_tasks/state.py): Durable server-side `cart_id` handles enforcing caller `owner_sub` verification alongside HMAC-SHA256 signed client-carried cursor handles.
- [`elicitation.py`](../src/mcp_studio_tasks/elicitation.py): Multi-round-trip `input_required` form and URL elicitation flows, blocking credential/payment collection in form fields (`INV-ADV-04`) and falling back cleanly when clients omit elicitation capabilities.
- [`tasks.py`](../src/mcp_studio_tasks/tasks.py): Restart-resilient SQLite `TaskManager` implementing `tasks/get`, `tasks/update`, `tasks/cancel`, and SSE `notifications/progress` / `list_changed` events.
- [`content.py`](../src/mcp_studio_tasks/content.py): Context-aware `completion/complete`, stdlib PNG/WAV multimodal generators, `resource_link` vs. embedded resources, and token-cost savings estimation.
- [`agent_client.py`](../src/mcp_studio_tasks/agent_client.py): End-to-end async chat client automating tool selection, elicitation retries, and background task polling.
