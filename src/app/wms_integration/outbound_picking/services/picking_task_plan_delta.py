"""计划 Evidence 与业务事实同事务提交；不触发物理动作。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from src.app.execution.models import InboundEvidenceApplyStatus as ApplyStatus
from src.app.execution.models import InboundEvidenceKind, WmsConfirmationStatus
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.execution.services.inbound_evidence_service import normalize_payload
from src.app.wms_adapter.outbound_picking.plan_delta_event_handler import (
    PickingTaskPlanDeltaPersistenceResult as Result,
)
from src.app.wms_adapter.outbound_picking.plan_delta_wire import (
    PICKING_TASK_PLAN_DELTA_OPERATION,
    PickingTaskPlanDeltaEvent,
    PickingTaskPlanDeltaInvalidData,
)
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.utils.timezone import timezone


class PickingTaskPlanDeltaService:
    def __init__(
        self,
        session_factory: Any,
        *,
        evidence_service: Any = None,
        task_repository: Any = None,
        plan_repository: Any = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = task_repository or picking_task_repository
        self._plans = plan_repository or PickingTaskPlanDeltaRepository()

    async def record(
        self, envelope: PickingTaskPlanDeltaEvent | PickingTaskPlanDeltaInvalidData, *, received_at: datetime
    ) -> Result:
        invalid = isinstance(envelope, PickingTaskPlanDeltaInvalidData)
        payload = envelope.raw_envelope if invalid else envelope.model_dump(mode="json", exclude_none=True)
        operation, operation_id = payload["operation"], payload["operation_id"]
        async with self._sessions.begin() as db:
            accepted = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{operation}:{operation_id}",
                normalized_payload=payload,
                received_at=received_at,
                operation=operation,
                operation_id=operation_id,
                contract_key=operation,
                contract_version="1.0",
                apply_status=ApplyStatus.IGNORED if invalid else ApplyStatus.PENDING,
            )
            evidence = accepted.evidence
            if isinstance(accepted, InboundEvidenceConflictResult):
                # 身份归属只来自首次通过严格 DTO 的正文，不信任冲突正文中的 task_id。
                if ApplyStatus(evidence.apply_status) is not ApplyStatus.IGNORED:
                    original = PickingTaskPlanDeltaEvent.model_validate(evidence.normalized_payload)
                    task = await self._tasks.get_by_task_id_for_update(db, original.data.task_id)
                    self._block(task, cast("int", evidence.id))
                return self._result(evidence, "CONFLICT", "IDEMPOTENCY_CONFLICT")
            if invalid:
                evidence.processed_at = evidence.received_at
                return self._result(evidence, "REJECTED", "INVALID_DATA")
            if ApplyStatus(evidence.apply_status) is ApplyStatus.APPLIED:
                return self._result(evidence, "DUPLICATE")
            task = await self._tasks.get_by_task_id_for_update(db, envelope.data.task_id)
            reason = await self.validate_plan(db, task, envelope.data, received_at=received_at)
            if reason == "PENDING":
                return self._result(evidence, "UNAVAILABLE")
            if reason == "DUPLICATE":
                evidence.apply_status = ApplyStatus.APPLIED
                evidence.processed_at = received_at
                return self._result(evidence, "DUPLICATE")
            if reason is not None:
                if ApplyStatus(evidence.apply_status) is ApplyStatus.RECONCILING:
                    # 重新校验仍不可应用时重放首次拒绝，不因旧错误重报重建已解除的 blocker。
                    first_reason = await self._plans.first_rejection(db, cast("int", evidence.id))
                    if first_reason is None:
                        raise RuntimeError("计划拒绝缺少首次 reason_code")
                    return self._result(evidence, "CONFLICT", first_reason)
                await self._reject(db, task, evidence, reason, received_at)
                return self._result(evidence, "CONFLICT", reason)
            # validate_plan 已确认 blocker 归属；与计划应用共用任务锁和一次版本推进。
            task.plan_blocked_evidence_id = None
            await self.apply_plan(db, task, envelope.data, evidence, received_at=received_at)
            return self._result(evidence, "RECEIVED")

    @staticmethod
    def _result(evidence: Any, code: str, reason: str | None = None) -> Result:
        return Result(
            code=code, timestamp_ms=int(timezone.to_utc(evidence.received_at).timestamp() * 1000), reason_code=reason
        )

    @staticmethod
    def _block(task: Any, evidence_id: int) -> None:
        if (
            task is not None
            and task.status != PickingTaskStatus.EXECUTION_COMPLETED
            and task.plan_blocked_evidence_id is None
        ):
            task.plan_blocked_evidence_id = evidence_id
            task.increment_version()

    async def _reject(self, db: Any, task: Any, evidence: Any, reason: str, received_at: datetime) -> None:
        _ = await self._evidence.record_conflict(
            db,
            first=evidence,
            source_identity=evidence.source_identity,
            normalized_payload=evidence.normalized_payload,
            reason_code=reason,
            received_at=received_at,
        )
        evidence.apply_status = ApplyStatus.RECONCILING
        evidence.processed_at = received_at
        self._block(task, cast("int", evidence.id))

    async def validate_plan(  # noqa: PLR0911
        self, db: Any, task: Any, data: Any, *, received_at: datetime
    ) -> str | None:
        if task is None:
            return "REFERENCE_CONFLICT"
        if task.status not in (PickingTaskStatus.PREPARING, PickingTaskStatus.EXECUTING):
            return "STATE_CONFLICT"
        if data.plan_revision == task.last_applied_plan_revision:
            previous = await self._plans.get_evidence(db, task.last_plan_evidence_id)
            if (
                previous is not None
                and normalize_payload(previous.normalized_payload["data"])[1]
                == normalize_payload(data.model_dump(mode="json", exclude_none=True))[1]
            ):
                return "DUPLICATE"
            return "REVISION_CONFLICT"
        if data.plan_revision != task.last_applied_plan_revision + 1:
            return "REVISION_CONFLICT"
        if (data.plan_revision == 1) != (task.status == PickingTaskStatus.PREPARING):
            return "STATE_CONFLICT"
        confirmations, line = await self._plans.prepare_context(db, task)
        if len(confirmations) != 1 or line is None or line.id != task.workline_id:
            return "REFERENCE_CONFLICT"
        if not line.is_active:
            return "STATE_CONFLICT"
        confirmation = confirmations[0]
        request = confirmation.request_payload
        if (
            request.get("operation") != PICKING_TASK_PREPARE_OPERATION
            or request.get("operation_id") != confirmation.operation_id
            or request.get("data") != {"task_id": task.task_id, "workline_code": line.line_code}
        ):
            return "REFERENCE_CONFLICT"
        if confirmation.status == WmsConfirmationStatus.COMPLETED:
            response = (
                await self._plans.get_evidence(db, confirmation.response_evidence_id)
                if confirmation.response_evidence_id
                else None
            )
            if (
                confirmation.response_result != "PREPARE_ACCEPTED"
                or response is None
                or response.kind != InboundEvidenceKind.WMS_RESULT
                or response.operation != PICKING_TASK_PREPARE_OPERATION
                or response.operation_id != confirmation.operation_id
                or response.normalized_payload.get("operation_id") != confirmation.operation_id
                or response.normalized_payload.get("code") != "PREPARE_ACCEPTED"
                or response.normalized_payload.get("data") != {}
            ):
                return "STATE_CONFLICT"
        elif (
            data.plan_revision == 1
            and confirmation.status in (WmsConfirmationStatus.PENDING, WmsConfirmationStatus.DISPATCHING)
            and confirmation.deadline_at > received_at
        ):
            return "PENDING"
        else:
            return "STATE_CONFLICT"
        incoming_picks = [
            (p.source_locator.rack_id, p.source_locator.rack_face, p.source_locator.slot_id)
            for p in data.added_direct_picks or ()
        ]
        incoming_racks = [(r.rack_id, r.rack_face) for r in data.added_bin_source_racks or ()]
        picks, racks = await self._plans.source_identities(
            db, task.id, direct_picks=incoming_picks, bin_racks=incoming_racks
        )
        if (
            len(set(incoming_picks)) != len(incoming_picks)
            or len(set(incoming_racks)) != len(incoming_racks)
            or picks.intersection(incoming_picks)
            or racks.intersection(incoming_racks)
        ):
            return "REFERENCE_CONFLICT"
        if task.plan_blocked_evidence_id is not None:
            blocker = await self._plans.get_evidence(db, task.plan_blocked_evidence_id)
            if (
                blocker is None
                or blocker.operation != PICKING_TASK_PLAN_DELTA_OPERATION
                or blocker.normalized_payload.get("data", {}).get("task_id") != task.task_id
            ):
                return "REFERENCE_CONFLICT"
        return None

    async def apply_plan(self, db: Any, task: Any, data: Any, evidence: Any, *, received_at: datetime) -> None:
        if data.plan_revision == 1:
            task.target_rack_id = data.target_rack.rack_id
            task.target_rack_face = data.target_rack.rack_face
            task.initial_plan_evidence_id = evidence.id
            task.status = PickingTaskStatus.EXECUTING
        task.increment_version()
        task.last_applied_plan_revision = data.plan_revision
        task.last_plan_evidence_id = evidence.id
        evidence.apply_status = ApplyStatus.APPLIED
        evidence.processed_at = received_at
        _ = await self._plans.add_members(db, task.id, data, evidence.id)


__all__ = ["PickingTaskPlanDeltaService"]
