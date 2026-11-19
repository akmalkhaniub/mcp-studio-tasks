"""Durable SQLite Tasks extension: negotiation, tasks/get, tasks/update (input_required), cancel & progress (`REQ-ADV-03`)."""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskManager:
    """SQLite-backed durable MCP Task manager surviving server process restarts (`INV-ADV-05`)."""

    db_path: str
    notifications: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    _running: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tasks (
                    task_id TEXT PRIMARY KEY,
                    owner_sub TEXT NOT NULL DEFAULT 'alice@studio.local',
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    status_message TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    result_json TEXT,
                    elicitation_json TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(mcp_tasks)").fetchall()}
            if "owner_sub" not in cols:
                conn.execute("ALTER TABLE mcp_tasks ADD COLUMN owner_sub TEXT NOT NULL DEFAULT 'alice@studio.local'")
            conn.commit()

    def emit_notification(self, method: str, params: dict[str, Any]) -> None:
        self.notifications.append({"jsonrpc": "2.0", "method": method, "params": params})

    def create_task(
        self,
        *,
        tool_name: str,
        params: dict[str, Any],
        owner_sub: str = "alice@studio.local",
        pause_for_input: bool = False,
        total_steps: int = 3,
    ) -> dict[str, Any]:
        task_id = f"task_{secrets.token_hex(6)}"
        status = "input_required" if pause_for_input else "working"
        msg = (
            "Task paused awaiting user bitrate/review confirmation."
            if pause_for_input
            else "Queued for background processing."
        )
        elicit = (
            {
                "mode": "form",
                "message": "Select output quality before finalizing media render.",
                "requestedSchema": {
                    "type": "object",
                    "properties": {
                        "quality": {"type": "string", "enum": ["720p", "1080p", "4k"]},
                    },
                    "required": ["quality"],
                    "additionalProperties": False,
                },
            }
            if pause_for_input
            else None
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_tasks (task_id, owner_sub, tool_name, status, progress, total, status_message, params_json, result_json, elicitation_json, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    task_id,
                    owner_sub,
                    tool_name,
                    status,
                    total_steps,
                    msg,
                    json.dumps(params),
                    json.dumps(elicit) if elicit else None,
                    time.time(),
                ),
            )
            conn.commit()
        return self.get_task(task_id, caller_sub=owner_sub)

    def advance_step(self, task_id: str, *, step: int, message: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] == "cancelled":
            return task
        total = int(task["total"])
        new_status = "completed" if result is not None or step >= total else "working"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mcp_tasks
                SET status = ?, progress = ?, status_message = ?, result_json = COALESCE(?, result_json), updated_at = ?
                WHERE task_id = ?
                """,
                (
                    new_status,
                    min(step, total),
                    message,
                    json.dumps(result) if result is not None else None,
                    time.time(),
                    task_id,
                ),
            )
            conn.commit()
        self.emit_notification(
            "notifications/progress",
            {"progressToken": task_id, "progress": min(step, total), "total": total, "message": message},
        )
        return self.get_task(task_id)

    def start_background_render(self, *, task_id: str, title: str, step_delay_sec: float = 0.05) -> None:
        async def _worker() -> None:
            try:
                for step in range(1, 4):
                    await asyncio.sleep(step_delay_sec)
                    current = self.get_task(task_id)
                    if current["status"] == "cancelled":
                        return
                    if step < 3:
                        self.advance_step(task_id, step=step, message=f"Rendering '{title}' stage {step}/3")
                    else:
                        self.advance_step(
                            task_id,
                            step=3,
                            message=f"Completed '{title}'",
                            result={
                                "content": [{"type": "text", "text": f"Rendered video briefing for '{title}'."}],
                                "structuredContent": {"title": title, "duration_sec": 45, "asset_uri": f"studio://renders/{task_id}.mp4"},
                            },
                        )
            except asyncio.CancelledError:
                return

        self._running[task_id] = asyncio.create_task(_worker())

    def get_task(self, task_id: str, *, caller_sub: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mcp_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Task '{task_id}' not found.")
        if caller_sub is not None and row["owner_sub"] != caller_sub:
            raise PermissionError("Handle is not authorization: caller does not own this taskId.")
        payload: dict[str, Any] = {
            "taskId": row["task_id"],
            "toolName": row["tool_name"],
            "status": row["status"],
            "progress": row["progress"],
            "total": row["total"],
            "statusMessage": row["status_message"],
        }
        if row["elicitation_json"]:
            payload["elicitation"] = json.loads(row["elicitation_json"])
        if row["result_json"]:
            payload["result"] = json.loads(row["result_json"])
        return payload

    def update_task_input(
        self,
        *,
        task_id: str,
        action: str,
        data: dict[str, Any],
        caller_sub: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an `input_required` paused task (`accept`, `decline`, `cancel`) and finish/cancel durably."""
        if action not in ("accept", "decline", "cancel"):
            raise ValueError(f"Invalid tasks/update action '{action}': expected 'accept', 'decline', or 'cancel'.")
        task = self.get_task(task_id, caller_sub=caller_sub)
        if task["status"] != "input_required":
            raise ValueError(f"Task '{task_id}' is '{task['status']}', not 'input_required'.")
        if action in ("cancel", "decline"):
            return self.cancel_task(task_id, reason=f"User selected '{action}'.", caller_sub=caller_sub)

        quality = str(data.get("quality", "1080p"))
        if quality not in ("720p", "1080p", "4k"):
            raise ValueError(f"Invalid quality '{quality}': expected one of ['720p', '1080p', '4k'].")
        final_res = {
            "content": [{"type": "text", "text": f"Task '{task_id}' resumed and completed at {quality}."}],
            "structuredContent": {"taskId": task_id, "quality": quality, "status": "completed"},
        }
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mcp_tasks
                SET status = 'completed', progress = total, status_message = ?, elicitation_json = NULL, result_json = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (f"Completed at {quality}", json.dumps(final_res), time.time(), task_id),
            )
            conn.commit()
        self.emit_notification(
            "notifications/progress",
            {"progressToken": task_id, "progress": task["total"], "total": task["total"], "message": f"Completed at {quality}"},
        )
        return self.get_task(task_id, caller_sub=caller_sub)

    def cancel_task(self, task_id: str, reason: str = "Cancelled by client", *, caller_sub: str | None = None) -> dict[str, Any]:
        self.get_task(task_id, caller_sub=caller_sub)
        bg = self._running.pop(task_id, None)
        if bg and not bg.done():
            bg.cancel()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE mcp_tasks SET status = 'cancelled', status_message = ?, elicitation_json = NULL, updated_at = ? WHERE task_id = ?",
                (reason, time.time(), task_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Task '{task_id}' not found.")
            conn.commit()
        return self.get_task(task_id, caller_sub=caller_sub)
