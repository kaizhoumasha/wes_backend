"""RuntimeInbox 通用终态写回与归档证据。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxService,
    runtime_inbox_service,
)
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import canonical_event_type, optional_int


class RuntimeInboxLeaseLostError(RuntimeError):
    """RuntimeInbox 的 lease fencing 未命中。"""


@dataclass(frozen=True, slots=True)
class RuntimeInboxWriteBackResult:
    """通用终态写回结果。"""

    processed: bool


def _require_fenced_update(updated: bool, *, action: str, inbox_id: int) -> None:
    if not updated:
        raise RuntimeInboxLeaseLostError(f"RuntimeInbox {inbox_id} lease lost before {action}")


def _session_status_value(session: Any) -> str | None:
    value = getattr(getattr(session, "status", None), "value", getattr(session, "status", None))
    return value if isinstance(value, str) and value else None


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


def _command_status_value(command: Any) -> str | None:
    value = getattr(getattr(command, "status", None), "value", getattr(command, "status", None))
    return value if isinstance(value, str) else None


def _is_late_or_duplicate_command_result_for_session(
    *, inbox: Any, payload: dict[str, Any], session: Any | None, command: Any | None
) -> bool:
    if _kind_value(inbox) != "COMMAND_RESULT" or session is None:
        return False
    callback_code = payload.get("command_code") or getattr(command, "command_code", None)
    awaiting_code = getattr(session, "awaiting_device_command_code", None)
    if not isinstance(callback_code, str) or not callback_code:
        return True
    if not isinstance(awaiting_code, str) or callback_code != awaiting_code:
        return True
    if command is None:
        return False
    terminal_command_statuses = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
    if _command_status_value(command) not in terminal_command_statuses:
        return False
    return _session_status_value(session) in {"COMPLETED", "FAILED", "CANCELLED"}


async def _record_late_command_result_archive_timeline(
    db: Any, *, session: Any, workline: Any, inbox: Any, command: Any, payload: dict[str, Any], reason: str
) -> None:
    from src.app.runtime.orchestration.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
        WorklineTimeline,
    )
    from src.app.runtime.orchestration.services.trace.timeline_sequence_service import add_timeline_with_sequence
    from src.utils.timezone import timezone
    from src.utils.value_normalization import optional_str, resolve_entity_id

    session_id = resolve_entity_id(session)
    workline_id = resolve_entity_id(workline) or optional_int(getattr(session, "workline_id", None))
    if session_id is None or workline_id is None:
        return
    timeline = WorklineTimeline(
        session_id=session_id,
        workline_id=workline_id,
        trace_id=optional_str(getattr(inbox, "trace_id", None)) or optional_str(getattr(session, "trace_id", None)),
        seq_no=0,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_PROCESSED,
        actor_type=TimelineActorType.ORCHESTRATOR,
        actor_code="runtime-inbox-writeback",
        status=TimelineStatus.SUCCESS,
        message="LATE_COMMAND_RESULT_ARCHIVED",
        payload_json={
            "reason": reason,
            "command_code": getattr(command, "command_code", None),
            "command_status": _command_status_value(command),
            "session_status": _session_status_value(session),
        },
        related_inbox_id=resolve_entity_id(inbox),
        related_command_id=resolve_entity_id(command),
    )
    try:
        _ = await add_timeline_with_sequence(db, timeline)
    except Exception as exc:
        logger.warning(f"迟到命令结果归档 timeline 记录失败: {exc}")


async def _record_duplicate_entry_archive_timeline(
    db: Any, *, session: Any, workline: Any, inbox: Any, payload: dict[str, Any], reason: str
) -> None:
    from src.app.runtime.orchestration.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
        WorklineTimeline,
    )
    from src.app.runtime.orchestration.services.trace.timeline_sequence_service import add_timeline_with_sequence
    from src.utils.timezone import timezone
    from src.utils.value_normalization import optional_str, resolve_entity_id

    session_id = resolve_entity_id(session)
    workline_id = resolve_entity_id(workline) or optional_int(getattr(session, "workline_id", None))
    if session_id is None or workline_id is None:
        return
    timeline = WorklineTimeline(
        session_id=session_id,
        workline_id=workline_id,
        trace_id=optional_str(getattr(inbox, "trace_id", None)) or optional_str(getattr(session, "trace_id", None)),
        seq_no=0,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_PROCESSED,
        actor_type=TimelineActorType.ORCHESTRATOR,
        actor_code="runtime-inbox-writeback",
        status=TimelineStatus.SUCCESS,
        message="DUPLICATE_ENTRY_ARCHIVED",
        payload_json={
            "reason": reason,
            "event_type": canonical_event_type(payload),
            "session_status": _session_status_value(session),
        },
        related_inbox_id=resolve_entity_id(inbox),
    )
    try:
        _ = await add_timeline_with_sequence(db, timeline)
    except Exception as exc:
        logger.warning(f"重复入口归档 timeline 记录失败: {exc}")


def _payload_for_inbox(inbox: Any) -> dict[str, Any]:
    return payload_dict(getattr(inbox, "payload_json", None))


def _build_runtime_session_updated_event_payload(*, workline_id: int | None, session_id: int | None) -> dict[str, Any]:
    return {
        "domain": "workline_runtime",
        "entity": "session",
        "action": "updated",
        "keys": {"workline_id": workline_id, "session_id": session_id},
    }


class RuntimeInboxWriteBackService:
    """仅负责 RuntimeInbox 通用终态的 fenced 写回。"""

    def __init__(self, *, inbox_service: RuntimeInboxService | None = None) -> None:
        self._inbox_service = inbox_service or runtime_inbox_service

    async def mark_processed(self, db: Any, *, inbox_id: int, lease_token: str) -> RuntimeInboxWriteBackResult:
        _require_fenced_update(
            await self._inbox_service.mark_processed(db, inbox_id=inbox_id, lease_token=lease_token),
            action="mark_processed",
            inbox_id=inbox_id,
        )
        return RuntimeInboxWriteBackResult(processed=True)


__all__ = [
    "RuntimeInboxLeaseLostError",
    "RuntimeInboxWriteBackResult",
    "RuntimeInboxWriteBackService",
]
