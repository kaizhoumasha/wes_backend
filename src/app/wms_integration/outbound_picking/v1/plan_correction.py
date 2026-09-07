"""计划阻塞的受限人工对账入口；业务校验和提交归所属 Service。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Protocol, cast

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PlanCorrectionConflictError
from src.core.exceptions import ConflictException, ServiceUnavailableException
from src.core.rbac import require_superuser
from src.core.response import ResponseSchemaModel, response_builder
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import (
        PickingTaskPlanCorrectionResult,
    )

router = APIRouter(tags=["Outbound Picking 计划对账"])


class ApplyPlanCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    correction_evidence_id: Annotated[int, Field(gt=0, le=2**63 - 1)]
    expected_version: Annotated[int, Field(gt=0, le=2**63 - 1)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ApplyPlanCorrectionResponse(BaseModel):
    task_id: str
    plan_revision: int
    version: int
    correction_evidence_id: int


class PlanCorrectionServicePort(Protocol):
    async def apply_correction(
        self,
        *,
        task_id: str,
        blocked_evidence_id: int,
        correction_evidence_id: int,
        expected_version: int,
        reason: str,
        actor_id: int | None,
        received_at: datetime,
    ) -> PickingTaskPlanCorrectionResult: ...


def _service(request: Request) -> PlanCorrectionServicePort:
    runtime = getattr(request.app.state, "outbound_picking_runtime", None)
    if runtime is None:
        raise ServiceUnavailableException("Outbound Picking runtime 不可用")
    return cast("PlanCorrectionServicePort", runtime.plan_delta_service)


@router.post(
    "/v1/outbound-picking/tasks/{task_id:path}/plan-blockers/{blocking_evidence_id}/apply-correction",
    summary="校验两份计划证据并原子应用修正版本",
    response_model=ResponseSchemaModel[ApplyPlanCorrectionResponse],
    dependencies=[Depends(require_superuser)],
)
async def apply_plan_correction(
    request: Request,
    payload: ApplyPlanCorrectionRequest,
    task_id: Annotated[str, Path(pattern=BUSINESS_IDENTIFIER_PATTERN)],
    blocking_evidence_id: Annotated[int, Path(gt=0, le=2**63 - 1)],
) -> ResponseSchemaModel[ApplyPlanCorrectionResponse]:
    try:
        task = await _service(request).apply_correction(
            task_id=task_id,
            blocked_evidence_id=blocking_evidence_id,
            correction_evidence_id=payload.correction_evidence_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            actor_id=request.state.user_id,
            received_at=timezone.now_for_db(),
        )
    except PlanCorrectionConflictError as error:
        raise ConflictException(str(error)) from error
    data = ApplyPlanCorrectionResponse(
        task_id=task.task_id,
        plan_revision=task.plan_revision,
        version=task.version,
        correction_evidence_id=task.correction_evidence_id,
    )
    return cast("ResponseSchemaModel[ApplyPlanCorrectionResponse]", response_builder.success(data=data))
