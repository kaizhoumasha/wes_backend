"""计划 Evidence 与业务事实同事务提交；提交后仅唤醒独立计划激活。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, cast

from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
)

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
from src.app.workline.repositories import workline_repository as default_workline_repository
from src.core.transaction_wakeup import defer_wakeup
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping

    from wes_plugin_sdk import PickingTaskPlanAdmissionPolicy


class PickingTaskPlanDeltaService:
    def __init__(
        self,
        session_factory: Any,
        *,
        evidence_service: Any = None,
        task_repository: Any = None,
        plan_repository: Any = None,
        plan_activation_plugin_identities: tuple[tuple[str, str], ...] = (),
        plan_admission_policies: Mapping[tuple[str, str], PickingTaskPlanAdmissionPolicy] | None = None,
        task_queue_gateway: Any = None,
        workline_repository: Any = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = task_repository or picking_task_repository
        self._plans = plan_repository or PickingTaskPlanDeltaRepository()
        self._plan_activation_plugin_identities = plan_activation_plugin_identities
        self._plan_admission_policies = plan_admission_policies or {}
        self._task_queue = task_queue_gateway
        self._worklines = workline_repository or default_workline_repository

    async def record(  # noqa: PLR0911
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
                    task = await self._lock_task_authority(db, original.data.task_id)
                    self._block(task, cast("int", evidence.id))
                return self._result(evidence, "CONFLICT", "IDEMPOTENCY_CONFLICT")
            if invalid:
                evidence.processed_at = evidence.received_at
                return self._result(evidence, "REJECTED", "INVALID_DATA")
            if ApplyStatus(evidence.apply_status) is ApplyStatus.APPLIED:
                return self._result(evidence, "DUPLICATE")
            task = await self._lock_task_authority(db, envelope.data.task_id)
            if task is not None:
                evidence.picking_task_id = task.id
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
            admission_reason = await self._admission_reason(db, task, envelope.data, evidence)
            if admission_reason is not None:
                await self._reject(db, task, evidence, admission_reason, received_at)
                return self._result(evidence, "CONFLICT", admission_reason)
            task.plan_blocked_evidence_id = None
            await self.apply_plan(db, task, envelope.data, evidence, received_at=received_at)
            await self._defer_activation_if_enabled(db, task)
            return self._result(evidence, "RECEIVED")

    async def _lock_task_authority(self, db: Any, task_id: str) -> Any:
        workline_id = await self._tasks.get_workline_id_by_task_id(db, task_id)
        if workline_id is not None:
            workline = await self._worklines.get_for_authority_update(db, workline_id)
            if workline is None:
                return None
        return await self._tasks.get_by_task_id_for_update(db, task_id)

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
        incoming_racks = [
            (rack.rack_id, rack_face) for rack in data.added_bin_source_racks or () for rack_face in rack.rack_face
        ]
        picks, racks = await self._plans.source_identities(
            db, task.id, data.plan_revision, direct_picks=incoming_picks, bin_racks=incoming_racks
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

    async def _admission_reason(self, db: Any, task: Any, data: Any, evidence: Any) -> str | None:
        if not self._plan_admission_policies or task.workline_id is None:
            return None
        if ApplyStatus(evidence.apply_status) is ApplyStatus.RECONCILING:
            first_reason = await self._plans.first_rejection(db, cast("int", evidence.id))
            if first_reason is None:
                raise RuntimeError("计划拒绝缺少首次 reason_code")
            if first_reason not in {
                "REVISION_CONFLICT",
                "STATE_CONFLICT",
                "REFERENCE_CONFLICT",
                "SOURCE_IDENTITY_PAYLOAD_CONFLICT",
                "SOURCE_IDENTITY_CORRELATION_CONFLICT",
            }:
                return first_reason
        line = await self._worklines.get_by_id(db, task.workline_id)
        if line is None:
            return None
        policy = self._plan_admission_policies.get((line.plugin_key, line.plugin_version))
        if policy is None:
            return None
        decision = policy(
            PickingTaskPlanAdmissionFact(
                task_id=data.task_id,
                plan_revision=data.plan_revision,
                has_direct_picks=bool(data.added_direct_picks),
            )
        )
        if decision.kind is PickingTaskPlanAdmissionDecisionKind.REJECT:
            return decision.reason_code
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

    async def _defer_activation_if_enabled(self, db: Any, task: Any) -> None:
        if self._task_queue is None or not self._plan_activation_plugin_identities or task.workline_id is None:
            return
        line = await self._worklines.get_by_id(db, task.workline_id)
        if (
            line is not None
            and line.is_active
            and not line.is_deleted
            and (line.plugin_key, line.plugin_version) in self._plan_activation_plugin_identities
        ):
            defer_wakeup(db, self._task_queue.enqueue_picking_task_plans)


__all__ = ["PickingTaskPlanDeltaService"]
