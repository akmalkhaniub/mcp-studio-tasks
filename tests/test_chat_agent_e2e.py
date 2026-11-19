"""End-to-end tests for McpChatAgent handling tool selection, elicitation prompts, task polling & media (`REQ-ADV-05`)."""

from __future__ import annotations

import httpx
import pytest

from mcp_studio_tasks.agent_client import McpChatAgent
from mcp_studio_tasks.main import create_app


@pytest.mark.asyncio
async def test_chat_agent_end_to_end_flows(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "chat.db"))
    agent = McpChatAgent(mcp_url="http://test/mcp", transport=httpx.ASGITransport(app=app))

    # 1. Analytics prompt -> selects build_analytics_packet and returns multimodal blocks
    turn1 = await agent.run_turn("Generate analytics chart and audio report")
    assert turn1["status"] == "completed"
    assert turn1["selected_tool"] == "build_analytics_packet"
    assert {b["type"] for b in turn1["result"]["content"]} >= {"text", "image", "audio", "resource_link"}

    # 2. Publish prompt -> pauses for form elicitation -> completes on accept
    turn2_pause = await agent.run_turn("Publish media briefing report")
    assert turn2_pause["status"] == "awaiting_elicitation"
    assert turn2_pause["elicitation"]["mode"] == "form"

    turn2_done = await agent.run_turn(
        "Publish media briefing report",
        elicitation_response={"action": "accept", "data": {"target_channel": "internal-wiki", "notify_team": False}},
    )
    assert turn2_done["status"] == "completed"
    assert turn2_done["result"]["structuredContent"]["status"] == "published"

    # 3. Render video prompt -> negotiates Task, polls until completed, captures progress history
    turn3 = await agent.run_turn("Render video briefing on gateway performance")
    assert turn3["status"] == "completed"
    assert turn3["selected_tool"] == "render_briefing_video"
    assert turn3["task"]["status"] == "completed"
    assert len(turn3["progress_history"]) >= 1
