"""把原 Transport 结果可靠交给人工拣料业务。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol

from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.repositories import inbound_evidence_repository, transport_decision_binding_repository
from src.app.execution.services.inbound_evidence_service import InboundEvidenceConflictResult, InboundEvidenceService
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
    ) -> None:
        self._bindings = binding_repository
        self._evidences = evidence_repository
        self._evidence_service = evidence_service or InboundEvidenceService()

    async def publish(self, db: Any, outcome: TransportOutcome) -> bool:
        binding = await self._bindings.get_by_client_request_id(db, outcome.client_request_id)
        if binding is None or binding.client_request_id != outcome.client_request_id:
            raise LookupError("manual-picking Transport outcome 缺少原 binding")
        if binding.step not in {"PICKING_TASK_TARGET_RACK_IN", "PICKING_TASK_BIN_SOURCE_RACK_IN"}:
            raise ValueError("manual-picking Transport binding step 非法")
        if outcome.caller.workline_id != str(binding.workline_id):
            raise ValueError("manual-picking Transport outcome WorkLine 不匹配")
        if any(member.object_id != binding.resource_fence_id for member in outcome.members):
            raise ValueError("manual-picking Transport outcome rack 不匹配")
        source = await self._evidences.get_by_id_without_lock(db, binding.source_evidence_id)
        if source is None or source.operation != "outbound.picking_task.plan_delta@v1":
            raise LookupError("manual-picking Transport outcome 缺少原计划 Evidence")
        task_id = source.normalized_payload.get("data", {}).get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("manual-picking 原计划 Evidence 缺少 PickingTask identity")
        accepted = await self._evidence_service.accept(
            db,
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{outcome.transport_task_id}:outcome:{outcome.outcome_version}",
            normalized_payload={
                **asdict(outcome),
                "picking_task_id": task_id,
                "rack_id": binding.resource_fence_id,
                "step": binding.step,
                "source_evidence_id": binding.source_evidence_id,
            },
            received_at=timezone.now_for_db(),
            workline_id=binding.workline_id,
            transport_task_id=outcome.transport_task_id,
            contract_key="manual_picking.transport_outcome",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.PENDING,
        )
        if isinstance(accepted, InboundEvidenceConflictResult):
            raise accepted.to_exception()
        return False


__all__ = ["ManualPickingTransportOutcomePublisher"]
