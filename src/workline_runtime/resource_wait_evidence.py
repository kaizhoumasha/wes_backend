"""RESOURCE_WAIT evidence helper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from src.utils.value_normalization import optional_int, string_value

DIAGNOSTIC_KEY_MAX_LENGTH = 300
_DIAGNOSTIC_KEY_DIGEST_LENGTH = 16


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _positive_int(value: Any, default: int) -> int:
    resolved = optional_int(value)
    if resolved is None or resolved < 1:
        return default
    return resolved


def _fit_resource_wait_diagnostic_key(prefix: str, resource_key: str) -> str:
    raw_key = f"{prefix}{resource_key}"
    if len(raw_key) <= DIAGNOSTIC_KEY_MAX_LENGTH:
        return raw_key

    digest = sha256(raw_key.encode("utf-8")).hexdigest()[:_DIAGNOSTIC_KEY_DIGEST_LENGTH]
    digest_suffix = f":{digest}"
    resource_budget = DIAGNOSTIC_KEY_MAX_LENGTH - len(prefix) - len(digest_suffix)
    if resource_budget > 0:
        return f"{prefix}{resource_key[:resource_budget]}{digest_suffix}"

    head_budget = DIAGNOSTIC_KEY_MAX_LENGTH - len(digest_suffix)
    return f"{raw_key[:head_budget]}{digest_suffix}"


@dataclass(frozen=True)
class ResourceWaitEvidence:
    """Single source for RESOURCE_WAIT key, session context, and diagnostic evidence."""

    inbox_id: int
    resource_kind: str
    resource_key: str
    reason_code: str
    message: str
    first_seen_at: str
    last_seen_at: str
    wait_count: int
    session_id: int | None = None
    workline_id: int | None = None
    trace_id: str | None = None
    details: dict[str, Any] | None = None

    @classmethod
    def build(
        cls,
        *,
        inbox_id: int,
        resource_kind: str,
        resource_key: str,
        reason_code: str,
        message: str,
        occurred_at: datetime | str,
        session_id: int | None = None,
        workline_id: int | None = None,
        trace_id: str | None = None,
        details: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> ResourceWaitEvidence:
        existing_data = existing or {}
        now_iso = _iso(occurred_at) or string_value(occurred_at)
        first_seen_at = string_value(existing_data.get("first_seen_at"), now_iso)
        previous_count = _positive_int(existing_data.get("wait_count"), 0)
        return cls(
            inbox_id=inbox_id,
            resource_kind=resource_kind,
            resource_key=resource_key,
            reason_code=reason_code,
            message=message,
            first_seen_at=first_seen_at,
            last_seen_at=now_iso,
            wait_count=previous_count + 1,
            session_id=session_id,
            workline_id=workline_id,
            trace_id=trace_id,
            details=dict(details or {}),
        )

    @property
    def diagnostic_key(self) -> str:
        return _fit_resource_wait_diagnostic_key(
            f"RESOURCE_WAIT:{self.inbox_id}:",
            self.resource_key,
        )

    def to_session_context(self) -> dict[str, Any]:
        payload = self.to_diagnostic_evidence()
        payload.pop("details", None)
        return payload

    def to_diagnostic_evidence(self) -> dict[str, Any]:
        return {
            "inbox_id": self.inbox_id,
            "session_id": self.session_id,
            "workline_id": self.workline_id,
            "trace_id": self.trace_id,
            "resource_kind": self.resource_kind,
            "resource_key": self.resource_key,
            "reason_code": self.reason_code,
            "message": self.message,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "wait_count": self.wait_count,
            "details": dict(self.details or {}),
        }


__all__ = ["DIAGNOSTIC_KEY_MAX_LENGTH", "ResourceWaitEvidence"]
