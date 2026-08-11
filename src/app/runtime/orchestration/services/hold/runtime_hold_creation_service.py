"""Runtime Hold creation service."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldType
from src.app.runtime.orchestration.repositories.runtime_hold_repository import (
    RuntimeHoldRepository,
    runtime_hold_repository,
)
from src.app.runtime.orchestration.repository_wiring import workline_repository as default_workline_repository
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusProjectionService,
    workline_runtime_status_projection_service,
)
from src.utils.value_normalization import dict_attr, optional_int_attr, optional_str_attr, required_int_attr

if TYPE_CHECKING:
    from collections.abc import Mapping


def _dt_key(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


class RuntimeHoldCreationService:
    """创建/复用 RuntimeHold，不负责 release。"""

    def __init__(
        self,
        *,
        repository: RuntimeHoldRepository | None = None,
        workline_repository: Any | None = None,
        workline_status_projection_service: WorkLineRuntimeStatusProjectionService | None = None,
    ) -> None:
        self.repository = repository or runtime_hold_repository
        self.workline_repository = workline_repository or default_workline_repository
        self.workline_status_projection_service = (
            workline_status_projection_service or workline_runtime_status_projection_service
        )

    async def create_for_callback_deadline_expired(
        self,
        db: Any,
        *,
        session: Any,
        inbox: Any,
        source_inbox_id: int | None,
        command: Any | None = None,
    ) -> RuntimeHold:
        """Callback deadline expired 时幂等创建 RuntimeHold。"""

        session_id = required_int_attr(session, "id")
        inbox_id = required_int_attr(inbox, "id")
        command_id = (
            optional_int_attr(command, "id")
            if command is not None
            else optional_int_attr(session, "reconciliation_command_id")
        )
        device_id = (
            optional_int_attr(command, "device_id")
            if command is not None
            else optional_int_attr(session, "reconciliation_device_id")
        )
        source_reason = "CALLBACK_DEADLINE_EXPIRED"
        source_idempotency_key = f"callback-timeout:runtime-inbox:{session_id}:{inbox_id}"
        return await self.repository.create_open_hold(
            db,
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            workline_id=required_int_attr(session, "workline_id"),
            session_id=session_id,
            trace_id=optional_str_attr(session, "trace_id"),
            source_kind="TIMER_TIMEOUT",
            source_reason=source_reason,
            source_idempotency_key=source_idempotency_key,
            source_inbox_id=source_inbox_id,
            source_command_id=command_id,
            source_device_id=device_id,
            evidence_snapshot_json={
                "session_id": session_id,
                "inbox_id": inbox_id,
                "inbox_store": "runtime_inbox",
                "command_id": command_id,
                "command_code": optional_str_attr(command, "command_code") if command is not None else None,
                "device_id": device_id,
                "deadline_at": _dt_key(getattr(session, "reconciliation_deadline_at", None)),
                "wait_token": optional_str_attr(session, "reconciliation_wait_token"),
                "inbox_payload": dict_attr(inbox, "payload_json"),
                "reason": source_reason,
            },
        )

    async def create_for_dispatch_ack_exhausted(
        self,
        db: Any,
        *,
        session: Any,
        outbox: Any,
        command: Any | None,
        source_reason: str = "COMMAND_ACK_EXHAUSTED",
    ) -> RuntimeHold:
        """HTTP no-ACK retry exhausted 时幂等创建 RuntimeHold。"""

        session_id = required_int_attr(session, "id")
        outbox_id = required_int_attr(outbox, "id")
        command_id = optional_int_attr(command, "id") if command is not None else None
        device_id = optional_int_attr(command, "device_id") if command is not None else None
        command_key = str(command_id) if command_id is not None else "no-command"
        return await self.repository.create_open_hold(
            db,
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            workline_id=required_int_attr(session, "workline_id"),
            session_id=session_id,
            trace_id=optional_str_attr(session, "trace_id"),
            source_kind="DISPATCH_ACK_EXHAUSTED",
            source_reason=source_reason,
            source_idempotency_key=f"dispatch-ack-exhausted:{outbox_id}:{command_key}",
            source_outbox_id=outbox_id,
            source_command_id=command_id,
            source_device_id=device_id,
            evidence_snapshot_json={
                "session_id": session_id,
                "outbox_id": outbox_id,
                "dispatch_key": optional_str_attr(outbox, "dispatch_key"),
                "outbox_payload": dict_attr(outbox, "payload_json"),
                "command_id": command_id,
                "command_code": optional_str_attr(command, "command_code") if command is not None else None,
                "device_id": device_id,
                "reason": source_reason,
            },
        )

    async def create_for_safety_estop(self, db: Any, *, incident: Any) -> RuntimeHold:
        """Safety ESTOP incident 创建后幂等创建 RuntimeHold。"""

        incident_id = required_int_attr(incident, "id")
        source_reason = optional_str_attr(incident, "reason") or "ESTOP_PRESSED"
        return await self.repository.create_open_hold(
            db,
            hold_type=RuntimeHoldType.SAFETY_ESTOP,
            workline_id=required_int_attr(incident, "workline_id"),
            source_kind="SAFETY_ESTOP",
            source_reason=source_reason,
            source_idempotency_key=f"safety-estop:{incident_id}",
            source_inbox_id=optional_int_attr(incident, "source_inbox_id"),
            source_command_id=optional_int_attr(incident, "source_command_id"),
            source_device_id=optional_int_attr(incident, "source_device_id"),
            evidence_snapshot_json={
                "incident_id": incident_id,
                "event_type": optional_str_attr(incident, "event_type"),
                "reason": source_reason,
                "evidence": dict_attr(incident, "evidence_json"),
            },
        )

    async def create_for_resource_reconciliation(
        self,
        db: Any,
        *,
        workline_id: int,
        source_reason: str,
        source_event_id: str,
        evidence: Mapping[str, Any],
        session_id: int | None = None,
        trace_id: str | None = None,
    ) -> RuntimeHold:
        """资源投影冲突时幂等创建 RuntimeHold。"""

        hold = await self.repository.create_open_hold(
            db,
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            source_kind="RESOURCE_RECONCILIATION",
            source_reason=source_reason,
            source_idempotency_key=f"resource-reconciliation:{source_reason}:{source_event_id}",
            evidence_snapshot_json={
                **dict(evidence),
                "source_event_id": source_event_id,
                "reason": source_reason,
            },
        )
        await self._project_workline_resource_reconciling(db, workline_id=workline_id, source_reason=source_reason)
        return hold

    async def _project_workline_resource_reconciling(self, db: Any, *, workline_id: int, source_reason: str) -> None:
        """资源冲突 hold 生效时，同步冻结 WorkLine 安全状态投影。"""

        workline = await self.workline_repository.get_for_update(db, workline_id)
        if workline is None:
            return
        _ = await self.workline_status_projection_service.project_reconciling(
            db,
            workline_id=workline_id,
            occurred_at=None,
            reason=source_reason,
        )


runtime_hold_creation_service = RuntimeHoldCreationService()


__all__ = [
    "RuntimeHoldCreationService",
    "runtime_hold_creation_service",
]
