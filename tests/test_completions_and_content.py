"""Tests for context-aware completions + all 6 content types (PNG image, WAV audio, structured, link, embedded) (`REQ-ADV-04`)."""

from __future__ import annotations

import base64
import httpx
import pytest

from mcp_studio_tasks.main import create_app


@pytest.mark.asyncio
async def test_completions_assist_ux_without_bypassing_validation(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "studio.db"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        comp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "completion/complete",
                "params": {"ref": {"type": "ref/prompt", "name": "brief"}, "argument": {"name": "region", "value": "eu"}},
            },
        )
        vals = comp.json()["result"]["completion"]["values"]
        assert "eu-west-compliance" in vals
        assert "us-east-prod" not in vals

        # Invalid region still rejected at execution time even though completions exist
        bad_prompt = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "prompts/get",
                "params": {"name": "eu-west-report", "arguments": {"region": "invalid-region"}},
            },
        )
        assert "error" in bad_prompt.json()


@pytest.mark.asyncio
async def test_multimodal_png_wav_structured_resource_link_and_embedded(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "studio.db"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "build_analytics_packet",
                    "arguments": {"region": "eu-west-compliance", "include_embedded_archive": True},
                },
            },
        )
        result = res.json()["result"]
        types = {b["type"] for b in result["content"]}
        assert types == {"text", "image", "audio", "resource_link", "resource"}
        assert result["structuredContent"]["token_comparison"]["tokens_saved"] > 1000

        img_block = next(b for b in result["content"] if b["type"] == "image")
        assert base64.b64decode(img_block["data"]).startswith(b"\x89PNG\r\n\x1a\n")

        aud_block = next(b for b in result["content"] if b["type"] == "audio")
        assert base64.b64decode(aud_block["data"]).startswith(b"RIFF")
