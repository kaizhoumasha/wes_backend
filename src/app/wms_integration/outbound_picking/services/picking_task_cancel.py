"""在共享 Evidence 事务内取消 PickingTask 或其计划成员。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from src.app.execution.models import InboundEvidenceApplyStatus as Status
from src.app.execution.models import InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.outbound_picking.cancel_event_handler import (
    PickingTaskCancelPersistenceResult as Result,
)
from src.app.wms_adapter.outbound_picking.cancel_wire import (
    PickingTaskCancelEvent,
    PickingTaskCancelInvalidData,
    PickingTaskCancelMembersData,
)
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository
from src.app.wms_integration.outbound_picking.repositories.picking_task_cancel_repository import (
    PickingTaskCancelRepository,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.models import InboundEvidence


class TransportFinalizer(Protocol):
    async def finalize_unsent_task_in_session(
        self, db: AsyncSession, transport_task_id: str, *, reason_code: str = ...
    ) -> bool: ...


class PickingTaskCancelService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        evidence_service: InboundEvidenceService | None = None,
        task_repository: PickingTaskRepository | None = None,
        cancel_repository: PickingTaskCancelRepository | None = None,
        transport_service: TransportFinalizer | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = task_repository or picking_task_repository
        self._cancel = cancel_repository or PickingTaskCancelRepository()
        self._transport = transport_service

    async def record(
        self,
        envelope: PickingTaskCancelEvent | PickingTaskCancelInvalidData,
        *,
        received_at: datetime,
    ) -> Result:
        invalid = isinstance(envelope, PickingTaskCancelInvalidData)
        payload = envelope.raw_envelope if invalid else envelope.model_dump(mode="json", exclude_none=True)
        operation, operation_id = payload["operation"], payload["operation_id"]
        async with self._sessions.begin() as db:
            acceptance = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{operation}:{operation_id}",
                normalized_payload=payload,
                received_at=received_at,
                operation=operation,
                operation_id=operation_id,
                contract_key=operation,
                contract_version="1.0",
                apply_status=Status.IGNORED if invalid else Status.PENDING,
            )
            evidence = acceptance.evidence
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return self._result(evidence, "CONFLICT", "IDEMPOTENCY_CONFLICT")
            if invalid:
                evidence.processed_at = evidence.received_at
                return self._result(evidence, "REJECTED", "INVALID_DATA")
            if acceptance.duplicate:
                if Status(evidence.apply_status) is Status.APPLIED:
                    return self._result(evidence, "DUPLICATE")
                if Status(evidence.apply_status) is Status.RECONCILING:
                    reason = await self._cancel.first_rejection(db, cast("int", evidence.id))
                    if reason is None:
                        raise RuntimeError("取消拒绝缺少首次 reason_code")
                    return self._result(evidence, "CONFLICT", reason)
                raise RuntimeError("PickingTask 取消 Evidence 处于非法状态")
            await self._tasks.lock_task_identity(db, envelope.data.task_id)
            task = await self._tasks.get_by_task_id_for_update(db, envelope.data.task_id)
            if task is not None:
                evidence.picking_task_id = task.id
            reason: str | None = None
            if task is None:
                reason = "REFERENCE_CONFLICT"
            elif envelope.data.cancel_scope == "TASK":
                if (
                    task.status not in (PickingTaskStatus.QUEUED, PickingTaskStatus.PREPARING)
                    or task.last_applied_plan_revision != 0
                ):
                    reason = "STATE_CONFLICT"
                else:
                    task.status = PickingTaskStatus.CANCELLED
                    task.increment_version()
            elif not isinstance(envelope.data, PickingTaskCancelMembersData):
                raise RuntimeError("PLAN_MEMBERS 取消缺少成员合同")
            else:
                matched, transport_task_ids = await self._cancel.cancel_members(
                    db,
                    task_id=cast("int", task.id),
                    data=envelope.data,
                    evidence_id=cast("int", evidence.id),
                )
                if self._transport is not None:
                    for transport_task_id in transport_task_ids:
                        _ = await self._transport.finalize_unsent_task_in_session(
                            db,
                            transport_task_id,
                            reason_code="TRANSPORT_WITHDRAWN_BEFORE_SEND",
                        )
                if matched:
                    task.increment_version()
            if reason is not None:
                _ = await self._evidence.record_conflict(
                    db,
                    first=evidence,
                    source_identity=evidence.source_identity,
                    normalized_payload=evidence.normalized_payload,
                    reason_code=reason,
                    received_at=received_at,
                )
                evidence.apply_status = Status.RECONCILING
                evidence.processed_at = received_at
                await self._tasks.flush(db)
                return self._result(evidence, "CONFLICT", reason)
            evidence.apply_status = Status.APPLIED
            evidence.processed_at = received_at
            await self._tasks.flush(db)
            return self._result(evidence, "RECEIVED")

    @staticmethod
    def _result(evidence: InboundEvidence, code: str, reason: str | None = None) -> Result:
        return Result(
            code=code,
            timestamp_ms=int(timezone.to_utc(evidence.received_at).timestamp() * 1000),
            reason_code=reason,
        )


__all__ = ["PickingTaskCancelService"]
