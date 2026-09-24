"""Transport 调试创建与本地只读状态 API。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from src.app.transport.contracts import TransportContractError, TransportTaskKind, TransportTaskStatus
from src.core.exceptions import NotFoundException, ServiceUnavailableException, ValidationException
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder

router = APIRouter(tags=["Transport 调试"])

_UUID7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

_FACE = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=10,
        pattern=r"^[^\x00]+$",
        description="Opaque non-empty face value without NUL; preserve exactly",
    ),
]


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransportEvidenceResponse(_StrictApiModel):
    operation: str
    operation_id: str
    outcome_revision: int | None
    status: Literal["PENDING", "APPLIED", "CONFLICT"]
    conflict_code: str | None
    received_at: str
    processed_at: str | None


class TransportTaskSummaryResponse(_StrictApiModel):
    transport_task_id: str
    client_request_id: str
    submit_operation_id: str
    kind: Literal["RACK_MOVE", "RACK_ROTATE", "BIN_MOVE", "BIN_EXCHANGE"]
    status: Literal["PENDING", "ACCEPTED", "REJECTED", "SUCCEEDED", "FAILED", "RECONCILING"]
    reason_code: str | None
    created_at: str
    updated_at: str
    latest_evidence: TransportEvidenceResponse | None


class TransportResultMemberResponse(_StrictApiModel):
    object_id: str
    status: Literal["UNKNOWN", "FAILED", "SUCCEEDED"]
    final_position: dict[str, Any] | None
    position_unknown: bool
    failure_code: str | None
    arrival_face: _FACE | None


class TransportResultResponse(_StrictApiModel):
    outcome_version: int
    status: Literal["SUCCEEDED", "FAILED", "REJECTED", "UNKNOWN"]
    reason_code: str | None
    members: list[TransportResultMemberResponse]


class TransportTaskResponse(TransportTaskSummaryResponse):
    send_started_at: str | None
    next_submit_at: str | None
    result_deadline_at: str | None
    submit_attempt_count: int
    outcome_version: int
    published_outcome_version: int
    pending_evidence_count: int
    request: dict[str, Any]
    result: TransportResultResponse | None


class TransportCallbackReceiptResponse(_StrictApiModel):
    operation: str
    operation_id: str
    response_http_status: int
    response_code: str
    response_data: dict[str, Any]
    received_at: str
    conflict_code: str | None


class TransportTaskPageResponse(_StrictApiModel):
    items: list[TransportTaskSummaryResponse]
    next_cursor: str | None


def _transport_runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "transport_runtime", None)
    if runtime is None or runtime.closed:
        raise ServiceUnavailableException("Transport runtime 不可用")
    return runtime


@router.get(
    "/tasks",
    summary="[ops:transport-task:list] 查询本地 Transport 任务列表",
    response_model=ResponseSchemaModel[TransportTaskPageResponse],
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "游标或筛选条件无效"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport-task:list"))],
)
async def list_transport_tasks(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    kind: TransportTaskKind | None = None,
    status_filter: Annotated[TransportTaskStatus | None, Query(alias="status")] = None,
) -> ResponseSchemaModel[TransportTaskPageResponse]:
    runtime = _transport_runtime(request)
    try:
        page = await runtime.service.list_task_snapshots(
            limit=limit,
            cursor=cursor,
            kind=kind.value if kind is not None else None,
            status=status_filter.value if status_filter is not None else None,
        )
    except (TransportContractError, ValueError) as exc:
        raise ValidationException(str(exc), code="2004", status_code=400) from exc
    data = TransportTaskPageResponse.model_validate(page, from_attributes=True)
    return cast("ResponseSchemaModel[TransportTaskPageResponse]", response_builder.success(data=data))


@router.get(
    "/tasks/{transport_task_id}",
    summary="[ops:transport-task:read] 查询本地 Transport 任务",
    response_model=ResponseSchemaModel[TransportTaskResponse],
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "TransportTask 不存在"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport-task:read"))],
)
async def get_transport_task(
    request: Request,
    transport_task_id: Annotated[str, Path(min_length=1, max_length=120)],
) -> ResponseSchemaModel[TransportTaskResponse]:
    runtime = _transport_runtime(request)
    snapshot = await runtime.service.get_task_snapshot(transport_task_id)
    data = TransportTaskResponse.model_validate(snapshot, from_attributes=True)
    return cast("ResponseSchemaModel[TransportTaskResponse]", response_builder.success(data=data))


@router.get(
    "/callback-receipts",
    summary="[ops:transport-callback-receipt:read] 查询持久化 Transport 回调收据",
    response_model=ResponseSchemaModel[TransportCallbackReceiptResponse],
    dependencies=[Depends(RequirePermission("ops:transport-callback-receipt:read"))],
    responses={404: {"description": "收据不存在"}, 503: {"description": "Transport runtime 不可用"}},
)
async def get_transport_callback_receipt(
    request: Request,
    operation: Annotated[str, Query(min_length=1, max_length=80)],
    operation_id: Annotated[str, Query(pattern=_UUID7_PATTERN)],
) -> ResponseSchemaModel[TransportCallbackReceiptResponse]:
    runtime = _transport_runtime(request)
    snapshot = await runtime.service.get_callback_receipt_snapshot(operation, operation_id)
    if snapshot is None:
        raise NotFoundException(resource_type="TransportCallbackReceipt", resource_id=operation_id)
    data = TransportCallbackReceiptResponse.model_validate(snapshot, from_attributes=True)
    return cast("ResponseSchemaModel[TransportCallbackReceiptResponse]", response_builder.success(data=data))


__all__ = ["router"]
