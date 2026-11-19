"""Tests for task ownership enforcement, clean JSON-RPC error codes, and opaque handle TTL."""

from __future__ import annotations

import time

import httpx
import pytest

from mcp_studio_tasks.main import create_app
from mcp_studio_tasks.state import OpaquePaginationState, SignedOpaqueStateManager


@pytest.mark.asyncio
async def test_task_handle_ownership_and_jsonrpc_error_codes(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "studio.db"))
    caps_tasks = {"protocolVersion": "2026-07-28", "capabilities": {"tasks": True}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/mcp",
            headers={"X-Caller-Sub": "alice@studio.local"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "render_briefing_video",
                    "arguments": {"title": "Confidential Launch", "pause_for_input": True},
                    "_meta": caps_tasks,
                },
            },
        )
        task_id = created.json()["result"]["task"]["taskId"]

        # Bob cannot inspect, update, or cancel Alice's task handle
        for method, extra in [("tasks/get", {}), ("tasks/update", {"action": "accept", "data": {"quality": "4k"}}), ("tasks/cancel", {})]:
            denied = await client.post(
                "/mcp",
                headers={"X-Caller-Sub": "bob@studio.local"},
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": {"taskId": task_id, **extra}},
            )
            assert denied.status_code == 200
            assert denied.json()["error"]["code"] == -32003

        # Invalid quality and unknown action return clean -32602 JSON-RPC errors (not HTTP 500)
        bad_quality = await client.post(
            "/mcp",
            headers={"X-Caller-Sub": "alice@studio.local"},
            json={"jsonrpc": "2.0", "id": 3, "method": "tasks/update", "params": {"taskId": task_id, "action": "accept", "data": {"quality": "8k"}}},
        )
        assert bad_quality.status_code == 200
        assert bad_quality.json()["error"]["code"] == -32602

        # Unknown taskId and unknown resource URI return -32004 JSON-RPC errors
        missing_task = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 4, "method": "tasks/get", "params": {"taskId": "task_does_not_exist"}},
        )
        assert missing_task.status_code == 200
        assert missing_task.json()["error"]["code"] == -32004

        missing_res = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "studio://artifacts/missing"}},
        )
        assert missing_res.status_code == 200
        assert missing_res.json()["error"]["code"] == -32004


def test_opaque_state_token_ttl_enforced() -> None:
    signer = SignedOpaqueStateManager("test-secret")
    stale_token = signer.seal(
        OpaquePaginationState(offset=10, filter_region="eu-west-compliance", issued_at=int(time.time()) - 3600)
    )
    with pytest.raises(ValueError, match="Expired"):
        signer.unseal(stale_token, max_age_sec=60)
