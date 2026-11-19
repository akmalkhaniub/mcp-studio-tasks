"""Tests for stateless 2026 scaling vs legacy session failure, cart handle authorization & opaque state (`REQ-ADV-01`)."""

from __future__ import annotations

import httpx
import pytest

from mcp_studio_tasks.main import create_app


@pytest.mark.asyncio
async def test_stateless_multi_replica_succeeds_while_legacy_session_breaks(tmp_path) -> None:
    db_path = str(tmp_path / "shared_state.db")
    replica1 = create_app(db_path=db_path, instance_id="replica-1")
    replica2 = create_app(db_path=db_path, instance_id="replica-2")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=replica1), base_url="http://test") as c1, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=replica2), base_url="http://test"
    ) as c2:
        # 1. Stateless 2026 endpoint needs no initialize; opaque cursor round-trips seamlessly from replica-1 to replica-2
        p1 = await c1.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_region_catalog", "arguments": {"region": "eu-west-compliance"}},
            },
        )
        assert p1.json()["_meta"]["serverInstance"] == "replica-1"
        cursor = p1.json()["result"]["structuredContent"]["next_cursor_token"]

        p2 = await c2.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_region_catalog", "arguments": {"cursor_token": cursor}},
            },
        )
        assert p2.json()["_meta"]["serverInstance"] == "replica-2"
        assert p2.json()["result"]["structuredContent"]["offset"] == 10

        # Tampered opaque state token is rejected
        tampered = cursor[:-4] + ("AAAA" if cursor[-4:] != "AAAA" else "BBBB")
        bad = await c2.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_region_catalog", "arguments": {"cursor_token": tampered}},
            },
        )
        assert "hmac mismatch" in bad.json()["error"]["message"].lower()

        # 2. Legacy Mcp-Session-Id sticky session succeeds on replica-1 but fails on replica-2
        init = await c1.post("/mcp/legacy", json={"jsonrpc": "2.0", "id": 10, "method": "initialize"})
        sid = init.headers["mcp-session-id"]
        lost = await c2.post(
            "/mcp/legacy",
            headers={"Mcp-Session-Id": sid},
            json={"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
        )
        assert lost.status_code == 400
        assert "SESSION_NOT_FOUND" in lost.json()["error"]["message"]


@pytest.mark.asyncio
async def test_state_handle_is_not_authentication(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "carts.db"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = await client.post(
            "/mcp",
            headers={"X-Caller-Sub": "alice@studio.local"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "cart_add", "arguments": {"item_sku": "SKU-VIDEO-PRO", "quantity": 1}},
            },
        )
        cart_id = alice.json()["result"]["structuredContent"]["cart_id"]

        # Another caller guessing or stealing cart_id is rejected
        mallory = await client.post(
            "/mcp",
            headers={"X-Caller-Sub": "mallory@studio.local"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "cart_add", "arguments": {"cart_id": cart_id, "item_sku": "SKU-FREE", "quantity": 5}},
            },
        )
        assert mallory.json()["error"]["code"] == -32003
        assert "handle is not authorization" in mallory.json()["error"]["message"].lower()
