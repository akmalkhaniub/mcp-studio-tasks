"""Stateless 2026-07-28 MCP Server vs Legacy Session Server, Handles, Elicitation, Tasks & Content (`REQ-ADV-01..04`)."""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .content import ArtifactCatalog, DATASET_REGIONS, REPORT_TEMPLATES, complete_argument
from .elicitation import handle_connect_cdn_billing, handle_publish_brief
from .state import DurableCartStore, OpaquePaginationState, SignedOpaqueStateManager
from .tasks import TaskConflictError, TaskManager


class StudioMcpServer:
    """Implements stateless `2026-07-28` MCP RPC, Tasks v2, completions, rich content, and legacy session contrast."""

    def __init__(self, *, db_path: str, state_secret: str, instance_id: str = "replica-1") -> None:
        self.instance_id = instance_id
        self.carts = DurableCartStore(db_path=db_path)
        self.tasks = TaskManager(db_path=db_path)
        self.state_signer = SignedOpaqueStateManager(secret=state_secret)
        self.catalog = ArtifactCatalog()
        self._legacy_sessions: dict[str, dict[str, Any]] = {}
        self._disabled_tools_by_session: dict[str, set[str]] = {}
        self._admin_export_enabled = True

    def _base_tools(self) -> list[dict[str, Any]]:
        tools = [
            {
                "name": "cart_add",
                "description": "Add an asset SKU to a server-stored cart handle (enforces owner verification).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cart_id": {"type": "string"},
                        "item_sku": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["item_sku", "quantity"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_region_catalog",
                "description": "Stateless paginated listing returning an HMAC-signed opaque cursor handle.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string"},
                        "cursor_token": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "publish_media_brief",
                "description": "Publish briefing; uses `input_required` form elicitation (`accept`/`decline`/`cancel`) with fallback.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "report_id": {"type": "string"},
                        "target_channel": {"type": "string"},
                        "notify_team": {"type": "boolean"},
                        "elicitation": {"type": "object"},
                    },
                    "required": ["report_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "connect_cdn_billing",
                "description": "Configure external CDN payment/key via URL-mode elicitation (forbids secrets in form mode).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "form_fields_attempted": {"type": "array", "items": {"type": "string"}},
                        "url_completed_session_id": {"type": "string"},
                    },
                    "required": ["provider"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "render_briefing_video",
                "description": "Long-running job returning a durable MCP Task handle (`tasks/get`, `tasks/update`, `tasks/cancel`).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "pause_for_input": {"type": "boolean"},
                        "step_delay_sec": {"type": "number"},
                        "idempotency_key": {"type": "string"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "build_analytics_packet",
                "description": "Return all 6 MCP result types: text, structuredContent, PNG image, WAV audio, resource_link & embedded resource.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string"},
                        "include_embedded_archive": {"type": "boolean"},
                    },
                    "required": ["region"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "admin_export_bundle",
                "description": "Privileged export tool that can be dynamically disabled per session (triggers tools/list_changed).",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ]
        if not self._admin_export_enabled:
            tools = [t for t in tools if t["name"] != "admin_export_bundle"]
        return tools

    def set_session_tool_disabled(self, *, session_scope: str, tool_name: str, disabled: bool) -> list[str]:
        bucket = self._disabled_tools_by_session.setdefault(session_scope, set())
        if disabled:
            bucket.add(tool_name)
        else:
            bucket.discard(tool_name)
        self.tasks.emit_notification("notifications/tools/list_changed", {"session": session_scope})
        return sorted(bucket)

    def _list_tools_for(self, session_scope: str) -> list[dict[str, Any]]:
        disabled = self._disabled_tools_by_session.get(session_scope, set())
        return [t for t in self._base_tools() if t["name"] not in disabled]

    def _handle_tool_call(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        caller_sub: str,
        session_scope: str,
        meta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if name in self._disabled_tools_by_session.get(session_scope, set()):
            raise HTTPException(status_code=403, detail=f"Tool '{name}' is disabled for session '{session_scope}'.")

        if name == "cart_add":
            res = self.carts.create_or_add(
                cart_id=arguments.get("cart_id"),
                owner_sub=caller_sub,
                item_sku=str(arguments["item_sku"]),
                quantity=int(arguments["quantity"]),
            )
            return {"content": [{"type": "text", "text": f"Cart {res['cart_id']} updated."}], "structuredContent": res}

        if name == "list_region_catalog":
            if "cursor_token" in arguments and arguments["cursor_token"]:
                st = self.state_signer.unseal(str(arguments["cursor_token"]))
                region = st.filter_region
                offset = st.offset
            else:
                region = str(arguments.get("region", "eu-west-compliance"))
                if region not in DATASET_REGIONS:
                    raise ValueError(f"Unsupported region: {region}")
                offset = 0
            next_token = self.state_signer.seal(
                OpaquePaginationState(offset=offset + 10, filter_region=region, issued_at=int(time.time()))
            )
            data = {"region": region, "offset": offset, "next_cursor_token": next_token, "server_instance": self.instance_id}
            return {"content": [{"type": "text", "text": f"Page offset={offset} for {region}"}], "structuredContent": data}

        if name == "publish_media_brief":
            res = handle_publish_brief(arguments, meta)
            return {"content": [{"type": "text", "text": str(res)}], "structuredContent": res, "isError": bool(res.get("isError"))}

        if name == "connect_cdn_billing":
            res = handle_connect_cdn_billing(arguments, meta)
            return {"content": [{"type": "text", "text": str(res)}], "structuredContent": res, "isError": bool(res.get("isError"))}

        if name == "render_briefing_video":
            caps = (meta or {}).get("capabilities") or {}
            if not caps.get("tasks"):
                raise HTTPException(status_code=400, detail="Client must negotiate capabilities.tasks in _meta for async rendering.")
            pause_for_input = bool(arguments.get("pause_for_input", False))
            idem_key = arguments.get("idempotency_key") or (meta or {}).get("idempotencyKey")
            task = self.tasks.create_task(
                tool_name=name,
                params=arguments,
                owner_sub=caller_sub,
                pause_for_input=pause_for_input,
                idempotency_key=str(idem_key) if idem_key else None,
            )
            if not pause_for_input and not task.get("deduplicated"):
                self.tasks.start_background_render(
                    task_id=task["taskId"],
                    title=str(arguments["title"]),
                    step_delay_sec=float(arguments.get("step_delay_sec", 0.05)),
                )
            return {"task": task, "content": [{"type": "text", "text": f"Task {task['taskId']} created ({task['status']})."}]}

        if name == "build_analytics_packet":
            return self.catalog.build_multimodal_packet(
                region=str(arguments["region"]),
                include_embedded_archive=bool(arguments.get("include_embedded_archive", False)),
            )

        if name == "admin_export_bundle":
            return {"content": [{"type": "text", "text": "Exported admin bundle."}], "structuredContent": {"exported": True}}

        raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")

    def handle_stateless_rpc(self, *, payload: dict[str, Any], caller_sub: str, session_scope: str) -> dict[str, Any]:
        """Execute JSON-RPC 2.0 following stateless 2026-07-28 rules (per-request `_meta`, no connection pinning)."""
        req_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}

        if method == "tools/list":
            result: dict[str, Any] = {"tools": self._list_tools_for(session_scope)}
        elif method == "tools/call":
            try:
                result = self._handle_tool_call(
                    name=str(params.get("name", "")),
                    arguments=params.get("arguments") or {},
                    caller_sub=caller_sub,
                    session_scope=session_scope,
                    meta=meta,
                )
            except (ValueError, KeyError) as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}
            except PermissionError as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32003, "message": str(exc)}}
        elif method in ("tasks/get", "tasks/update", "tasks/cancel"):
            try:
                task_id = str(params["taskId"])
                expected_version = params.get("expectedVersion")
                if method == "tasks/get":
                    result = {"task": self.tasks.get_task(task_id, caller_sub=caller_sub)}
                elif method == "tasks/update":
                    result = {
                        "task": self.tasks.update_task_input(
                            task_id=task_id,
                            action=str(params.get("action", "accept")),
                            data=params.get("data") or {},
                            caller_sub=caller_sub,
                            expected_version=int(expected_version) if expected_version is not None else None,
                        )
                    }
                else:
                    result = {
                        "task": self.tasks.cancel_task(
                            task_id,
                            caller_sub=caller_sub,
                            expected_version=int(expected_version) if expected_version is not None else None,
                        )
                    }
            except KeyError as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32004, "message": str(exc)}}
            except TaskConflictError as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32009, "message": str(exc)}}
            except ValueError as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}
            except PermissionError as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32003, "message": str(exc)}}
        elif method == "completion/complete":
            ref = params.get("ref") or {}
            arg = params.get("argument") or {}
            result = complete_argument(
                ref_type=str(ref.get("type", "ref/prompt")),
                ref_name=str(ref.get("name", "")),
                arg_name=str(arg.get("name", "")),
                value_prefix=str(arg.get("value", "")),
            )
        elif method == "prompts/get":
            tpl = str(params.get("name", ""))
            region = str((params.get("arguments") or {}).get("region", ""))
            if tpl not in REPORT_TEMPLATES or region not in DATASET_REGIONS:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Invalid prompt template '{tpl}' or region '{region}'."},
                }
            result = {
                "messages": [{"role": "user", "content": {"type": "text", "text": f"Generate {tpl} for {region}."}}]
            }
        elif method == "resources/list":
            result = {"resources": self.catalog.list_resources()}
        elif method == "resources/read":
            try:
                result = {"contents": [self.catalog.read_resource(str(params["uri"]))]}
            except KeyError as exc:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32004, "message": str(exc)}}
        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method '{method}'"}}

        traceparent = str(meta.get("traceparent") or "").strip() or f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
            "_meta": {
                "serverInstance": self.instance_id,
                "protocolVersion": meta.get("protocolVersion", "2026-07-28"),
                "traceparent": traceparent,
            },
        }


def build_studio_router(server: StudioMcpServer) -> APIRouter:
    """Mount stateless `/mcp`, legacy `/mcp/legacy`, session-toggle helpers & notifications stream."""
    router = APIRouter()

    @router.post("/mcp")
    async def stateless_mcp(request: Request) -> JSONResponse:
        payload = await request.json()
        caller_sub = request.headers.get("x-caller-sub", "alice@studio.local")
        session_scope = request.headers.get("x-client-scope", "default")
        resp = server.handle_stateless_rpc(payload=payload, caller_sub=caller_sub, session_scope=session_scope)
        return JSONResponse(content=resp)

    @router.post("/mcp/legacy")
    async def legacy_session_mcp(request: Request) -> JSONResponse:
        """Legacy `2024-11-05` stateful handler requiring `initialize` + `Mcp-Session-Id` in memory."""
        payload = await request.json()
        method = payload.get("method")
        req_id = payload.get("id")
        if method == "initialize":
            sid = f"legacy-{secrets.token_hex(6)}"
            server._legacy_sessions[sid] = {"initialized": True}
            return JSONResponse(
                headers={"Mcp-Session-Id": sid},
                content={"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05"}},
            )
        sid = request.headers.get("mcp-session-id")
        if not sid or sid not in server._legacy_sessions:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32000,
                        "message": f"SESSION_NOT_FOUND on instance '{server.instance_id}': stateful server lost sticky session.",
                    },
                },
            )
        return JSONResponse(
            content=server.handle_stateless_rpc(
                payload=payload,
                caller_sub=request.headers.get("x-caller-sub", "alice@studio.local"),
                session_scope=sid,
            )
        )

    @router.post("/admin/toggle-session-tool")
    async def toggle_session_tool(session_scope: str, tool_name: str, disabled: bool) -> dict[str, Any]:
        current = server.set_session_tool_disabled(session_scope=session_scope, tool_name=tool_name, disabled=disabled)
        return {"session_scope": session_scope, "disabled_tools": current, "notified": "notifications/tools/list_changed"}

    @router.get("/mcp/notifications")
    async def list_notifications() -> list[dict[str, Any]]:
        return list(server.tasks.notifications)

    return router
