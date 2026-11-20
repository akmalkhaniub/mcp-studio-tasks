"""Tests for optimistic concurrency (`expectedVersion` CAS), idempotency-key deduplication & stateless W3C traceparent."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mcp_studio_tasks.main import create_app


def test_task_idempotency_key_deduplicates_across_stateless_replicas(tmp_path):
    db_file = str(tmp_path / "studio_cas.db")
    replica_1 = TestClient(create_app(db_path=db_file, instance_id="replica-1"))
    replica_2 = TestClient(create_app(db_path=db_file, instance_id="replica-2"))

    traceparent = "00-11223344556677889900aabbccddeeff-0011223344556677-01"
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "render_briefing_video",
            "arguments": {
                "title": "Q4 Reliability Review",
                "pause_for_input": True,
                "idempotency_key": "idem-q4-video-001",
            },
            "_meta": {
                "protocolVersion": "2026-07-28",
                "capabilities": {"tasks": True},
                "traceparent": traceparent,
            },
        },
    }

    r1 = replica_1.post("/mcp", headers={"x-caller-sub": "alice@studio.local"}, json=rpc_payload)
    assert r1.status_code == 200
    body1 = r1.json()
    task1 = body1["result"]["task"]
    assert task1["deduplicated"] is False
    assert task1["version"] == 1
    assert body1["_meta"]["traceparent"] == traceparent
    assert body1["_meta"]["serverInstance"] == "replica-1"

    # Retry on replica-2 with same idempotency_key returns the exact same taskId without duplicating work
    r2 = replica_2.post("/mcp", headers={"x-caller-sub": "alice@studio.local"}, json=rpc_payload)
    assert r2.status_code == 200
    body2 = r2.json()
    task2 = body2["result"]["task"]
    assert task2["taskId"] == task1["taskId"]
    assert task2["deduplicated"] is True
    assert body2["_meta"]["serverInstance"] == "replica-2"
    assert body2["_meta"]["traceparent"] == traceparent

    # Different caller with same key gets their own isolated task
    r3 = replica_2.post("/mcp", headers={"x-caller-sub": "bob@studio.local"}, json=rpc_payload)
    assert r3.status_code == 200
    assert r3.json()["result"]["task"]["taskId"] != task1["taskId"]


def test_task_optimistic_concurrency_cas_blocks_stale_update_and_cancel(tmp_path):
    db_file = str(tmp_path / "studio_cas_conflict.db")
    replica_1 = TestClient(create_app(db_path=db_file, instance_id="replica-1"))
    replica_2 = TestClient(create_app(db_path=db_file, instance_id="replica-2"))

    create_resp = replica_1.post(
        "/mcp",
        headers={"x-caller-sub": "alice@studio.local"},
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "render_briefing_video",
                "arguments": {"title": "CAS Test", "pause_for_input": True},
                "_meta": {"capabilities": {"tasks": True}},
            },
        },
    ).json()
    task = create_resp["result"]["task"]
    task_id = task["taskId"]
    assert task["version"] == 1

    # Stale expectedVersion (e.g., 99) is rejected with JSON-RPC -32009 Conflict
    stale_resp = replica_2.post(
        "/mcp",
        headers={"x-caller-sub": "alice@studio.local"},
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tasks/update",
            "params": {
                "taskId": task_id,
                "action": "accept",
                "data": {"quality": "4k"},
                "expectedVersion": 99,
            },
        },
    ).json()
    assert stale_resp["error"]["code"] == -32009

    # Valid expectedVersion=1 succeeds and increments version to 2
    ok_resp = replica_2.post(
        "/mcp",
        headers={"x-caller-sub": "alice@studio.local"},
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tasks/update",
            "params": {
                "taskId": task_id,
                "action": "accept",
                "data": {"quality": "4k"},
                "expectedVersion": 1,
            },
        },
    ).json()
    assert ok_resp["result"]["task"]["status"] == "completed"
    assert ok_resp["result"]["task"]["version"] == 2

    # Late cancel using old expectedVersion=1 also returns -32009 Conflict
    late_cancel = replica_1.post(
        "/mcp",
        headers={"x-caller-sub": "alice@studio.local"},
        json={
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tasks/cancel",
            "params": {"taskId": task_id, "expectedVersion": 1},
        },
    ).json()
    assert late_cancel["error"]["code"] == -32009
