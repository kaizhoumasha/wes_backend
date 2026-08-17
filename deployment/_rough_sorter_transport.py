"""粗分换架 Transport outcome 到 material evidence 的部署适配。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind, MaterialExecutionStatus
from src.app.execution.repositories import (
    inbound_evidence_repository,
    material_execution_repository,
    rack_replacement_transport_binding_repository,
)
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceConflictResult,
    InboundEvidenceService,
)
from src.app.transport.contracts import TransportOutcome, TransportOutcomeStatus
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.execution.models import InboundEvidence, MaterialExecution, RackReplacementTransportBinding

logger = logging.getLogger(__name__)


class SessionFactoryPort(Protocol):
    def begin(self) -> Any: ...


class BindingRepositoryPort(Protocol):
    async def get_by_client_request_id_for_update(
        self, db: Any, client_request_id: str
    ) -> RackReplacementTransportBinding | None: ...


class EvidenceRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: Any, evidence_id: int) -> InboundEvidence | None: ...


class ExecutionRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: Any, execution_id: int) -> MaterialExecution | None: ...


class RoughSorterTransportOutcomePublisher:
    """NEW_IN 才桥接 execution evidence；OLD_OUT 保持 Transport 域隔离。"""

    def __init__(
        self,
        *,
        session_factory: SessionFactoryPort,
        binding_repository: BindingRepositoryPort = rack_replacement_transport_binding_repository,
        evidence_repository: EvidenceRepositoryPort = inbound_evidence_repository,
        execution_repository: ExecutionRepositoryPort = material_execution_repository,
        evidence_service: InboundEvidenceService | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        self._sessions = session_factory
        self._bindings = binding_repository
        self._evidences = evidence_repository
        self._executions = execution_repository
        self._evidence_service = evidence_service or InboundEvidenceService()
        self._queue = queue_gateway

    async def publish(self, outcome: TransportOutcome) -> None:
        should_wake = False
        async with self._sessions.begin() as db:
            binding = await self._bindings.get_by_client_request_id_for_update(db, outcome.client_request_id)
            if binding is None:
                raise LookupError("Transport outcome 缺少换架 business binding")
            if binding.leg == "OLD_OUT":
                return
            if binding.leg != "NEW_IN":
                raise ValueError("Transport binding leg 非法")
            source = await self._evidences.get_by_id_for_update(db, binding.source_evidence_id)
            if (
                source is None
                or source.id is None
                or source.material_execution_id is None
                or source.line_run_epoch_id is None
                or source.operation != "inbound.source_rack.replacement_plan_decide@v1"
            ):
                raise ValueError("NEW_IN binding source evidence 不可用于 material correlation")
            execution = await self._executions.get_by_id_for_update(db, source.material_execution_id)
            if (
                execution is None
                or execution.id is None
                or execution.line_run_epoch_id != source.line_run_epoch_id
                or outcome.caller.workline_id != str(execution.workline_id)
            ):
                raise ValueError("Transport outcome 与 source execution correlation 不匹配")
            apply_status = (
                InboundEvidenceApplyStatus.IGNORED
                if execution.status == MaterialExecutionStatus.RECONCILING
                and outcome.status is TransportOutcomeStatus.UNKNOWN
                else InboundEvidenceApplyStatus.APPLIED
            )
            accepted = await self._evidence_service.accept(
                db,
                kind=InboundEvidenceKind.TRANSPORT_RESULT,
                source_identity=f"transport:{outcome.transport_task_id}:outcome:{outcome.outcome_version}",
                normalized_payload=_outcome_payload(outcome),
                received_at=timezone.now_for_db(),
                line_run_epoch_id=source.line_run_epoch_id,
                material_execution_id=source.material_execution_id,
                transport_task_id=outcome.transport_task_id,
                contract_key="rough_sorter.transport_outcome",
                contract_version="1.0",
                apply_status=apply_status,
            )
            if isinstance(accepted, InboundEvidenceConflictResult):
                raise accepted.to_exception()
            should_wake = accepted.evidence.apply_status == InboundEvidenceApplyStatus.APPLIED
        if should_wake:
            try:
                self._queue.enqueue_execution_facts()
            except Exception:
                logger.exception(
                    "rough_sorter.transport.execution_wake_failed",
                    extra={
                        "event": "rough_sorter.transport.execution_wake_failed",
                        "transport_task_id": outcome.transport_task_id,
                    },
                )


def _outcome_payload(outcome: TransportOutcome) -> dict[str, Any]:
    return {
        "transport_task_id": outcome.transport_task_id,
        "client_request_id": outcome.client_request_id,
        "outcome_version": outcome.outcome_version,
        "caller": {
            "workline_id": outcome.caller.workline_id,
            **({"station_id": outcome.caller.station_id} if outcome.caller.station_id is not None else {}),
        },
        "status": outcome.status.value,
        "reason_code": outcome.reason_code,
        "members": [
            {
                "object_id": member.object_id,
                "final_position": (
                    {
                        "kind": member.final_position.kind,
                        "location_code": member.final_position.location_code,
                    }
                    if member.final_position is not None
                    else None
                ),
                "position_unknown": member.position_unknown,
                "failure_code": member.failure_code,
                "arrival_face": member.arrival_face.value if member.arrival_face is not None else None,
            }
            for member in outcome.members
        ],
    }


__all__ = ["RoughSorterTransportOutcomePublisher"]
