"""Runtime snapshot assembler (BC-02)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeSnapshotInput:
    """Runtime snapshot 输入行集合。"""

    session: Any
    correlation: Any | None = None
    timeline: tuple[Any, ...] = ()
    inbox: tuple[Any, ...] = ()
    hold: tuple[Any, ...] = ()
    pending_intent: tuple[Any, ...] = ()


class RuntimeSnapshotAssembler:
    """把 runtime/orchestration 行装配为 BC-02 视图。"""

    def assemble(self, data: RuntimeSnapshotInput) -> dict[str, Any]:
        session = data.session
        return {
            "state": {
                "execution_session_id": getattr(session, "id", None),
                "workline_id": getattr(session, "workline_id", None),
                "manifest_version": getattr(session, "manifest_version", None),
                "state": getattr(session, "state", None),
                "created_at": getattr(session, "created_at", None),
                "updated_at": getattr(session, "updated_at", None),
                "closed_at": getattr(session, "closed_at", None),
            },
            "timeline": [self._timeline_item(item) for item in sorted(data.timeline, key=self._timeline_sort_key)],
            "inbox": [self._inbox_item(item) for item in data.inbox],
            "hold": [self._hold_item(item) for item in data.hold],
            "pending_intent": [self._intent_item(item) for item in data.pending_intent],
            "correlation": self._correlation_item(data.correlation),
        }

    def _timeline_sort_key(self, item: Any) -> tuple[int, int]:
        return (int(getattr(item, "occurred_at", 0) or 0), int(getattr(item, "id", 0) or 0))

    def _timeline_item(self, item: Any) -> dict[str, Any]:
        return {
            "id": getattr(item, "id", None),
            "trace_id": getattr(item, "trace_id", None),
            "correlation_id": getattr(item, "correlation_id", None),
            "event_type": getattr(item, "event_type", None),
            "occurred_at": getattr(item, "occurred_at", None),
        }

    def _inbox_item(self, item: Any) -> dict[str, Any]:
        return {
            "id": getattr(item, "id", None),
            "correlation_id": getattr(item, "correlation_id", None),
            "provider_code": getattr(item, "provider_code", None),
            "event_type": getattr(item, "event_type", None),
            "source_event_id": getattr(item, "source_event_id", None),
            "status": getattr(item, "status", None),
        }

    def _hold_item(self, item: Any) -> dict[str, Any]:
        return {
            "id": getattr(item, "id", None),
            "correlation_id": getattr(item, "correlation_id", None),
            "reason": getattr(item, "reason", None),
            "hold_type": getattr(item, "hold_type", None),
            "scope_type": getattr(item, "scope_type", None),
            "scope_key": getattr(item, "scope_key", None),
            "resolved_at": getattr(item, "resolved_at", None),
            "allowed_next_effect_scope": getattr(item, "allowed_next_effect_scope", None),
        }

    def _intent_item(self, item: Any) -> dict[str, Any]:
        return {
            "id": getattr(item, "id", None),
            "correlation_id": getattr(item, "correlation_id", None),
            "provider_code": getattr(item, "provider_code", None),
            "target_domain": getattr(item, "target_domain", None),
            "target_action": getattr(item, "target_action", None),
            "idempotency_key": getattr(item, "idempotency_key", None),
            "dispatch_status": getattr(item, "dispatch_status", None),
        }

    def _correlation_item(self, item: Any | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "id": getattr(item, "id", None),
            "correlation_id": getattr(item, "correlation_id", None),
            "execution_session_id": getattr(item, "execution_session_id", None),
            "trace_id": getattr(item, "trace_id", None),
            "source_event_id": getattr(item, "source_event_id", None),
            "business_owner_key": getattr(item, "business_owner_key", None),
        }


runtime_snapshot_assembler = RuntimeSnapshotAssembler()
