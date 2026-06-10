"""Workline Inbox claim bucket key helper."""

from __future__ import annotations

from hashlib import md5
from typing import Any

CLAIM_BUCKET_KEY_MAX_LENGTH = 200
_CLAIM_BUCKET_KEY_DIGEST_LENGTH = 16
_CLAIM_BUCKET_KEY_HEAD_LENGTH = CLAIM_BUCKET_KEY_MAX_LENGTH - _CLAIM_BUCKET_KEY_DIGEST_LENGTH - 1


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fit_claim_bucket_key(raw_key: str) -> str:
    if len(raw_key) <= CLAIM_BUCKET_KEY_MAX_LENGTH:
        return raw_key

    # 非安全用途：与 PostgreSQL md5() 迁移回填合同保持一致。
    digest = md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:_CLAIM_BUCKET_KEY_DIGEST_LENGTH]
    return f"{raw_key[:_CLAIM_BUCKET_KEY_HEAD_LENGTH]}:{digest}"


def _bucket_key(prefix: str, value: str) -> str:
    return _fit_claim_bucket_key(f"{prefix}:{value}")


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
        return _bucket_key("session", session_key)

    device_key = _non_empty_text(device_id)
    if device_key is not None:
        return _bucket_key("device", device_key)

    payload = payload_json or {}
    device_code = _non_empty_text(payload.get("device_code"))
    if device_code is not None:
        return _bucket_key("device_code", device_code)

    location = _non_empty_text(payload.get("location"))
    if location is not None:
        return _bucket_key("device_code", location)

    workline_key = _non_empty_text(workline_id)
    if workline_key is not None:
        return _bucket_key("workline", workline_key)

    return "serial:unknown"


def build_claim_bucket_key_for_update(*, current: Any, data: dict[str, Any]) -> str:
    """Build claim bucket key from current Inbox values plus pending updates."""

    def value_for(field_name: str) -> Any:
        return data[field_name] if field_name in data else getattr(current, field_name, None)

    return build_claim_bucket_key(
        session_id=value_for("session_id"),
        device_id=value_for("device_id"),
        workline_id=value_for("workline_id"),
        payload_json=value_for("payload_json"),
    )


__all__ = ["CLAIM_BUCKET_KEY_MAX_LENGTH", "build_claim_bucket_key", "build_claim_bucket_key_for_update"]
