"""WorkLine runtime reconciliation lifecycle service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_service import DeviceService
from src.app.rack.repositories import RackTaskRepository
from src.app.reconciliation.manager import (
    ReconciliationConflictInput,
    ReconciliationManager,
)
from src.app.runtime.orchestration.diagnostics import (
    ErrorCode,
    build_diagnostic_context,
    build_diagnostic_event,
)
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldType
from src.app.runtime.orchestration.models.runtime_hold_api import ResolveRuntimeHoldRequest
from src.app.runtime.orchestration.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationResolution,
    RuntimeReconciliationSourceKind,
    RuntimeReconciliationState,
    SessionStatus,
    WorklineSession,
)
from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.runtime.orchestration.repositories.runtime_hold_repository import (
    RuntimeHoldRepository,
)
from src.app.runtime.orchestration.repositories.runtime_hold_repository import (
    runtime_hold_repository as default_runtime_hold_repository,
)
from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
from src.app.runtime.orchestration.services.hold.runtime_hold_creation_service import (
    runtime_hold_creation_service as default_runtime_hold_creation_service,
)
from src.app.runtime.orchestration.services.hold.runtime_hold_release_service import (
    RuntimeHoldReleaseService,
)
from src.app.runtime.orchestration.services.hold.runtime_hold_release_service import (
    runtime_hold_release_service as default_runtime_hold_release_service,
)
from src.app.runtime.orchestration.services.trace.timeline_sequence_service import add_timeline_with_sequence
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.repositories import SystemOutboxRepository
from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.app.workline.services.diagnostic_service import workline_diagnostic_service
from src.core.logger import logger
from src.utils.timezone import timezone
from src.utils.value_normalization import as_dict, enum_str

if TYPE_CHECKING:
    from src.app.runtime.orchestration.models.inbox import WorklineInbox
    from src.app.sys.models import SystemOutbox


from src.app.runtime.orchestration.services.hold.runtime_hold_query_service import (
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


@dataclass(frozen=True, slots=True)
class TimerTimeoutReconciliationResult:
    """TIMER_TIMEOUT 业务判断结果；Inbox 终态由调用方负责。"""

    disposition: str
    session: WorklineSession | None


@dataclass(frozen=True, slots=True)
class _TimerTimeoutEvidence:
    """显式 timeout 参数转换出的稳定审计证据，不承担 Inbox 持久化职责。"""

    id: int
    session_id: int | None
    workline_id: int | None
    trace_id: str | None
    correlation_id: str | None
    payload_json: dict[str, Any]


def _timer_timeout_payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容 canonical envelope 与 Task 7 前 legacy flat timeout payload。"""

    data = payload.get("data")
    return dict(data) if isinstance(data, dict) else dict(payload)


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
        reconciliation_manager: ReconciliationManager | None = None,
        workline_status_projection_service: Any | None = None,
    ) -> None:
        self.session_repository = session_repository or WorklineSessionRepository()
        self.workline_repository = workline_repository or WorkLineRepository()
        self.system_outbox_repository = system_outbox_repository or SystemOutboxRepository()
        self.device_service = device_service or DeviceService()
        self.runtime_hold_creation_service = runtime_hold_creation_service or default_runtime_hold_creation_service
        self.runtime_hold_repository = runtime_hold_repository or default_runtime_hold_repository
        self.runtime_hold_release_service = runtime_hold_release_service or default_runtime_hold_release_service
        self.rack_task_repository = rack_task_repository or RackTaskRepository()
        self.reconciliation_manager = reconciliation_manager or ReconciliationManager()
        self.workline_status_projection_service = (
            workline_status_projection_service or workline_runtime_status_projection_service
        )

    async def activate_execution_deadline_after_ack(
        self,
        db: Any,
        *,
        command_id: int,
        ack_received_at: datetime,
    ) -> WorklineSession | None:
        """ACK 后按 session.current_wait_timeout_seconds 激活执行等待 deadline。"""

        command = await db.get(DeviceCommand, command_id)
        command_code = getattr(command, "command_code", None)
        if not isinstance(command_code, str) or not command_code:
            return None

        session = await self.session_repository.get_open_session_by_awaiting_device_command_code(db, command_code)
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
        session_id: int | None,
        inbox_id: int,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> TimerTimeoutReconciliationResult:
        """处理系统 TIMER_TIMEOUT 业务；不选择或写入任何 Inbox 终态。"""

        payload_data = _timer_timeout_payload_data(payload)
        if not isinstance(session_id, int):
            return TimerTimeoutReconciliationResult(disposition="SESSION_MISSING", session=None)

        evidence = _TimerTimeoutEvidence(
            id=inbox_id,
            session_id=session_id,
            workline_id=_payload_int(payload_data, "workline_id"),
            trace_id=trace_id,
            correlation_id=correlation_id,
            payload_json=dict(payload),
        )

        session = await self.session_repository.get_for_update(db, session_id)
        if session is None:
            return TimerTimeoutReconciliationResult(disposition="SESSION_MISSING", session=None)
        if session.status not in {
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
        }:
            return TimerTimeoutReconciliationResult(disposition="SESSION_NOT_WAITING", session=session)

        command = await self._load_timeout_command(db, session=session, payload=payload_data)
        if not self._timer_timeout_claim_matches(session=session, command=command, payload=payload_data):
            return TimerTimeoutReconciliationResult(disposition="EVIDENCE_STALE", session=session)

        now = timezone.now_for_db()
        claim_deadline_at = self._timer_timeout_deadline(session=session, payload=payload_data)
        claim_ack_received_at = getattr(command, "ack_received_at", None) or timezone.to_db_datetime(
            payload_data.get("ack_received_at")
        )
        from_status = enum_str(session.status)
        workline_session_lifecycle_service.manual_hold(session, occurred_at=now)
        session.reconciliation_state = RuntimeReconciliationState.PENDING
        session.reconciliation_reason = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
        session.reconciliation_source_kind = RuntimeReconciliationSourceKind.TIMER_TIMEOUT
        session.reconciliation_source_inbox_id = inbox_id
        session.reconciliation_command_id = _resolve_id(command)
        session.reconciliation_device_id = getattr(command, "device_id", None)
        session.reconciliation_wait_token = _payload_str(payload_data, "command_code")
        session.reconciliation_ack_received_at = claim_ack_received_at
        session.reconciliation_deadline_at = claim_deadline_at
        session.reconciliation_occurred_at = now
        session.reconciliation_late_evidence_received = False
        reconciliation_registration = await self._register_runtime_reconciliation_idempotent(
            db,
            session=session,
            conflict_kind=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
            reason="callback deadline expired",
            detected_at=now,
            inbox=evidence,
            command=command,
        )

        workline = await self.workline_repository.get_for_update(db, session.workline_id)
        if workline is not None:
            _ = await self.workline_status_projection_service.project_reconciling(
                db,
                workline_id=session.workline_id,
                occurred_at=now,
                reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
            )

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
            inbox=evidence,
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
                "reconciliation_registration": reconciliation_registration,
            },
            inbox=evidence,
            command=command,
            occurred_at=now,
        )
        await self._record_reconciliation_diagnostic(
            db,
            session=session,
            error_code=ErrorCode.CALLBACK_DEADLINE_EXPIRED,
            message="Callback deadline expired; physical result is unknown.",
            inbox=evidence,
            command=command,
            evidence={
                "deadline_at": _dt_key(session.reconciliation_deadline_at),
                "ack_received_at": _dt_key(session.reconciliation_ack_received_at),
                "wait_token": session.reconciliation_wait_token,
                "runtime_hold_id": runtime_hold_id,
                "reconciliation_registration": reconciliation_registration,
            },
        )

        await db.flush()
        return TimerTimeoutReconciliationResult(disposition="RECONCILED", session=session)

    async def handle_dispatch_ack_exhausted(
        self,
        db: Any,
        *,
        outbox: SystemOutbox,
        command: DeviceCommand | None,
        error_message: str = "OUTBOX_DISPATCH_FAILED",
    ) -> WorklineSession | None:
        """HTTP no-ACK retry exhausted 后进入通信 ACK 对账隔离。"""

        session_id = outbox.session_id
        if not isinstance(session_id, int):
            return None

        session = await self.session_repository.get_for_update(db, session_id)
        if session is None:
            return None
        now = timezone.now_for_db()
        hold_source_reason = self._dispatch_ack_hold_source_reason(error_message)
        reconciliation_registration = await self._register_runtime_reconciliation_idempotent(
            db,
            session=session,
            conflict_kind=hold_source_reason,
            reason=error_message,
            detected_at=now,
            outbox=outbox,
            command=command,
        )
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
                    session_id=session.id,
                )
            _ = await self.runtime_hold_creation_service.create_for_dispatch_ack_exhausted(
                db,
                session=session,
                outbox=outbox,
                command=command,
                source_reason=hold_source_reason,
            )
            _ = reconciliation_registration
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
                session_id=session.id,
            )

        workline = await self.workline_repository.get_for_update(db, session.workline_id)
        if workline is not None:
            _ = await self.workline_status_projection_service.project_reconciling(
                db,
                workline_id=session.workline_id,
                occurred_at=now,
                reason=RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
            )

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
                "reconciliation_registration": reconciliation_registration,
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
                "reconciliation_registration": reconciliation_registration,
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

        workline_id = outbox.workline_id
        if workline_id is None:
            return None

        owner = await self.session_repository.get_pending_reconciliation_owner_for_workline(db, workline_id)
        owner_id = _resolve_id(owner)
        if owner_id is None:
            return None
        outbox_id = _resolve_id(outbox)
        if outbox_id is None:
            return None
        active_holds = await self.runtime_hold_repository.get_active_blocking_by_workline(db, workline_id)
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
                blocked_workline_id=workline_id,
            )
        return await self.system_outbox_repository.mark_as_blocked_by_workline_state(
            db,
            outbox_id,
            owner_session_id=owner_id,
            reason=reason,
            blocked_device_id=getattr(owner, "reconciliation_device_id", None),
            blocked_workline_id=workline_id,
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
        session_id = _resolve_id(session)
        if session_id is None:
            return False
        locked_session = await self.session_repository.get_for_update(db, session_id)
        session = locked_session
        if (
            session is None
            or session.reconciliation_state != RuntimeReconciliationState.PENDING
            or session.reconciliation_command_id != command_id
        ):
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
        now = timezone.now_for_db()
        _ = await self._register_runtime_reconciliation_idempotent(
            db,
            session=session,
            conflict_kind=enum_str(session.reconciliation_reason) or "LATE_CALLBACK_EVIDENCE",
            reason="late callback recorded while runtime reconciliation is pending",
            detected_at=now,
            command=command,
            extra_evidence_refs=[f"late_callback:{evidence_key}"],
            source_ref_override=f"late_callback:{evidence_key}",
        )
        workline = await self.workline_repository.get_for_update(db, session.workline_id)
        if workline is not None:
            _ = await self.workline_status_projection_service.project_reconciling(
                db,
                workline_id=session.workline_id,
                occurred_at=now,
                reason=enum_str(session.reconciliation_reason) or "LATE_CALLBACK_EVIDENCE",
            )
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
            occurred_at=now,
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
        command_code = getattr(session, "awaiting_device_command_code", None)
        if not isinstance(command_code, str) or not command_code:
            candidate = payload.get("awaiting_device_command_code") or payload.get("command_code")
            command_code = candidate if isinstance(candidate, str) and candidate else None
        if not command_code:
            return None
        from src.app.device.repositories.command_repository import DeviceCommandRepository

        return await DeviceCommandRepository().get_by_command_code(db, command_code)

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

    async def _register_runtime_reconciliation_idempotent(
        self,
        db: Any,
        *,
        session: WorklineSession,
        conflict_kind: str,
        reason: str,
        detected_at: datetime,
        inbox: WorklineInbox | None = None,
        outbox: SystemOutbox | None = None,
        command: DeviceCommand | None = None,
        extra_evidence_refs: list[str] | None = None,
        source_ref_override: str | None = None,
    ) -> dict[str, Any] | None:
        """runtime reconciliation 生产入口登记 owner-scoped decision 前的幂等 claim。"""

        session_id = _resolve_id(session)
        if session_id is None:
            return None
        correlation_id = self._runtime_reconciliation_correlation_id(inbox=inbox, outbox=outbox, command=command)
        audit_correlation_id = correlation_id or self._runtime_reconciliation_fallback_correlation_id(command=command)

        owner_id = str(session_id)
        evidence_refs = self._runtime_reconciliation_evidence_refs(
            inbox=inbox,
            outbox=outbox,
            command=command,
            extra_refs=extra_evidence_refs,
        )
        source_ref = self._runtime_reconciliation_source_ref(
            session_id=session_id,
            inbox=inbox,
            outbox=outbox,
            source_ref_override=source_ref_override,
        )
        idempotency_key = f"runtime-reconciliation:{conflict_kind}:{source_ref}"
        business_owner_key = f"runtime:ExecutionSession:{owner_id}"
        request_hash = _canonical_json_hash(
            {
                "owner_domain": "runtime",
                "owner_kind": "ExecutionSession",
                "owner_id": owner_id,
                "conflict_kind": conflict_kind,
                "source_ref": source_ref,
                "evidence_refs": evidence_refs,
                "correlation_id": correlation_id,
            }
        )
        conflict = ReconciliationConflictInput(
            owner_domain="runtime",
            owner_kind="ExecutionSession",
            owner_id=owner_id,
            conflict_kind=conflict_kind,
            reason=reason,
            evidence_refs=evidence_refs,
            detected_at=detected_at,
            owner_snapshot={
                "session_status": enum_str(getattr(session, "status", None)),
                "reconciliation_state": enum_str(getattr(session, "reconciliation_state", None)),
                "workline_id": getattr(session, "workline_id", None),
                "trace_id": getattr(session, "trace_id", None),
            },
        )
        claim_result_text = "UNTRACKED_NO_CORRELATION"
        if correlation_id is not None:
            result = await self.reconciliation_manager.register_conflict_idempotent(
                db,
                conflict,
                provider_code="WES",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                execution_correlation_id=correlation_id,
                now_ms=int(timezone.now_utc().timestamp() * 1000),
                business_owner_key=business_owner_key,
            )
            claim_result_text = enum_str(result.claim_result)
            decision = result.decision
        else:
            decision = self.reconciliation_manager.register_conflict(conflict)
        audit_payload = {
            "provider_code": "WES",
            "operation_kind": "reconciliation",
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "correlation_id": audit_correlation_id,
            "business_owner_key": business_owner_key,
            "claim_result": claim_result_text,
            "decision": self._reconciliation_decision_payload(decision),
        }
        context = as_dict(getattr(session, "context_json", None))
        # latest registration audit snapshot; late callback evidence history lives separately.
        context["runtime_reconciliation_registration"] = audit_payload
        session.context_json = context
        return audit_payload

    def _runtime_reconciliation_correlation_id(
        self,
        *,
        inbox: WorklineInbox | None = None,
        outbox: SystemOutbox | None = None,
        command: DeviceCommand | None = None,
    ) -> str | None:
        for source in (command, outbox, inbox):
            value = _payload_str({"correlation_id": getattr(source, "correlation_id", None)}, "correlation_id")
            if value is not None:
                return value
            payload = as_dict(getattr(source, "payload_json", None))
            value = _payload_str(payload, "correlation_id") or _payload_str(payload, "execution_correlation_id")
            if value is not None:
                return value
        return None

    def _runtime_reconciliation_fallback_correlation_id(
        self,
        *,
        command: DeviceCommand | None = None,
    ) -> str | None:
        command_code = getattr(command, "command_code", None)
        if isinstance(command_code, str) and command_code:
            return f"command:{command_code}"
        command_id = _resolve_id(command)
        if command_id is not None:
            return f"command-id:{command_id}"
        return None

    def _runtime_reconciliation_evidence_refs(
        self,
        *,
        inbox: WorklineInbox | None = None,
        outbox: SystemOutbox | None = None,
        command: DeviceCommand | None = None,
        extra_refs: list[str] | None = None,
    ) -> list[str]:
        refs: list[str] = []
        inbox_id = _resolve_id(inbox)
        if inbox_id is not None:
            refs.append(f"inbox:{inbox_id}")
        outbox_id = _resolve_id(outbox)
        if outbox_id is not None:
            refs.append(f"outbox:{outbox_id}")
        command_id = _resolve_id(command)
        if command_id is not None:
            refs.append(f"command:{command_id}")
        else:
            command_code = getattr(command, "command_code", None)
            if isinstance(command_code, str) and command_code:
                refs.append(f"command:{command_code}")
        if extra_refs:
            refs.extend(extra_ref for extra_ref in extra_refs if isinstance(extra_ref, str) and extra_ref)
        return refs

    def _runtime_reconciliation_source_ref(
        self,
        *,
        session_id: int,
        inbox: WorklineInbox | None = None,
        outbox: SystemOutbox | None = None,
        source_ref_override: str | None = None,
    ) -> str:
        if source_ref_override is not None:
            return source_ref_override
        inbox_id = _resolve_id(inbox)
        if inbox_id is not None:
            return f"inbox:{inbox_id}"
        outbox_id = _resolve_id(outbox)
        if outbox_id is not None:
            return f"outbox:{outbox_id}"
        return f"session:{session_id}"

    def _reconciliation_decision_payload(self, decision: Any) -> dict[str, Any]:
        return {
            "owner_domain": decision.owner_domain,
            "owner_kind": decision.owner_kind,
            "owner_id": decision.owner_id,
            "conflict_kind": decision.conflict_kind,
            "reason": decision.reason,
            "evidence_refs": list(decision.evidence_refs),
            "detected_at": _dt_key(decision.detected_at),
            "status": decision.status,
            "severity": enum_str(decision.severity),
            "action": enum_str(decision.action),
            "runtime_hold_required": decision.runtime_hold_required,
            "allowed_next_effect_scope": dict(decision.allowed_next_effect_scope),
            "owner_snapshot": dict(decision.owner_snapshot) if decision.owner_snapshot is not None else None,
        }

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
        payload_command_code = payload.get("awaiting_device_command_code") or payload.get("command_code")
        if payload_command_code is not None and not isinstance(payload_command_code, str):
            return False
        session_command_code = getattr(session, "awaiting_device_command_code", None)
        if (
            isinstance(payload_command_code, str)
            and isinstance(session_command_code, str)
            and payload_command_code != session_command_code
        ):
            return False
        effective_command_code = session_command_code if isinstance(session_command_code, str) else payload_command_code
        command_code = getattr(command, "command_code", None)
        if effective_command_code is not None and command_code != effective_command_code:
            return False
        if effective_command_code is None:
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
        session.awaiting_device_command_code = None

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
    "TimerTimeoutReconciliationResult",
    "WorklineRuntimeReconciliationService",
    "workline_runtime_reconciliation_service",
]
