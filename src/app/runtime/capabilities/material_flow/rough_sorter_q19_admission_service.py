"""粗分机 Q19 首次准入事实的持久化与重放。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_context import (
    RoughSorterQ19AdmissionDecision,
)
from src.app.wms_integration.ports.document_operations import (
    ValidateRoughSorterAdmissionRequest,
    ValidateRoughSorterAdmissionResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryContractFailure,
    QuerySuccess,
    WmsQueryOutcome,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.models.session import WorklineSession
    from src.app.wms_integration.query_projection import WmsQueryRequestProjection


class RoughSorterQ19QueryRuntime(Protocol):
    """Q19 service 只消费 registry runtime 的 typed 投影与执行边界。"""

    def project(self, request: ValidateRoughSorterAdmissionRequest) -> WmsQueryRequestProjection: ...

    async def execute(
        self,
        request: ValidateRoughSorterAdmissionRequest,
    ) -> WmsQueryOutcome[ValidateRoughSorterAdmissionResult]: ...


def _decision_from_result(
    *,
    request_canonical_hash: str,
    result: ValidateRoughSorterAdmissionResult,
    evidence_reference: str,
) -> RoughSorterQ19AdmissionDecision:
    return RoughSorterQ19AdmissionDecision(
        request_canonical_hash=request_canonical_hash,
        decision=result.decision,
        reason_code=result.reason_code,
        grn_id=result.grn_id,
        po_number=result.po_number,
        po_item=result.po_item,
        material_code=result.material_code,
        pkg_id=result.pkg_id,
        measurement_decision=result.measurement_decision,
        standard_reel_diameter_mm=result.standard_reel_diameter_mm,
        reel_diameter_tolerance_mm=result.reel_diameter_tolerance_mm,
        standard_reel_thickness_mm=result.standard_reel_thickness_mm,
        reel_thickness_tolerance_mm=result.reel_thickness_tolerance_mm,
        rule_version=result.rule_version,
        source_version=result.source_version,
        evidence_reference=evidence_reference,
    )


class RoughSorterQ19AdmissionService:
    """首次有效 Q19 结果落 Session context；已有事实时零 I/O 重放。"""

    def __init__(self, query_runtime: RoughSorterQ19QueryRuntime) -> None:
        self._query_runtime = query_runtime

    async def resolve(
        self,
        db: AsyncSession,
        *,
        session: WorklineSession,
        request: ValidateRoughSorterAdmissionRequest,
    ) -> WmsQueryOutcome[RoughSorterQ19AdmissionDecision]:
        projection = self._query_runtime.project(request)
        context = dict(session.context_json) if isinstance(session.context_json, dict) else {}
        persisted = context.get("wms_admission_decision")
        if persisted is not None:
            try:
                decision = RoughSorterQ19AdmissionDecision.model_validate(persisted)
            except ValidationError:
                return QueryContractFailure(
                    reason_code="WMS_Q19_PERSISTED_DECISION_INVALID",
                    message="persisted Q19 admission decision violates its typed contract",
                )
            if decision.request_canonical_hash != projection.request_canonical_hash:
                return QueryContractFailure(
                    reason_code="WMS_Q19_REPLAY_REQUEST_MISMATCH",
                    message="replayed Q19 request differs from the persisted first decision",
                )
            return QuerySuccess(decision, evidence_key=decision.evidence_reference)

        outcome: WmsQueryOutcome[Any] = await self._query_runtime.execute(request)
        if not isinstance(outcome, QuerySuccess):
            return outcome
        if not isinstance(outcome.value, ValidateRoughSorterAdmissionResult):
            return QueryContractFailure(
                reason_code="WMS_Q19_RESULT_TYPE_MISMATCH",
                message="Q19 runtime returned an unexpected typed result",
            )
        if outcome.evidence_key is None:
            return QueryContractFailure(
                reason_code="WMS_Q19_EVIDENCE_MISSING",
                message="Q19 first decision requires a transport evidence reference",
            )
        decision = _decision_from_result(
            request_canonical_hash=projection.request_canonical_hash,
            result=outcome.value,
            evidence_reference=outcome.evidence_key,
        )
        context["wms_admission_decision"] = decision.model_dump(mode="json")
        session.context_json = context
        await db.flush()
        return QuerySuccess(decision, evidence_key=decision.evidence_reference)


__all__ = ["RoughSorterQ19AdmissionService", "RoughSorterQ19QueryRuntime"]
