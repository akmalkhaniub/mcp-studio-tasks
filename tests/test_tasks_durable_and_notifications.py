"""Tests for durable Tasks surviving server restarts, input_required, cancel, progress & list_changed (`REQ-ADV-03`)."""

from __future__ import annotations

import asyncio
import httpx
import pytest

from mcp_studio_tasks.main import create_app


@pytest.mark.asyncio
async def test_durable_task_survives_restart_and_supports_input_required_update_and_cancel(tmp_path) -> None:
    db_path = str(tmp_path / "durable_tasks.db")
    caps_tasks = {"protocolVersion": "2026-07-28", "capabilities": {"tasks": True}}

    # Instance 1 creates a paused task requiring mid-task user quality input
    app_before_restart = create_app(db_path=db_path, instance_id="replica-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_before_restart), base_url="http://test") as client1:
        created = await client1.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "render_briefing_video",
                    "arguments": {"title": "Q4 Launch", "pause_for_input": True},
                    "_meta": caps_tasks,
                },
            },
        )
        task_id = created.json()["result"]["task"]["taskId"]
        assert created.json()["result"]["task"]["status"] == "input_required"

    # Simulate full server restart with fresh app pointing at the same SQLite DB
    app_after_restart = create_app(db_path=db_path, instance_id="replica-2")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_after_restart), base_url="http://test") as client2:
        polled = await client2.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"taskId": task_id}})
        assert polled.json()["result"]["task"]["status"] == "input_required"

        resumed = await client2.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tasks/update",
                "params": {"taskId": task_id, "action": "accept", "data": {"quality": "4k"}},
            },
        )
        assert resumed.json()["result"]["task"]["status"] == "completed"
        assert resumed.json()["result"]["task"]["result"]["structuredContent"]["quality"] == "4k"


@pytest.mark.asyncio
async def test_async_task_progress_cancellation_and_list_changed_notifications(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "tasks.db"))
    caps_tasks = {"protocolVersion": "2026-07-28", "capabilities": {"tasks": True}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Async task completion emits notifications/progress
        start = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "render_briefing_video",
                    "arguments": {"title": "SRE Summary", "step_delay_sec": 0.01},
                    "_meta": caps_tasks,
                },
            },
        )
        tid = start.json()["result"]["task"]["taskId"]
        await asyncio.sleep(0.1)
        done = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"taskId": tid}})
        assert done.json()["result"]["task"]["status"] == "completed"

        # 2. Cancel slows down a long task cleanly
        slow = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "render_briefing_video",
                    "arguments": {"title": "Long Render", "step_delay_sec": 0.5},
                    "_meta": caps_tasks,
                },
            },
        )
        slow_id = slow.json()["result"]["task"]["taskId"]
        cancelled = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tasks/cancel", "params": {"taskId": slow_id}})
        assert cancelled.json()["result"]["task"]["status"] == "cancelled"

        # 3. Disable tool for session triggers notifications/tools/list_changed and removes tool from tools/list
        toggle = await client.post(
            "/admin/toggle-session-tool",
            params={"session_scope": "session-alpha", "tool_name": "admin_export_bundle", "disabled": True},
        )
        assert toggle.json()["notified"] == "notifications/tools/list_changed"

        listed = await client.post(
            "/mcp",
            headers={"X-Client-Scope": "session-alpha"},
            json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        )
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert "admin_export_bundle" not in names

        notes = (await client.get("/mcp/notifications")).json()
        methods = {n["method"] for n in notes}
        assert "notifications/progress" in methods
        assert "notifications/tools/list_changed" in methods
