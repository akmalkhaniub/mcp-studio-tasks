"""FastAPI entrypoint for MCP Studio Tasks: stateless MCP, chat agent API, SSE progress stream & Light/Dark UI."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent_client import McpChatAgent
from .server import StudioMcpServer, build_studio_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(*, db_path: str | None = None, instance_id: str = "replica-1") -> FastAPI:
    """Create configured stateless MCP Studio Tasks application backed by durable SQLite storage."""
    resolved_db = db_path or os.getenv("MCP_STUDIO_DB_PATH", "./studio_tasks.db")
    secret = os.getenv("MCP_STUDIO_STATE_SECRET", "dev-only-local-hmac-secret-change-in-prod")
    studio = StudioMcpServer(db_path=resolved_db, state_secret=secret, instance_id=instance_id)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield

    app = FastAPI(
        title="MCP Studio Tasks",
        description="2026 Stateless MCP Server, Form/URL Elicitation, Durable Tasks v2, Completions, Multimodal Results & Chat Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.studio = studio

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "instance_id": studio.instance_id, "protocol_version": "2026-07-28"}

    @app.post("/api/chat")
    async def chat_turn(request: Request) -> dict[str, Any]:
        body = await request.json()
        prompt = str(body.get("prompt", ""))
        elicit_reply = body.get("elicitation_response")
        agent = McpChatAgent(mcp_url="http://test/mcp", transport=httpx.ASGITransport(app=app))
        return await agent.run_turn(prompt, elicitation_response=elicit_reply)

    @app.get("/mcp/events")
    async def sse_events() -> StreamingResponse:
        async def _gen():
            for note in list(studio.tasks.notifications):
                yield f"data: {json.dumps(note)}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    app.include_router(build_studio_router(studio), tags=["MCP Studio Protocol"])

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>MCP Studio Tasks</h1>")

    return app


app = create_app()
