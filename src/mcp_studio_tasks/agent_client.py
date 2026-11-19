"""End-to-end MCP Chat Agent handling tool selection, elicitation prompts, task polling & progress (`REQ-ADV-05`)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class McpChatAgent:
    """SDK-style async client connecting an intent router/LLM loop to the advanced MCP server."""

    def __init__(self, *, mcp_url: str = "http://test/mcp", transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.mcp_url = mcp_url
        self._transport = transport
        self.protocol_version = "2026-07-28"
        self.capabilities = {"elicitation": {"form": True, "url": True}, "tasks": True}

    async def _rpc(self, method: str, params: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        merged_params = dict(params or {})
        merged_params["_meta"] = {
            "protocolVersion": self.protocol_version,
            "capabilities": self.capabilities,
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": merged_params}
        kwargs: dict[str, Any] = {"timeout": 10.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
            kwargs["base_url"] = "http://test"
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(self.mcp_url, json=body, headers=headers or {})
            resp.raise_for_status()
            return resp.json()

    async def list_tools(self) -> list[dict[str, Any]]:
        res = await self._rpc("tools/list")
        return res["result"]["tools"]

    def choose_tool_for_prompt(self, user_prompt: str, tools: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        """Deterministic intent-to-tool selector standing in for an LLM tool-caller in offline mode."""
        available = {t["name"] for t in tools}
        text = user_prompt.lower()
        if ("render" in text or "video" in text) and "render_briefing_video" in available:
            return "render_briefing_video", {"title": user_prompt.strip()[:80], "step_delay_sec": 0.02}
        if ("publish" in text or "brief" in text) and "publish_media_brief" in available:
            return "publish_media_brief", {"report_id": "RPT-1001"}
        if ("billing" in text or "cdn" in text) and "connect_cdn_billing" in available:
            return "connect_cdn_billing", {"provider": "cloud-cdn"}
        if ("analytics" in text or "chart" in text or "audio" in text) and "build_analytics_packet" in available:
            return "build_analytics_packet", {"region": "eu-west-compliance"}
        return "list_region_catalog", {"region": "eu-west-compliance"}

    async def run_turn(
        self,
        user_prompt: str,
        *,
        elicitation_response: dict[str, Any] | None = None,
        max_poll_steps: int = 20,
    ) -> dict[str, Any]:
        """Discover tools, select & invoke tool, handle elicitation retry, and poll Tasks to completion."""
        tools = await self.list_tools()
        tool_name, args = self.choose_tool_for_prompt(user_prompt, tools)
        call_res = await self._rpc("tools/call", {"name": tool_name, "arguments": args})
        result = call_res["result"]

        # 1. Handle input_required elicitation round-trip
        structured = result.get("structuredContent") or {}
        if structured.get("status") == "input_required":
            if elicitation_response is None:
                return {
                    "status": "awaiting_elicitation",
                    "selected_tool": tool_name,
                    "elicitation": structured["elicitation"],
                }
            if structured["elicitation"]["mode"] == "form":
                args["elicitation"] = elicitation_response
            else:
                args["url_completed_session_id"] = elicitation_response.get("session_id", "url-done-1")
            retry = await self._rpc("tools/call", {"name": tool_name, "arguments": args})
            return {
                "status": "completed",
                "selected_tool": tool_name,
                "result": retry["result"],
            }

        # 2. Handle async Task polling & collect progress trace
        if "task" in result:
            task = result["task"]
            progress_log: list[dict[str, Any]] = []
            for _ in range(max_poll_steps):
                progress_log.append(
                    {"status": task["status"], "progress": task["progress"], "total": task["total"], "message": task["statusMessage"]}
                )
                if task["status"] in ("completed", "failed", "cancelled", "input_required"):
                    break
                await asyncio.sleep(0.03)
                polled = await self._rpc("tasks/get", {"taskId": task["taskId"]})
                task = polled["result"]["task"]
            return {
                "status": task["status"],
                "selected_tool": tool_name,
                "task": task,
                "progress_history": progress_log,
            }

        return {
            "status": "completed",
            "selected_tool": tool_name,
            "result": result,
        }
