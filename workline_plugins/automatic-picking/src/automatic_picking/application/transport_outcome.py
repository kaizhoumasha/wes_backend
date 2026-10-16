"""原 Transport 结果的业务接收；派发、重试和物理投影继续由宿主负责。"""

from dataclasses import asdict

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.repositories import transport_decision_binding_repository
from src.app.execution.services.inbound_evidence_service import InboundEvidenceConflictResult, InboundEvidenceService
from src.utils.timezone import timezone


class AutomaticPickingTransportOutcomePublisher:
    def __init__(self, *, bindings=None, evidence_service=None):
        self.bindings = bindings or transport_decision_binding_repository
        self.evidences = evidence_service or InboundEvidenceService()

    async def publish(self, db, outcome):
        binding = await self.bindings.get_by_client_request_id(db, outcome.client_request_id)
        if (
            binding is None
            or binding.client_request_id != outcome.client_request_id
            or str(binding.workline_id) != outcome.caller.workline_id
            or binding.picking_task_id is None
        ):
            raise ValueError("automatic Transport outcome differs from its frozen owner")
        rack_steps = {"PICKING_TASK_TARGET_RACK_IN", "PICKING_TASK_BIN_SOURCE_RACK_IN", "PICKING_TASK_RETURN_RACK_IN"}
        if binding.step in rack_steps:
            if (outcome.members or outcome.status == "SUCCEEDED") and (
                len(outcome.members) != 1 or outcome.members[0].object_id != binding.resource_fence_id
            ):
                raise ValueError("automatic rack outcome differs from its frozen rack")
        elif binding.step != "AUTOMATIC_PICKING_FEED":
            raise ValueError("unsupported automatic Transport binding step")
        accepted = await self.evidences.accept(
            db,
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{outcome.transport_task_id}:outcome:{outcome.outcome_version}",
            normalized_payload={
                **asdict(outcome),
                "step": binding.step,
                "source_evidence_id": binding.source_evidence_id,
            },
            received_at=timezone.now_for_db(),
            workline_id=binding.workline_id,
            picking_task_id=binding.picking_task_id,
            transport_task_id=outcome.transport_task_id,
            contract_key="automatic_picking.transport_outcome",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        if isinstance(accepted, InboundEvidenceConflictResult):
            raise accepted.to_exception()
        # 本切片业务由既有 plan 周期 owner 推进，不重复创建执行或另派发事件。
        return False
