"""WorkLine runtime reconciliation lifecycle service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_service import DeviceService
from src.app.rack.repositories import RackTaskRepository
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.repositories import SystemOutboxRepository
from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service
from src.app.workline.models.runtime_hold import RuntimeHoldType
from src.app.workline.models.runtime_hold_api import ResolveRuntimeHoldRequest
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationResolution,
    RuntimeReconciliationSourceKind,
    RuntimeReconciliationState,
    SessionStatus,
    WorklineSession,
)
from src.app.workline.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.workline.repositories.runtime_hold_repository import (
    RuntimeHoldRepository,
)
from src.app.workline.repositories.runtime_hold_repository import (
    runtime_hold_repository as default_runtime_hold_repository,
)
from src.app.workline.repositories.session_repository import WorklineSessionRepository
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.app.workline.services.diagnostic_service import workline_diagnostic_service
from src.app.workline.services.inbox_service import inbox_service
from src.app.workline.services.runtime_hold_creation_service import (
    runtime_hold_creation_service as default_runtime_hold_creation_service,
)
from src.app.workline.services.runtime_hold_release_service import (
    RuntimeHoldReleaseService,
)
from src.app.workline.services.runtime_hold_release_service import (
    runtime_hold_release_service as default_runtime_hold_release_service,
)
from src.app.workline.services.timeline_sequence_service import add_timeline_with_sequence
from src.core.logger import logger
from src.utils.timezone import timezone
from src.utils.value_normalization import as_dict, enum_str
from src.workline_runtime.diagnostics import ErrorCode, build_diagnostic_context, build_diagnostic_event

if TYPE_CHECKING:
    from src.app.sys.models import SystemOutbox
    from src.app.workline.models.inbox import WorklineInbox


from src.app.workline.services.runtime_hold_query_service import (
    _CALLBACK_TIMEOUT_CHECKS,
    _DISPATCH_ACK_CHECKS,
)

_LATE_CALLBACK_EVIDENCE_REASONS = {
    RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED,
    RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED,
    RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED,
}
_TERMINAL_SESSION_STATUSES = {
    SessionStatus.COMPLETED.value,
    SessionStatus.FAILED.value,
    SessionStatus.CANCELLED.value,
}


def _dt_key(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _resolve_id(value: Any) -> int | None:
    raw_id = getattr(value, "id", None)
    return raw_id if isinstance(raw_id, int) else None


def _payload_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _payload_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _late_callback_evidence_key(command: DeviceCommand, callback_payload: dict[str, Any]) -> str:
    event_id = _payload_str(callback_payload, "event_id")
    if event_id is not None:
        return f"event_id:{event_id}"

    command_code = _payload_str(callback_payload, "command_code") or command.command_code
    result = str(callback_payload.get("result") or "")
    finish_time = str(callback_payload.get("finish_time") or "")
    payload_hash = _canonical_json_hash(callback_payload)
    return f"command_result:{command_code}:{result}:{finish_time}:{payload_hash}"


class WorklineRuntimeReconciliationService:
    """系统级 runtime reconciliation 唯一领域协调者。"""

    def __init__(
        self,
        *,
        session_repository: WorklineSessionRepository | None = None,
        workline_repository: WorkLineRepository | None = None,
        system_outbox_repository: SystemOutboxRepository | None = None,
        device_service: DeviceService | None = None,
        runtime_hold_creation_service: Any | None = None,
        runtime_hold_repository: RuntimeHoldRepository | None = None,
        runtime_hold_release_service: RuntimeHoldReleaseService | None = None,
        rack_task_repository: RackTaskRepository | None = None,
    ) -> None:
        self.session_repository = session_repository or WorklineSessionRepository()
        self.workline_repository = workline_repository or WorkLineRepository()
        self.system_outbox_repository = system_outbox_repository or SystemOutboxRepository()
        self.device_service = device_service or DeviceService()
        self.runtime_hold_creation_service = runtime_hold_creation_service or default_runtime_hold_creation_service
        self.runtime_hold_repository = runtime_hold_repository or default_runtime_hold_repository
        self.runtime_hold_release_service = runtime_hold_release_service or default_runtime_hold_release_service
        self.rack_task_repository = rack_task_repository or RackTaskRepository()

    async def activate_execution_deadline_after_ack(
        self,
        db: Any,
        *,
        command_id: int,
        ack_received_at: datetime,
    ) -> WorklineSession | None:
        """ACK 后按 session.current_wait_timeout_seconds 激活执行等待 deadline。"""

        session = await self.session_repository.get_open_session_by_awaiting_command_id(db, command_id)
        if session is None:
            return None
        if session.status != SessionStatus.WAITING_DEVICE_RESULT:
            return None
        if session.current_wait_type != "COMMAND_RESULT":
            return None
        if not session.current_wait_timeout_seconds:
            return None

        session.deadline_at = ack_received_at + timedelta(seconds=session.current_wait_timeout_seconds)
        await db.flush()
        return session

    async def handle_timer_timeout(
        self,
        db: Any,
        *,
        inbox: WorklineInbox,
        processor_token: str | None = None,
    ) -> WorklineSession | None:
        """处理系统 TIMER_TIMEOUT：进入 Callback deadline runtime reconciliation。"""

        payload = as_dict(inbox.payload_json)
        inbox_id = _resolve_id(inbox)
        if inbox_id is None:
            logger.warning("TIMER_TIMEOUT inbox 缺少持久化 id，跳过 runtime reconciliation")
            return None

        session_id = inbox.session_id if isinstance(inbox.session_id, int) else payload.get("session_id")
        if not isinstance(session_id, int):
            _ = await inbox_service.mark_as_processed(db, inbox_id, processor_token=processor_token, auto_commit=False)
            return None

        session = await self.session_repository.get_for_update(db, session_id)
        if session is None or session.status not in {
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
        }:
            _ = await inbox_service.mark_as_processed(db, inbox_id, processor_token=processor_token, auto_commit=False)
            return session

        command = await self._load_timeout_command(db, session=session, payload=payload)
        if not self._timer_timeout_claim_matches(session=session, command=command, payload=payload):
            _ = await inbox_service.mark_as_processed(db, inbox_id, processor_token=processor_token, auto_commit=False)
            return session

        now = timezone.now_for_db()
        claim_deadline_at = self._timer_timeout_deadline(session=session, payload=payload)
        claim_ack_received_at = getattr(command, "ack_received_at", None) or timezone.to_db_datetime(
            payload.get("ack_received_at")
        )
        from_status = enum_str(session.status)
        workline_session_lifecycle_service.manual_hold(session, occurred_at=now)
        session.reconciliation_state = RuntimeReconciliationState.PENDING
        session.reconciliation_reason = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
        session.reconciliation_source_kind = RuntimeReconciliationSourceKind.TIMER_TIMEOUT
        session.reconciliation_source_inbox_id = inbox_id
        session.reconciliation_command_id = _resolve_id(command)
        session.reconciliation_device_id = getattr(command, "device_id", None)
        session.reconciliation_wait_token = _payload_str(payload, "command_code")
        session.reconciliation_ack_received_at = claim_ack_received_at
        session.reconciliation_deadline_at = claim_deadline_at
        session.reconciliation_occurred_at = now
        session.reconciliation_late_evidence_received = False

        workline = await self.workline_repository.get_for_update(db, session.workline_id)
        if workline is not None:
            workline.runtime_status = WorkLineRuntimeStatus.RECONCILING
            workline.stopped_at = workline.stopped_at or now
            workline.stopped_reason = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value

        device_id = getattr(command, "device_id", None)
        if isinstance(device_id, int):
            _ = await self.device_service.mark_callback_deadline_expired(db, device_id=device_id, auto_commit=False)

        if session.id is not None:
            _ = await self.system_outbox_repository.cancel_active_by_session(
                db,
                session_id=session.id,
                reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
            )
            _ = await self.rack_task_repository.cancel_active_by_material_session(
                db,
                material_session_id=session.id,
                reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
            )

        runtime_hold = await self.runtime_hold_creation_service.create_for_callback_deadline_expired(
            db,
            session=session,
            inbox=inbox,
            command=command,
        )
        runtime_hold_id = _resolve_id(runtime_hold)

        await self._append_reconciliation_timeline(
            db,
            session=session,
            stage=TimelineStage.TIMEOUT,
            action_type=TimelineActionType.WAIT_TIMEOUT,
            status=TimelineStatus.FAILED,
            from_status=from_status,
            to_status=SessionStatus.MANUAL_HOLD.value,
            message="Callback deadline expired; runtime reconciliation started.",
            payload={
                "reason": RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
                "deadline_at": _dt_key(session.reconciliation_deadline_at),
                "ack_received_at": _dt_key(session.reconciliation_ack_received_at),
                "wait_token": session.reconciliation_wait_token,
                "runtime_hold_id": runtime_hold_id,
            },
            inbox=inbox,
            command=command,
            occurred_at=now,
        )
        await self._record_reconciliation_diagnostic(
            db,
            session=session,
            error_code=ErrorCode.CALLBACK_DEADLINE_EXPIRED,
            message="Callback deadline expired; physical result is unknown.",
            inbox=inbox,
            command=command,
            evidence={
                "deadline_at": _dt_key(session.reconciliation_deadline_at),
                "ack_received_at": _dt_key(session.reconciliation_ack_received_at),
                "wait_token": session.reconciliation_wait_token,
                "runtime_hold_id": runtime_hold_id,
            },
        )

        _ = await inbox_service.mark_as_processed(db, inbox_id, processor_token=processor_token, auto_commit=False)
        await db.flush()
        return session

    async def handle_dispatch_ack_exhausted(
        self,
        db: Any,
        *,
        outbox: SystemOutbox,
        command: DeviceCommand | None,
        error_message: str = "OUTBOX_DISPATCH_FAILED",
    ) -> WorklineSession | None:
        """HTTP no-ACK retry exhausted 后进入通信 ACK 对账隔离。"""

        session_id = outbox.session_id or getattr(command, "session_id_int", None)
        if not isinstance(session_id, int):
            return None

        session = await self.session_repository.get_for_update(db, session_id)
        if session is None:
            return None
        now = timezone.now_for_db()
        hold_source_reason = self._dispatch_ack_hold_source_reason(error_message)
        outbox.status = SystemOutboxStatus.FAILED
        outbox.last_error = error_message
        outbox.next_retry_at = None
        outbox.finished_at = now
        outbox.blocked_by_runtime_hold_id = None
        outbox.blocked_by_reconciliation_session_id = None
        outbox.blocked_device_id = None
        outbox.blocked_workline_id = None
        outbox.blocked_reason = None

        if session.reconciliation_state == RuntimeReconciliationState.PENDING:
            if command is not None and enum_str(command.status) in {
                CommandStatus.PENDING.value,
                CommandStatus.SENT.value,
            }:
                command.status = CommandStatus.FAILED
                command.completed_at = command.completed_at or now
                command.error_detail = {
                    **as_dict(command.error_detail),
                    "error_code": RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
                    "error_message": error_message,
                    "outbox_id": _resolve_id(outbox),
                }
                from src.app.sys.services.event_stream_service import (
                    defer_command_status_changed_event,
                )

                defer_command_status_changed_event(
                    db,
                    command=command,
                    action="updated",
                    workline_id=getattr(command, "workline_id", None),
                    device_id=getattr(command, "device_id", None),
                    session_id=getattr(command, "session_id_int", None),
                )
            _ = await self.runtime_hold_creation_service.create_for_dispatch_ack_exhausted(
                db,
                session=session,
                outbox=outbox,
                command=command,
                source_reason=hold_source_reason,
            )
            await db.flush()
            return session

        from_status = enum_str(getattr(session, "status", None)) or SessionStatus.WAITING_DEVICE_RESULT.value
        if from_status not in _TERMINAL_SESSION_STATUSES:
            workline_session_lifecycle_service.manual_hold(session, occurred_at=now)
        to_status = enum_str(getattr(session, "status", None)) or from_status
        session.reconciliation_state = RuntimeReconciliationState.PENDING
        session.reconciliation_reason = RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED
        session.reconciliation_source_kind = RuntimeReconciliationSourceKind.DISPATCH_ACK_EXHAUSTED
        session.reconciliation_source_outbox_id = _resolve_id(outbox)
        session.reconciliation_command_id = _resolve_id(command)
        session.reconciliation_device_id = getattr(command, "device_id", None)
        session.reconciliation_wait_token = getattr(command, "command_code", None)
        session.reconciliation_occurred_at = now
        session.reconciliation_late_evidence_received = False

        if command is not None:
            command.status = CommandStatus.FAILED
            command.completed_at = command.completed_at or now
            command.error_detail = {
                **as_dict(command.error_detail),
                "error_code": RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
                "error_message": error_message,
                "outbox_id": _resolve_id(outbox),
            }
            from src.app.sys.services.event_stream_service import defer_command_status_changed_event

            defer_command_status_changed_event(
                db,
                command=command,
                action="updated",
                workline_id=getattr(command, "workline_id", None),
                device_id=getattr(command, "device_id", None),
                session_id=getattr(command, "session_id_int", None),
            )

        workline = await self.workline_repository.get_for_update(db, session.workline_id)
        if workline is not None:
            workline.runtime_status = WorkLineRuntimeStatus.RECONCILING
            workline.stopped_at = workline.stopped_at or now
            workline.stopped_reason = RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value

        device_id = getattr(command, "device_id", None)
        if isinstance(device_id, int):
            _ = await self.device_service.mark_dispatch_ack_exhausted(db, device_id=device_id, auto_commit=False)

        runtime_hold = await self.runtime_hold_creation_service.create_for_dispatch_ack_exhausted(
            db,
            session=session,
            outbox=outbox,
            command=command,
            source_reason=hold_source_reason,
        )
        runtime_hold_id = _resolve_id(runtime_hold)

        await self._append_reconciliation_timeline(
            db,
            session=session,
            stage=TimelineStage.FAIL,
            action_type=TimelineActionType.ERROR_OCCURRED,
            status=TimelineStatus.FAILED,
            from_status=from_status,
            to_status=to_status,
            message="Command ACK exhausted; runtime reconciliation started.",
            payload={
                "reason": RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
                "error_message": error_message,
                "outbox_id": _resolve_id(outbox),
                "runtime_hold_id": runtime_hold_id,
            },
            outbox=outbox,
            command=command,
            occurred_at=now,
        )
        await self._record_reconciliation_diagnostic(
            db,
            session=session,
            error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
            message=error_message,
            outbox=outbox,
            command=command,
            evidence={
                "outbox_id": _resolve_id(outbox),
                "command_id": _resolve_id(command),
                "error_message": error_message,
                "runtime_hold_id": runtime_hold_id,
            },
        )

        await db.flush()
        return session

    async def park_outbox_for_reconciliation(
        self,
        db: Any,
        *,
        outbox: SystemOutbox,
        reason: str,
    ) -> SystemOutbox | None:
        """WorkLine RECONCILING 时，将尚未 ACK 的 outbox 暂停为 BLOCKED_RESOURCE。"""

        owner = await self.session_repository.get_pending_reconciliation_owner_for_workline(db, outbox.workline_id)
        owner_id = _resolve_id(owner)
        if owner_id is None:
            return None
        outbox_id = _resolve_id(outbox)
        if outbox_id is None:
            return None
        active_holds = await self.runtime_hold_repository.get_active_blocking_by_workline(db, outbox.workline_id)
        runtime_hold = next(
            (
                hold
                for hold in active_holds
                if hold.session_id == owner_id and hold.hold_type == RuntimeHoldType.RUNTIME_RECONCILIATION
            ),
            None,
        )
        runtime_hold_id = _resolve_id(runtime_hold)
        if runtime_hold_id is not None:
            return await self.system_outbox_repository.block_by_runtime_hold(
                db,
                outbox_id,
                runtime_hold_id=runtime_hold_id,
                owner_session_id=owner_id,
                reason=reason,
                blocked_device_id=getattr(owner, "reconciliation_device_id", None),
                blocked_workline_id=outbox.workline_id,
            )
        return await self.system_outbox_repository.mark_as_blocked_by_workline_state(
            db,
            outbox_id,
            owner_session_id=owner_id,
            reason=reason,
            blocked_device_id=getattr(owner, "reconciliation_device_id", None),
            blocked_workline_id=outbox.workline_id,
        )

    async def record_late_callback_if_pending(
        self,
        db: Any,
        *,
        command: DeviceCommand,
        callback_payload: dict[str, Any],
    ) -> bool:
        """迟到 callback 只记录证据，不自动恢复 pending reconciliation。"""

        command_id = _resolve_id(command)
        if command_id is None:
            return False

        session = await self.session_repository.get_pending_reconciliation_by_command_id(db, command_id)
        if session is None:
            return False
        if session.reconciliation_reason not in _LATE_CALLBACK_EVIDENCE_REASONS:
            return False

        context = as_dict(session.context_json)
        evidence = context.get("runtime_reconciliation_late_callback_evidence")
        evidence_items: list[dict[str, Any]] = []
        if isinstance(evidence, list):
            evidence_items = [cast("dict[str, Any]", item) for item in evidence if isinstance(item, dict)]
        evidence_key = _late_callback_evidence_key(command, callback_payload)
        if any(item.get("evidence_key") == evidence_key for item in evidence_items):
            return True

        evidence_item = {
            "evidence_key": evidence_key,
            "recorded_at": timezone.now_for_db().isoformat(),
            "command_id": command_id,
            "command_code": command.command_code,
            "command_status": enum_str(command.status),
            "payload": callback_payload,
        }
        evidence_items.append(evidence_item)
        context["runtime_reconciliation_late_callback_evidence"] = evidence_items
        session.context_json = context
        session.reconciliation_late_evidence_received = True
        await self._append_reconciliation_timeline(
            db,
            session=session,
            stage=TimelineStage.CALLBACK,
            action_type=TimelineActionType.EVENT_RECEIVED,
            status=TimelineStatus.PENDING,
            actor_type=TimelineActorType.DEVICE,
            actor_code=str(getattr(command, "device_id", "")) or None,
            message="Late callback recorded as runtime reconciliation evidence.",
            payload=evidence_item,
            command=command,
        )
        await db.flush()
        return True

    async def resolve_runtime_reconciliation(
        self,
        db: Any,
        *,
        session_id: int,
        resolution: RuntimeReconciliationResolution,
        checks: dict[str, bool],
        operator_note: str,
        confirmed_at: datetime,
        operator_id: int,
        result_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """兼容入口：校验旧 reconciliation 请求，然后委托 RuntimeHoldReleaseService。"""

        session = await self.session_repository.get_for_update(db, session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id}")
        if session.status != SessionStatus.MANUAL_HOLD:
            raise ValueError(f"当前会话状态不允许解除对账: session_id={session_id}, status={enum_str(session.status)}")
        if session.reconciliation_state != RuntimeReconciliationState.PENDING:
            raise ValueError(f"当前会话没有 pending runtime reconciliation: session_id={session_id}")

        self._validate_checks(session.reconciliation_reason, checks)

        now = timezone.now_for_db()
        confirmed_at_for_db = timezone.to_db_datetime(confirmed_at) or confirmed_at
        active_holds = await self.runtime_hold_repository.get_active_blocking_by_workline(db, session.workline_id)
        runtime_hold = next(
            (
                hold
                for hold in active_holds
                if hold.session_id == session_id and hold.hold_type == RuntimeHoldType.RUNTIME_RECONCILIATION
            ),
            None,
        )
        runtime_hold_id = _resolve_id(runtime_hold)
        if runtime_hold is None or runtime_hold_id is None:
            raise ValueError(f"未找到 active RuntimeHold: session_id={session_id}")

        release_request = ResolveRuntimeHoldRequest(
            resolution=resolution.value,
            checks=checks,
            operator_note=operator_note,
            material_disposition="CONTINUE",
            result_payload=result_payload,
            hold_version=runtime_hold.version,
            latest_evidence_hash=self.runtime_hold_release_service.build_latest_evidence_hash(
                runtime_hold,
                session=session,
            ),
        )
        result = await self.runtime_hold_release_service.resolve_hold(
            db,
            runtime_hold_id,
            release_request,
            operator_id,
        )
        command = await self._load_reconciliation_command(db, session)

        await self._append_reconciliation_timeline(
            db,
            session=session,
            stage=TimelineStage.MANUAL,
            action_type=TimelineActionType.MANUAL_RESUME,
            status=TimelineStatus.SUCCESS,
            actor_type=TimelineActorType.MANUAL_OPERATOR,
            actor_code=str(operator_id),
            from_status=SessionStatus.MANUAL_HOLD.value,
            to_status=resolution.value,
            message="Runtime reconciliation resolved by operator.",
            payload={
                "resolution": resolution.value,
                "operator_id": operator_id,
                "operator_note": operator_note,
                "checks": checks,
                "confirmed_at": confirmed_at_for_db.isoformat(),
                "runtime_hold_id": runtime_hold_id,
                "released_outbox_count": result["released_outbox_count"],
                "remaining_pending_reconciliations": result["remaining_active_blocking_holds"],
            },
            command=command,
            occurred_at=now,
        )

        await db.flush()
        return {
            "session_id": session_id,
            "resolution": resolution.value,
            "runtime_hold_id": runtime_hold_id,
            "released_outbox_count": result["released_outbox_count"],
            "remaining_pending_reconciliations": result["remaining_active_blocking_holds"],
        }

    def assert_not_pending_reconciliation(self, session: WorklineSession) -> None:
        if session.reconciliation_state == RuntimeReconciliationState.PENDING:
            raise ValueError(
                "Session 正在 runtime reconciliation 对账中，唯一恢复入口是 reconciliation resolve API: "
                f"session_id={session.id}"
            )

    async def _load_timeout_command(
        self,
        db: Any,
        *,
        session: WorklineSession,
        payload: dict[str, Any],
    ) -> DeviceCommand | None:
        command_id = session.awaiting_command_id
        if not isinstance(command_id, int):
            command_id = _payload_int(payload, "awaiting_command_id")
        if not isinstance(command_id, int):
            return None
        return await db.get(DeviceCommand, command_id)

    async def _load_reconciliation_command(self, db: Any, session: WorklineSession) -> DeviceCommand | None:
        command_id = session.reconciliation_command_id
        if not isinstance(command_id, int):
            return None
        return await db.get(DeviceCommand, command_id)

    async def _append_reconciliation_timeline(
        self,
        db: Any,
        *,
        session: WorklineSession,
        stage: TimelineStage,
        action_type: TimelineActionType,
        status: TimelineStatus,
        message: str,
        payload: dict[str, Any] | None = None,
        actor_type: TimelineActorType = TimelineActorType.ORCHESTRATOR,
        actor_code: str | None = "runtime_reconciliation",
        from_status: str | None = None,
        to_status: str | None = None,
        inbox: WorklineInbox | None = None,
        outbox: SystemOutbox | None = None,
        command: DeviceCommand | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        session_id = _resolve_id(session)
        if session_id is None:
            return

        timeline = WorklineTimeline(
            session_id=session_id,
            workline_id=session.workline_id,
            trace_id=getattr(session, "trace_id", None),
            seq_no=0,
            occurred_at=occurred_at or timezone.now_for_db(),
            stage=stage,
            action_type=action_type,
            actor_type=actor_type,
            actor_code=actor_code,
            from_status=from_status,
            to_status=to_status,
            status=status,
            failure_domain="runtime_reconciliation" if status == TimelineStatus.FAILED else None,
            message=message,
            payload_json=cast("dict[str, object] | None", payload),
            related_inbox_id=_resolve_id(inbox),
            related_command_id=_resolve_id(command),
        )
        _ = outbox
        _ = await add_timeline_with_sequence(db, timeline)

    async def _record_reconciliation_diagnostic(
        self,
        db: Any,
        *,
        session: WorklineSession,
        error_code: ErrorCode,
        message: str,
        inbox: WorklineInbox | None = None,
        outbox: SystemOutbox | None = None,
        command: DeviceCommand | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        event = build_diagnostic_event(
            error_code=error_code,
            context=build_diagnostic_context(
                session=session,
                inbox=inbox,
                outbox=outbox,
                command=command,
                extra={"source": "runtime_reconciliation"},
            ),
            message=message,
            technical_summary=message,
        )
        _ = await workline_diagnostic_service.record_event(
            db,
            event=event,
            evidence=evidence or {},
            auto_commit=False,
        )

    def _timer_timeout_claim_matches(
        self,
        *,
        session: WorklineSession,
        command: DeviceCommand | None,
        payload: dict[str, Any],
    ) -> bool:
        deadline_at = self._timer_timeout_deadline(session=session, payload=payload)
        if deadline_at is None or deadline_at > timezone.now_for_db():
            return False
        payload_deadline = timezone.to_db_datetime(payload.get("deadline_at"))
        if session.deadline_at is not None and payload_deadline is not None and payload_deadline != session.deadline_at:
            return False
        payload_command_id = _payload_int(payload, "awaiting_command_id")
        if (
            isinstance(payload_command_id, int)
            and payload_command_id != session.awaiting_command_id
            and session.awaiting_command_id is not None
        ):
            return False
        effective_command_id = (
            session.awaiting_command_id if isinstance(session.awaiting_command_id, int) else payload_command_id
        )
        command_id = _resolve_id(command)
        if effective_command_id is not None and command_id != effective_command_id:
            return False
        if effective_command_id is None:
            return session.status == SessionStatus.WAITING_EXTERNAL
        return (
            command is not None
            and enum_str(command.status) == CommandStatus.ACK_RECEIVED.value
            and (
                command.ack_received_at is not None
                or timezone.to_db_datetime(payload.get("ack_received_at")) is not None
            )
        )

    def _timer_timeout_deadline(self, *, session: WorklineSession, payload: dict[str, Any]) -> datetime | None:
        return session.deadline_at or timezone.to_db_datetime(payload.get("deadline_at"))

    def _clear_wait(self, session: WorklineSession) -> None:
        session.current_wait_type = None
        session.waiting_since = None
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_command_id = None

    def _dispatch_ack_hold_source_reason(self, error_message: str) -> str:
        if error_message == RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value:
            return RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value
        return RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value

    def _validate_checks(
        self,
        reason: RuntimeReconciliationReason | None,
        checks: dict[str, bool],
    ) -> None:
        required = _CALLBACK_TIMEOUT_CHECKS
        if reason in {
            RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED,
            RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED,
        }:
            required = _DISPATCH_ACK_CHECKS
        missing = sorted(item for item in required if checks.get(item) is not True)
        if missing:
            raise ValueError(f"runtime reconciliation checklist 未全部确认: {', '.join(missing)}")

    def _device_error_for_reason(self, reason: RuntimeReconciliationReason | None) -> str | None:
        if reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED:
            return RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value
        if reason in {
            RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED,
            RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED,
        }:
            return RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value
        logger.warning(f"未知 runtime reconciliation reason，跳过设备错误清除: {reason}")
        return None


workline_runtime_reconciliation_service = WorklineRuntimeReconciliationService()


__all__ = [
    "WorklineRuntimeReconciliationService",
    "workline_runtime_reconciliation_service",
]
