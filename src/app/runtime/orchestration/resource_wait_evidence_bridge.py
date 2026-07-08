# runtime migration C5a 桥接:src.workline_runtime.resource_wait_evidence 的门面副本
# wlr 目录在阶段 3 整体删除时,本桥接与 wlr 副本合并 / 删除。

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


def _fit_resource_wait_diagnostic_key(prefix: str, subject_key: str) -> str:
    raw_key = f"{prefix}{subject_key}"
    if len(raw_key) <= DIAGNOSTIC_KEY_MAX_LENGTH:
        return raw_key

    digest = sha256(raw_key.encode("utf-8")).hexdigest()[:_DIAGNOSTIC_KEY_DIGEST_LENGTH]
    digest_suffix = f":{digest}"
    subject_budget = DIAGNOSTIC_KEY_MAX_LENGTH - len(prefix) - len(digest_suffix)
    if subject_budget > 0:
        return f"{prefix}{subject_key[:subject_budget]}{digest_suffix}"

    head_budget = DIAGNOSTIC_KEY_MAX_LENGTH - len(digest_suffix)
    return f"{raw_key[:head_budget]}{digest_suffix}"


@dataclass(frozen=True)
class ResourceWaitEvidence:
    """Single source for RESOURCE_WAIT key, session context, and diagnostic evidence."""

    inbox_id: int
    subject_type: str
    subject_key: str
    projection_type: str
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
        subject_type: str,
        subject_key: str,
        projection_type: str,
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
            subject_type=subject_type,
            subject_key=subject_key,
            projection_type=projection_type,
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
    def resource_kind(self) -> str:
        """旧诊断服务只读兼容入口；新 evidence 主体使用 subject_type。"""

        return self.subject_type

    @property
    def resource_key(self) -> str:
        """旧诊断服务只读兼容入口；新诊断 key 使用 subject_key。"""

        return self.subject_key

    @property
    def diagnostic_key(self) -> str:
        return _fit_resource_wait_diagnostic_key(
            f"RESOURCE_WAIT:{self.inbox_id}:{self.subject_type}:{self.projection_type}:",
            self.subject_key,
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
            "subject_type": self.subject_type,
            "subject_key": self.subject_key,
            "projection_type": self.projection_type,
            "reason_code": self.reason_code,
            "message": self.message,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "wait_count": self.wait_count,
            "details": dict(self.details or {}),
        }


__all__ = ["DIAGNOSTIC_KEY_MAX_LENGTH", "ResourceWaitEvidence"]
