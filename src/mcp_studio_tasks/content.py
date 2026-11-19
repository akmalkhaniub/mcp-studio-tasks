"""Completions (`completion/complete`), valid multimodal generators (PNG + WAV), resources & links (`REQ-ADV-04`)."""

from __future__ import annotations

import base64
import io
import math
import struct
import wave
import zlib
from typing import Any


DATASET_REGIONS = ["us-east-prod", "us-west-staging", "eu-west-compliance", "eu-central-audit", "ap-south-edge"]
REPORT_TEMPLATES = ["quarterly-latency-brief", "executive-cost-summary", "eu-compliance-audit", "eu-west-report"]


def complete_argument(*, ref_type: str, ref_name: str, arg_name: str, value_prefix: str) -> dict[str, Any]:
    """Context-aware completion handler (suggestions only — execution must still validate inputs)."""
    prefix = value_prefix.strip().lower()
    pool: list[str] = []
    if arg_name == "region":
        pool = DATASET_REGIONS
    elif arg_name == "template_name":
        pool = REPORT_TEMPLATES
    elif arg_name == "report_id":
        pool = ["RPT-1001", "RPT-1002", "RPT-2040"]

    matches = [item for item in pool if item.lower().startswith(prefix) or prefix in item.lower()]
    return {
        "completion": {
            "values": matches[:10],
            "total": len(matches),
            "hasMore": len(matches) > 10,
        },
        "validation_note": "Completions are UX hints only; server still validates inputs strictly.",
    }


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)


def generate_bar_chart_png_b64(width: int = 64, height: int = 32) -> str:
    """Synthesize a genuine valid PNG image bytes (base64) using stdlib zlib only."""
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # filter type 0
        for x in range(width):
            # Simple two-bar chart in indigo & emerald on slate canvas
            in_bar_1 = 8 <= x < 24 and y >= 8
            in_bar_2 = 34 <= x < 50 and y >= 4
            if in_bar_1:
                raw_rows.extend((79, 70, 229))
            elif in_bar_2:
                raw_rows.extend((16, 185, 129))
            else:
                raw_rows.extend((241, 245, 249))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw_rows)))
        + _png_chunk(b"IEND", b"")
    )
    return base64.b64encode(png_bytes).decode("ascii")


def generate_chime_wav_b64(duration_sec: float = 0.2, freq_hz: float = 523.25) -> str:
    """Synthesize a genuine valid WAV audio tone (base64) using stdlib wave/math."""
    sample_rate = 8000
    n_frames = int(sample_rate * duration_sec)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            val = int(12000 * math.sin(2.0 * math.pi * freq_hz * i / sample_rate))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def estimate_token_savings(*, embedded_bytes: int, link_json_bytes: int) -> dict[str, int]:
    """Approximate LLM context token difference (~4 chars per token) between embedded binary vs resource_link."""
    embedded_tokens = max(1, (embedded_bytes * 4 // 3) // 4)
    link_tokens = max(1, link_json_bytes // 4)
    return {
        "embedded_approx_tokens": embedded_tokens,
        "resource_link_approx_tokens": link_tokens,
        "tokens_saved": max(0, embedded_tokens - link_tokens),
    }


class ArtifactCatalog:
    """Stores downloadable/linkable binary & text artifacts exposed as MCP resources."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {
            "art-summary": {
                "uri": "studio://artifacts/art-summary",
                "name": "Quarterly Latency Summary Note",
                "mimeType": "application/json",
                "text": '{"region": "eu-west-compliance", "p95_ms": 142, "status": "healthy"}',
            },
            "art-hd-archive": {
                "uri": "studio://artifacts/art-hd-archive",
                "name": "Large Media Render Package (64KB simulated binary)",
                "mimeType": "application/octet-stream",
                "blob": base64.b64encode(b"STUDIO-ARCHIVE-" * 4000).decode("ascii"),
                "size": 60_000,
            },
        }

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": item["uri"],
                "name": item["name"],
                "mimeType": item["mimeType"],
                "size": item.get("size", len(item.get("text", ""))),
            }
            for item in self._items.values()
        ]

    def read_resource(self, uri: str) -> dict[str, Any]:
        for item in self._items.values():
            if item["uri"] == uri:
                return item
        raise KeyError(f"Resource '{uri}' not found.")

    def build_multimodal_packet(self, *, region: str, include_embedded_archive: bool = False) -> dict[str, Any]:
        """Return text, structuredContent, PNG image, WAV audio, resource_link, and optional embedded resource."""
        if region not in DATASET_REGIONS:
            raise ValueError(f"Unsupported region '{region}'. Valid regions: {DATASET_REGIONS}")

        chart_b64 = generate_bar_chart_png_b64()
        audio_b64 = generate_chime_wav_b64()
        archive = self._items["art-hd-archive"]
        summary_res = self._items["art-summary"]

        structured = {
            "region": region,
            "throughput_rps": 420,
            "p95_latency_ms": 142,
            "sla_status": "passing",
            "token_comparison": estimate_token_savings(embedded_bytes=archive["size"], link_json_bytes=160),
        }

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": f"Analytics packet ready for {region}: p95=142ms, throughput=420 rps.",
            },
            {
                "type": "image",
                "data": chart_b64,
                "mimeType": "image/png",
            },
            {
                "type": "audio",
                "data": audio_b64,
                "mimeType": "audio/wav",
            },
            {
                "type": "resource_link",
                "uri": archive["uri"],
                "name": archive["name"],
                "mimeType": archive["mimeType"],
                "size": archive["size"],
            },
        ]

        if include_embedded_archive:
            content.append(
                {
                    "type": "resource",
                    "resource": {
                        "uri": summary_res["uri"],
                        "mimeType": summary_res["mimeType"],
                        "text": summary_res["text"],
                    },
                }
            )

        return {"content": content, "structuredContent": structured, "isError": False}
