"""WMS 可靠发送与接收事实只读入口。"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from src.app.execution.observation import ConfirmationObservation, EvidenceObservation
from src.app.execution.services.execution_observation_service import (
    ExecutionObservationService,
    execution_observation_service,
)
from src.app.wms_adapter.wire_common import OperationId, is_wire_operation
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder

router = APIRouter(prefix="/v1/wms-diagnostics", tags=["WMS diagnostics"])


class ObservationQuery(BaseModel):
    operation: str = Field(min_length=1, max_length=80)
    operation_id: OperationId

    @field_validator("operation")
    @classmethod
    def valid_operation(cls, value: str) -> str:
        if not is_wire_operation(value):
            raise ValueError("operation 必须满足公共 WMS 信封合同")
        return value


def get_observation_service() -> ExecutionObservationService:
    return execution_observation_service


@router.get(
    "/confirmations",
    summary="[ops:wms-confirmation:read] 查询 WMS 可靠发送义务",
    response_model=ResponseSchemaModel[ConfirmationObservation],
    dependencies=[Depends(RequirePermission("ops:wms-confirmation:read"))],
    responses={404: {"description": "可靠义务不存在"}, 503: {"description": "持久化存储不可用"}},
)
async def get_confirmation(
    query: Annotated[ObservationQuery, Query()],
    service: Annotated[ExecutionObservationService, Depends(get_observation_service)],
) -> ResponseSchemaModel[ConfirmationObservation]:
    try:
        result = await service.get_confirmation(query.operation, query.operation_id)
    except (SQLAlchemyError, ConnectionError, TimeoutError) as exc:
        raise HTTPException(503, "持久化存储暂不可用") from exc
    if result is None:
        raise HTTPException(404, "可靠义务不存在")
    return cast("ResponseSchemaModel[ConfirmationObservation]", response_builder.success(data=result))


@router.get(
    "/evidences",
    summary="[ops:wms-evidence:read] 查询 WMS 持久化接收与应用事实",
    response_model=ResponseSchemaModel[EvidenceObservation],
    dependencies=[Depends(RequirePermission("ops:wms-evidence:read"))],
    responses={404: {"description": "WMS Evidence 不存在"}, 503: {"description": "持久化存储不可用"}},
)
async def get_evidence(
    query: Annotated[ObservationQuery, Query()],
    service: Annotated[ExecutionObservationService, Depends(get_observation_service)],
) -> ResponseSchemaModel[EvidenceObservation]:
    try:
        result = await service.get_evidence(query.operation, query.operation_id)
    except (SQLAlchemyError, ConnectionError, TimeoutError) as exc:
        raise HTTPException(503, "持久化存储暂不可用") from exc
    if result is None:
        raise HTTPException(404, "WMS Evidence 不存在")
    return cast("ResponseSchemaModel[EvidenceObservation]", response_builder.success(data=result))
