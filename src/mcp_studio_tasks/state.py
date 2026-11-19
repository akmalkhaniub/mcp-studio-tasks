"""State handles (SQLite cart/workflow with ownership enforcement) & signed opaque client state (`REQ-ADV-01`)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OpaquePaginationState(BaseModel):
    """Strict schema validating decoded client-carried opaque state (`INV-ADV-03`)."""

    model_config = ConfigDict(extra="forbid")

    offset: int = Field(ge=0, le=100_000)
    filter_region: str = Field(pattern=r"^[a-z0-9-]+$", max_length=32)
    issued_at: int = Field(ge=0)


class SignedOpaqueStateManager:
    """Signs and verifies opaque state tokens round-tripped through untrusted clients."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def seal(self, state: OpaquePaginationState) -> str:
        raw = state.model_dump_json().encode("utf-8")
        body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        sig = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def unseal(self, token: str, *, max_age_sec: int | None = None) -> OpaquePaginationState:
        if "." not in token:
            raise ValueError("Invalid opaque state handle format (missing signature).")
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected):
            raise ValueError("Tampered opaque client state rejected (HMAC mismatch).")
        padded = body + "=" * (-len(body) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = OpaquePaginationState.model_validate_json(decoded)
        if max_age_sec is not None and (time.time() - parsed.issued_at) > max_age_sec:
            raise ValueError("Expired opaque client state token.")
        return parsed


class DurableCartStore:
    """Server-side workflow/cart handles stored in SQLite; verifies caller identity (`handle != auth`)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS carts (
                    cart_id TEXT PRIMARY KEY,
                    owner_sub TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def create_or_add(self, *, cart_id: str | None, owner_sub: str, item_sku: str, quantity: int) -> dict[str, Any]:
        cid = cart_id or f"cart_{secrets.token_hex(6)}"
        with self._connect() as conn:
            row = conn.execute("SELECT owner_sub, items_json, status FROM carts WHERE cart_id = ?", (cid,)).fetchone()
            if row is not None:
                if row["owner_sub"] != owner_sub:
                    raise PermissionError("Handle is not authorization: cart belongs to a different user.")
                items: list[dict[str, Any]] = json.loads(row["items_json"])
            else:
                items = []
            items.append({"sku": item_sku, "quantity": quantity})
            conn.execute(
                """
                INSERT INTO carts (cart_id, owner_sub, items_json, status, updated_at)
                VALUES (?, ?, ?, 'open', ?)
                ON CONFLICT(cart_id) DO UPDATE SET items_json = excluded.items_json, updated_at = excluded.updated_at
                """,
                (cid, owner_sub, json.dumps(items), time.time()),
            )
            conn.commit()
        return {"cart_id": cid, "owner_sub": owner_sub, "items": items, "status": "open"}

    def get_cart(self, *, cart_id: str, owner_sub: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT owner_sub, items_json, status FROM carts WHERE cart_id = ?", (cart_id,)).fetchone()
        if row is None:
            raise KeyError(f"Cart handle '{cart_id}' not found.")
        if row["owner_sub"] != owner_sub:
            raise PermissionError("Handle is not authorization: caller does not own this cart_id.")
        return {
            "cart_id": cart_id,
            "owner_sub": row["owner_sub"],
            "items": json.loads(row["items_json"]),
            "status": row["status"],
        }
