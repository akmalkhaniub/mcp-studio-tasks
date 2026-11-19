"""Tests for form/URL elicitation, accept/decline/cancel outcomes & capability fallback (`REQ-ADV-02`)."""

from __future__ import annotations

import httpx
import pytest

from mcp_studio_tasks.main import create_app


@pytest.mark.asyncio
async def test_form_elicitation_accept_decline_cancel_and_fallback(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "studio.db"))
    caps_ok = {"protocolVersion": "2026-07-28", "capabilities": {"elicitation": {"form": True, "url": True}}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Initial call returns input_required schema
        step1 = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "publish_media_brief", "arguments": {"report_id": "RPT-1001"}, "_meta": caps_ok},
            },
        )
        sc1 = step1.json()["result"]["structuredContent"]
        assert sc1["status"] == "input_required"
        assert sc1["elicitation"]["mode"] == "form"

        # 2. Retry with accept completes cleanly
        accept = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "publish_media_brief",
                    "arguments": {
                        "report_id": "RPT-1001",
                        "elicitation": {"action": "accept", "data": {"target_channel": "executive-digest", "notify_team": True}},
                    },
                    "_meta": caps_ok,
                },
            },
        )
        assert accept.json()["result"]["structuredContent"]["status"] == "published"

        # 3. Decline and cancel outcomes handled cleanly
        decline = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "publish_media_brief",
                    "arguments": {"report_id": "RPT-1001", "elicitation": {"action": "decline", "data": {}}},
                    "_meta": caps_ok,
                },
            },
        )
        assert decline.json()["result"]["structuredContent"]["status"] == "declined"

        cancel = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "publish_media_brief",
                    "arguments": {"report_id": "RPT-1001", "elicitation": {"action": "cancel", "data": {}}},
                    "_meta": caps_ok,
                },
            },
        )
        assert cancel.json()["result"]["structuredContent"]["status"] == "cancelled"

        # 4. Graceful fallback when client omits elicitation capability
        no_caps = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "publish_media_brief", "arguments": {"report_id": "RPT-1001"}, "_meta": {}},
            },
        )
        assert no_caps.json()["result"]["structuredContent"]["status"] == "fallback_missing_arguments"


@pytest.mark.asyncio
async def test_url_elicitation_blocks_passwords_and_keys_in_form_mode(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "studio.db"))
    caps_url = {"protocolVersion": "2026-07-28", "capabilities": {"elicitation": {"url": True}}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "connect_cdn_billing",
                    "arguments": {"provider": "cloud-cdn", "form_fields_attempted": ["api_key", "card_number"]},
                    "_meta": caps_url,
                },
            },
        )
        assert "error" in bad.json()
        assert "url mode" in bad.json()["error"]["message"].lower()

        ok = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "connect_cdn_billing", "arguments": {"provider": "cloud-cdn"}, "_meta": caps_url},
            },
        )
        sc = ok.json()["result"]["structuredContent"]
        assert sc["status"] == "input_required"
        assert sc["elicitation"]["mode"] == "url"
        assert sc["elicitation"]["url"].startswith("https://")
