"""Elicitation helpers: form & URL mode (`input-required`), accept/decline/cancel, and client fallback (`REQ-ADV-02`)."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SENSITIVE_FORM_KEYS = {"password", "api_key", "secret", "token", "card_number", "cvv", "ssn"}


class ElicitationReply(BaseModel):
    """Strict input returned on client retry after an `input_required` response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["accept", "decline", "cancel"]
    data: dict[str, Any] = Field(default_factory=dict)


class PublishBriefInput(BaseModel):
    """Multi-round-trip tool demonstrating form elicitation and client capability fallback."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    report_id: str = Field(pattern=r"^RPT-\d{3,6}$")
    target_channel: Literal["internal-wiki", "executive-digest", "public-blog"] | None = None
    notify_team: bool | None = None
    elicitation: ElicitationReply | None = None


class ConnectCdnBillingInput(BaseModel):
    """Demonstrates URL-mode elicitation banning plaintext secrets/cards from form fields (`INV-ADV-04`)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal["fastly", "cloudflare", "cloud-cdn"]
    form_fields_attempted: list[str] = Field(default_factory=list)
    url_completed_session_id: str | None = None


def supports_elicitation(meta: dict[str, Any] | None, *, mode: Literal["form", "url"] = "form") -> bool:
    """Inspect per-request `_meta.capabilities.elicitation` (stateless 2026 spec pattern)."""
    if not isinstance(meta, dict):
        return False
    caps = meta.get("capabilities") or {}
    elicit = caps.get("elicitation")
    if isinstance(elicit, bool):
        return elicit
    if isinstance(elicit, dict):
        if mode in elicit:
            return bool(elicit[mode])
        return bool(elicit)
    return False


def validate_form_schema_safety(schema_properties: dict[str, Any]) -> None:
    """Enforce `INV-ADV-04`: secrets, passwords, and payment keys MUST NOT appear in form elicitation schemas."""
    bad = [key for key in schema_properties if key.lower() in SENSITIVE_FORM_KEYS]
    if bad:
        raise ValueError(
            f"Forbidden form elicitation fields {bad}: passwords, API keys, and payment details must use URL mode."
        )


def handle_publish_brief(args: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    """Execute or pause via `input_required` form elicitation (with graceful fallback when unsupported)."""
    parsed = PublishBriefInput.model_validate(args)

    # 1. Process explicit elicitation outcome on retry
    if parsed.elicitation is not None:
        if parsed.elicitation.action == "cancel":
            return {"status": "cancelled", "report_id": parsed.report_id, "message": "User cancelled publication."}
        if parsed.elicitation.action == "decline":
            return {"status": "declined", "report_id": parsed.report_id, "message": "User declined confirmation prompt."}

        channel = parsed.elicitation.data.get("target_channel") or parsed.target_channel
        notify = parsed.elicitation.data.get("notify_team") if "notify_team" in parsed.elicitation.data else parsed.notify_team
        if not channel or notify is None:
            raise ValueError("Elicitation accepted, but required fields target_channel/notify_team are incomplete.")
        return {
            "status": "published",
            "report_id": parsed.report_id,
            "target_channel": channel,
            "notify_team": bool(notify),
        }

    # 2. If all args were provided explicitly (e.g., fallback or direct invocation), succeed immediately
    if parsed.target_channel is not None and parsed.notify_team is not None:
        return {
            "status": "published",
            "report_id": parsed.report_id,
            "target_channel": parsed.target_channel,
            "notify_team": parsed.notify_team,
        }

    # 3. Missing info: return `input_required` if client supports form elicitation, else degrade gracefully
    form_schema = {
        "type": "object",
        "properties": {
            "target_channel": {
                "type": "string",
                "enum": ["internal-wiki", "executive-digest", "public-blog"],
                "description": "Where should the briefing be published?",
            },
            "notify_team": {
                "type": "boolean",
                "description": "Send release notification to team channel?",
            },
        },
        "required": ["target_channel", "notify_team"],
        "additionalProperties": False,
    }
    validate_form_schema_safety(form_schema["properties"])

    if not supports_elicitation(meta, mode="form"):
        return {
            "status": "fallback_missing_arguments",
            "isError": True,
            "message": (
                "Client does not advertise form elicitation support; re-call publish_media_brief supplying "
                "`target_channel` and `notify_team` arguments directly."
            ),
            "required_arguments": ["target_channel", "notify_team"],
        }

    return {
        "status": "input_required",
        "elicitation": {
            "mode": "form",
            "message": f"Confirm publication settings for {parsed.report_id}.",
            "requestedSchema": form_schema,
            "outcomes": ["accept", "decline", "cancel"],
        },
    }


def handle_connect_cdn_billing(args: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    """Enforce URL-mode elicitation for API keys and payment credentials (`INV-ADV-04`)."""
    parsed = ConnectCdnBillingInput.model_validate(args)
    forbidden = [f for f in parsed.form_fields_attempted if f.lower() in SENSITIVE_FORM_KEYS]
    if forbidden:
        raise ValueError(
            f"Blocked insecure form collection for {forbidden}: sensitive keys/payment info must use URL mode."
        )

    if parsed.url_completed_session_id:
        return {
            "status": "connected",
            "provider": parsed.provider,
            "session_ref": parsed.url_completed_session_id,
            "message": "Out-of-band credential & billing verification completed safely outside MCP prompts.",
        }

    if not supports_elicitation(meta, mode="url"):
        return {
            "status": "url_elicitation_unsupported",
            "isError": True,
            "message": "Client does not support URL elicitation; provide out-of-band verification URL manually.",
        }

    nonce = secrets.token_urlsafe(10)
    return {
        "status": "input_required",
        "elicitation": {
            "mode": "url",
            "elicitationId": f"elicit_{nonce}",
            "url": f"https://auth.studio.example/connect/{parsed.provider}?session={nonce}",
            "message": f"Complete {parsed.provider} API key & payment verification securely in your browser.",
        },
    }
