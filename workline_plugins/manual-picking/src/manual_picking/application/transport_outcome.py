"""把原 Transport 结果可靠交给人工拣料业务。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol

from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.repositories import inbound_evidence_repository, transport_decision_binding_repository
from src.app.execution.services.inbound_evidence_service import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.transport.repository import TransportRepository
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.execution.models import InboundEvidence, TransportDecisionBinding
    from src.app.transport.contracts import TransportOutcome


class BindingRepositoryPort(Protocol):
    async def get_by_client_request_id(self, db: Any, client_request_id: str) -> TransportDecisionBinding | None: ...


class EvidenceRepositoryPort(Protocol):
    async def get_by_id_without_lock(self, db: Any, evidence_id: int) -> InboundEvidence | None: ...


class ManualPickingTransportOutcomePublisher:
    """只持久接收原任务结果；到位判定与后续业务动作由独立 handler 负责。"""

    def __init__(
        self,
        *,
        binding_repository: BindingRepositoryPort = transport_decision_binding_repository,
        evidence_repository: EvidenceRepositoryPort = inbound_evidence_repository,
        evidence_service: InboundEvidenceService | None = None,
        transports: TransportRepository | None = None,
    ) -> None:
        self._bindings = binding_repository
        self._evidences = evidence_repository
        self._evidence_service = evidence_service or InboundEvidenceService()
        self._transports = transports or TransportRepository()

    async def publish(self, db: Any, outcome: TransportOutcome) -> bool:
        binding = await self._bindings.get_by_client_request_id(db, outcome.client_request_id)
        if binding is None or binding.client_request_id != outcome.client_request_id:
            raise LookupError("manual-picking Transport outcome 缺少原 binding")
        rack_steps = {"PICKING_TASK_TARGET_RACK_IN", "PICKING_TASK_BIN_SOURCE_RACK_IN"}
        batch_operations = {
            "MANUAL_PICKING_INBOUND_BATCH": BIN_INBOUND_BATCH_OPERATION,
            "MANUAL_PICKING_RETURN_BATCH": BIN_RETURN_BATCH_OPERATION,
        }
        if binding.step not in rack_steps | batch_operations.keys():
            raise ValueError("manual-picking Transport binding step 非法")
        if outcome.caller.workline_id != str(binding.workline_id):
            raise ValueError("manual-picking Transport outcome WorkLine 不匹配")
        source = await self._evidences.get_by_id_without_lock(db, binding.source_evidence_id)
        if binding.step in rack_steps:
            if any(member.object_id != binding.resource_fence_id for member in outcome.members):
                raise ValueError("manual-picking Transport outcome rack 不匹配")
            if source is None or source.operation != "outbound.picking_task.plan_delta@v1":
                raise LookupError("manual-picking Transport outcome 缺少原计划 Evidence")
            task_id = source.normalized_payload.get("data", {}).get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise ValueError("manual-picking 原计划 Evidence 缺少 PickingTask identity")
            business_identity = {"picking_task_id": task_id, "rack_id": binding.resource_fence_id}
        else:
            if (
                source is None
                or source.kind != InboundEvidenceKind.WMS_RESULT
                or source.operation != batch_operations[binding.step]
                or source.operation_id != binding.resource_fence_id
                or (
                    binding.correlation_id != binding.resource_fence_id
                    if binding.step == "MANUAL_PICKING_RETURN_BATCH"
                    else not binding.correlation_id.startswith(f"{binding.resource_fence_id}:")
                )
            ):
                raise LookupError("manual-picking Transport outcome 缺少原批次 Evidence")
            task = await self._transports.get_task_by_client_request(db, outcome.client_request_id)
            if (
                task is None
                or task.kind != "BIN_MOVE"
                or task.transport_task_id != outcome.transport_task_id
                or task.client_request_id != outcome.client_request_id
            ):
                raise LookupError("manual-picking Transport outcome 缺少原 BIN_MOVE")
            if outcome.status == "SUCCEEDED":
                expected = tuple((move["bin_code"], move["target"]) for move in task.request_json["moves"])
                actual = tuple(
                    (member.object_id, asdict(member.final_position) if member.final_position is not None else None)
                    for member in outcome.members
                )
                if actual != expected:
                    raise ValueError("manual-picking Transport outcome bin members 不匹配")
            business_identity = {"batch_operation_id": binding.resource_fence_id}
        accepted = await self._evidence_service.accept(
            db,
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{outcome.transport_task_id}:outcome:{outcome.outcome_version}",
            normalized_payload={
                **asdict(outcome),
                **business_identity,
                "step": binding.step,
                "source_evidence_id": binding.source_evidence_id,
            },
            received_at=timezone.now_for_db(),
            workline_id=binding.workline_id,
            transport_task_id=outcome.transport_task_id,
            contract_key="manual_picking.transport_outcome",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        if isinstance(accepted, InboundEvidenceConflictResult):
            raise accepted.to_exception()
        return True


__all__ = ["ManualPickingTransportOutcomePublisher"]
