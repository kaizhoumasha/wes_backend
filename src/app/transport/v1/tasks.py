"""Transport 调试创建与本地只读状态 API。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    BinExchangePair,
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
    TransportContractError,
    TransportHandle,
    TransportIdempotencyConflict,
    TransportResourceConflict,
    TransportTaskKind,
    TransportTaskStatus,
)
from src.core.exceptions import ConflictException, ServiceUnavailableException, ValidationException
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, SuccessCode, response_builder

router = APIRouter(tags=["Transport 调试"])

_UUID7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_UUID7 = Annotated[str, StringConstraints(min_length=36, max_length=36, pattern=_UUID7_PATTERN)]
_TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r".*\S.*")]


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _DebugTaskRequestBase(_StrictApiModel):
    client_request_id: _UUID7
    station_id: _TEXT | None = None


class _RackPosition(_StrictApiModel):
    kind: Literal["RACK_POSITION"]
    location_code: _TEXT


class _RackBinSlot(_StrictApiModel):
    kind: Literal["RACK_BIN_SLOT"]
    rack_id: _TEXT
    rack_face: RackFace
    slot_id: _TEXT


class _HandoffPosition(_StrictApiModel):
    kind: Literal["HANDOFF_POSITION"]
    location_code: _TEXT


type _BinPosition = Annotated[_RackBinSlot | _HandoffPosition, Field(discriminator="kind")]


class _RackMoveData(_StrictApiModel):
    rack_id: _TEXT
    source: _RackPosition
    target: _RackPosition
    target_face: RackFace


class _RackRotateData(_StrictApiModel):
    rack_id: _TEXT
    position: _RackPosition
    target_face: RackFace


class _BinMoveMember(_StrictApiModel):
    bin_id: _TEXT
    source: _BinPosition
    target: _BinPosition


class _BinMoveData(_StrictApiModel):
    moves: list[_BinMoveMember] = Field(min_length=1, max_length=4)


class _BinExchangePair(_StrictApiModel):
    left_bin_id: _TEXT
    left_location: _RackBinSlot
    right_bin_id: _TEXT
    right_location: _RackBinSlot


class _BinExchangeData(_StrictApiModel):
    exchange_pairs: list[_BinExchangePair] = Field(min_length=1, max_length=2)


class _RackMoveDebugTask(_DebugTaskRequestBase):
    kind: Literal["RACK_MOVE"]
    data: _RackMoveData


class _RackRotateDebugTask(_DebugTaskRequestBase):
    kind: Literal["RACK_ROTATE"]
    data: _RackRotateData


class _BinMoveDebugTask(_DebugTaskRequestBase):
    kind: Literal["BIN_MOVE"]
    data: _BinMoveData


class _BinExchangeDebugTask(_DebugTaskRequestBase):
    kind: Literal["BIN_EXCHANGE"]
    data: _BinExchangeData


type _DebugTransportTaskRequest = Annotated[
    _RackMoveDebugTask | _RackRotateDebugTask | _BinMoveDebugTask | _BinExchangeDebugTask,
    Field(discriminator="kind"),
]


class DebugTransportTaskCreated(_StrictApiModel):
    transport_task_id: str
    client_request_id: str


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
    arrival_face: Literal["A", "B"] | None


class TransportResultResponse(_StrictApiModel):
    outcome_version: int
    status: Literal["SUCCEEDED", "FAILED", "REJECTED", "UNKNOWN"]
    reason_code: str | None
    members: list[TransportResultMemberResponse]


class TransportTaskResponse(TransportTaskSummaryResponse):
    request: dict[str, Any]
    result: TransportResultResponse | None


class TransportTaskPageResponse(_StrictApiModel):
    items: list[TransportTaskSummaryResponse]
    next_cursor: str | None


_OPENAPI_EXAMPLES = {
    "rack_move": {
        "summary": "移动货架",
        "value": {
            "client_request_id": "0198c480-5a00-7c31-8000-000000000001",
            "station_id": "STATION-DEBUG",
            "kind": "RACK_MOVE",
            "data": {
                "rack_id": "RACK-01",
                "source": {"kind": "RACK_POSITION", "location_code": "BUFFER-01"},
                "target": {"kind": "RACK_POSITION", "location_code": "LINE-01"},
                "target_face": "A",
            },
        },
    },
    "rack_rotate": {
        "summary": "旋转货架",
        "value": {
            "client_request_id": "0198c480-5a00-7c31-8000-000000000002",
            "station_id": "STATION-DEBUG",
            "kind": "RACK_ROTATE",
            "data": {
                "rack_id": "RACK-01",
                "position": {"kind": "RACK_POSITION", "location_code": "LINE-01"},
                "target_face": "B",
            },
        },
    },
    "bin_move": {
        "summary": "移动料箱",
        "value": {
            "client_request_id": "0198c480-5a00-7c31-8000-000000000003",
            "station_id": "STATION-DEBUG",
            "kind": "BIN_MOVE",
            "data": {
                "moves": [
                    {
                        "bin_id": "BIN-01",
                        "source": {
                            "kind": "RACK_BIN_SLOT",
                            "rack_id": "RACK-01",
                            "rack_face": "A",
                            "slot_id": "SLOT-01",
                        },
                        "target": {"kind": "HANDOFF_POSITION", "location_code": "HANDOFF-01"},
                    }
                ]
            },
        },
    },
    "bin_exchange": {
        "summary": "交换料箱",
        "value": {
            "client_request_id": "0198c480-5a00-7c31-8000-000000000004",
            "station_id": "STATION-DEBUG",
            "kind": "BIN_EXCHANGE",
            "data": {
                "exchange_pairs": [
                    {
                        "left_bin_id": "BIN-01",
                        "left_location": {
                            "kind": "RACK_BIN_SLOT",
                            "rack_id": "RACK-01",
                            "rack_face": "A",
                            "slot_id": "SLOT-01",
                        },
                        "right_bin_id": "BIN-02",
                        "right_location": {
                            "kind": "RACK_BIN_SLOT",
                            "rack_id": "RACK-02",
                            "rack_face": "A",
                            "slot_id": "SLOT-01",
                        },
                    }
                ]
            },
        },
    },
}


def _transport_runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "transport_runtime", None)
    if runtime is None or runtime.closed:
        raise ServiceUnavailableException("Transport runtime 不可用")
    return runtime


def _rack_position(position: _RackPosition) -> RackPosition:
    return RackPosition(location_code=position.location_code)


def _rack_bin_slot(position: _RackBinSlot) -> RackBinSlot:
    return RackBinSlot(rack_id=position.rack_id, rack_face=position.rack_face, slot_id=position.slot_id)


def _bin_position(position: _BinPosition) -> RackBinSlot | HandoffPosition:
    if isinstance(position, _RackBinSlot):
        return _rack_bin_slot(position)
    return HandoffPosition(location_code=position.location_code)


async def _dispatch_debug_task(payload: _DebugTransportTaskRequest, runtime: Any) -> TransportHandle:
    caller = TransportCaller(
        workline_id=TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
        station_id=payload.station_id,
    )
    if isinstance(payload, _RackMoveDebugTask):
        return await runtime.port.move_rack(
            payload.client_request_id,
            caller,
            payload.data.rack_id,
            _rack_position(payload.data.source),
            _rack_position(payload.data.target),
            payload.data.target_face,
        )
    if isinstance(payload, _RackRotateDebugTask):
        return await runtime.port.rotate_rack(
            payload.client_request_id,
            caller,
            payload.data.rack_id,
            _rack_position(payload.data.position),
            payload.data.target_face,
        )
    if isinstance(payload, _BinMoveDebugTask):
        moves = tuple(
            BinMove(
                bin_id=move.bin_id,
                source=_bin_position(move.source),
                target=_bin_position(move.target),
            )
            for move in payload.data.moves
        )
        return await runtime.port.move_bins(payload.client_request_id, caller, moves)
    pairs = tuple(
        BinExchangePair(
            left_bin_id=pair.left_bin_id,
            left_location=_rack_bin_slot(pair.left_location),
            right_bin_id=pair.right_bin_id,
            right_location=_rack_bin_slot(pair.right_location),
        )
        for pair in payload.data.exchange_pairs
    )
    return await runtime.port.exchange_bins(payload.client_request_id, caller, pairs)


@router.post(
    "/debug-tasks",
    summary="[ops:transport:debug-create] 创建 Transport 调试任务",
    response_model=ResponseSchemaModel[DebugTransportTaskCreated],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport 请求不满足领域约束"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "幂等身份或 Transport 资源冲突"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport:debug-create"))],
)
async def create_debug_transport_task(
    request: Request,
    payload: Annotated[_DebugTransportTaskRequest, Body(openapi_examples=_OPENAPI_EXAMPLES)],
) -> ResponseSchemaModel[DebugTransportTaskCreated]:
    runtime = _transport_runtime(request)
    try:
        handle = await _dispatch_debug_task(payload, runtime)
    except (TransportIdempotencyConflict, TransportResourceConflict) as exc:
        raise ConflictException(str(exc)) from exc
    except TransportContractError as exc:
        raise ValidationException(str(exc), code="2004", status_code=400) from exc
    data = DebugTransportTaskCreated(
        transport_task_id=handle.transport_task_id,
        client_request_id=handle.client_request_id,
    )
    return cast(
        "ResponseSchemaModel[DebugTransportTaskCreated]",
        response_builder.success(data=data, code=SuccessCode.ACCEPTED),
    )


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


__all__ = ["router"]
