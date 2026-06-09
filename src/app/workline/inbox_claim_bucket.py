"""Workline Inbox claim bucket key helper."""

from __future__ import annotations

from typing import Any


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_claim_bucket_key(
    *,
    session_id: int | str | None = None,
    device_id: int | str | None = None,
    workline_id: int | str | None = None,
    payload_json: dict[str, Any] | None = None,
) -> str:
    """Build the materialized claim conflict key for Inbox hot-queue fencing."""

    session_key = _non_empty_text(session_id)
    if session_key is not None:
        return f"session:{session_key}"

    device_key = _non_empty_text(device_id)
    if device_key is not None:
        return f"device:{device_key}"

    payload = payload_json or {}
    device_code = _non_empty_text(payload.get("device_code"))
    if device_code is not None:
        return f"device_code:{device_code}"

    location = _non_empty_text(payload.get("location"))
    if location is not None:
        return f"device_code:{location}"

    workline_key = _non_empty_text(workline_id)
    if workline_key is not None:
        return f"workline:{workline_key}"

    return "serial:unknown"


__all__ = ["build_claim_bucket_key"]
