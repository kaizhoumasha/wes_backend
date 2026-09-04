"""Transport 自动联调轮次 API 与 live-only 状态通知。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, cast

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, ValidationError, field_validator
from starlette.responses import StreamingResponse

from src.app.sys.services.event_stream_service import (
    TRANSPORT_DEBUG_RUN_STREAM_CHANNEL,
    event_stream_service,
)
from src.app.transport.debug_run_contracts import (
    CreateTransportDebugRun,
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
    TransportDebugRunPhase,
    TransportDebugRunStatus,
    TransportDebugRunStepStatus,
)
from src.app.transport.debug_run_service import (
    TransportDebugRunConflict,
    TransportDebugRunContractError,
)
from src.core.exceptions import ConflictException, ServiceUnavailableException, ValidationException
from src.core.logger import logger
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, SuccessCode, response_builder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

router = APIRouter(tags=["Transport 调试"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 25.0

_TEXT = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
_FACE = Annotated[
    StrictStr,
    Field(
        min_length=1,
        pattern=r"^[^\x00]+$",
        description="Opaque non-empty face value without NUL; preserve exactly",
    ),
]
_RUN_ID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
_REASON = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransportDebugRunBinRequest(_StrictApiModel):
    bin_id: _TEXT
    slot_id: _TEXT


class TransportDebugRunFaceGroupRequest(_StrictApiModel):
    face: _FACE
    bins: list[TransportDebugRunBinRequest] = Field(min_length=1, max_length=4)

    @field_validator("face")
    @classmethod
    def reject_blank_face(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("面值不能为空")
        return value


class CreateTransportDebugRunRequest(_StrictApiModel):
    rack_id: _TEXT
    face_groups: list[TransportDebugRunFaceGroupRequest] = Field(min_length=1)


class AbortTransportDebugRunRequest(_StrictApiModel):
    assertion: Literal["PHYSICAL_STATE_VERIFIED"]
    reason: _REASON


class TransportDebugRunBinResponse(_StrictApiModel):
    bin_id: str
    slot_id: str


class TransportDebugRunFaceGroupResponse(_StrictApiModel):
    face: str
    bins: list[TransportDebugRunBinResponse]


class TransportDebugRunStepResponse(_StrictApiModel):
    ordinal: int
    group_index: int | None
    phase: TransportDebugRunPhase
    status: TransportDebugRunStepStatus
    client_request_id: str | None
    transport_task_id: str | None
    evidence_high_watermark: int | None
    evidence_not_before_ms: int | None
    observed_bin_ids: list[str]
    reason_code: str | None
    created_at: str
    updated_at: str


class TransportDebugRunResponse(_StrictApiModel):
    run_id: str
    status: TransportDebugRunStatus
    rack_id: str
    face_groups: list[TransportDebugRunFaceGroupResponse]
    current_group_index: int
    current_phase: TransportDebugRunPhase
    current_step: TransportDebugRunStepResponse | None
    steps: list[TransportDebugRunStepResponse]
    observed_bin_ids: list[str]
    attention_code: str | None
    attention_detail: str | None
    can_abort: bool
    version: int
    created_by_user_id: int
    aborted_by_user_id: int | None
    aborted_reason: str | None
    created_at: str
    updated_at: str


class TransportDebugRunPageResponse(_StrictApiModel):
    items: list[TransportDebugRunResponse]
    next_cursor: str | None


class TransportDebugRunUpdated(_StrictApiModel):
    run_id: str
    version: int = Field(ge=1)
    status: TransportDebugRunStatus
    updated_at: str


class EventStreamPort(Protocol):
    def subscribe(
        self,
        channel: str,
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, object] | None]: ...


def _debug_run_service(request: Request) -> Any:
    runtime = getattr(request.app.state, "transport_runtime", None)
    if runtime is None or runtime.closed:
        raise ServiceUnavailableException("Transport runtime 不可用")
    service = getattr(runtime, "debug_run_service", None)
    if service is None:
        raise ServiceUnavailableException("Transport debug run service 不可用")
    return service


def _stream_service(request: Request) -> EventStreamPort:
    service = getattr(request.app.state, "transport_event_stream_service", event_stream_service)
    return cast("EventStreamPort", service)


def _domain_request(payload: CreateTransportDebugRunRequest) -> CreateTransportDebugRun:
    return CreateTransportDebugRun(
        rack_id=payload.rack_id,
        face_groups=tuple(
            TransportDebugFaceGroup(
                face=group.face,
                bins=tuple(TransportDebugBinSelection(bin_id=item.bin_id, slot_id=item.slot_id) for item in group.bins),
            )
            for group in payload.face_groups
        ),
    )


def _parse_update(event_type: object, payload: object) -> TransportDebugRunUpdated | None:
    if event_type != "transport_debug_run.updated" or not isinstance(payload, dict):
        return None
    try:
        return TransportDebugRunUpdated.model_validate(payload)
    except ValidationError as error:
        logger.warning(f"Transport debug run SSE 跳过非法 payload: {error}")
        return None


@router.post(
    "/debug-runs",
    summary="[ops:transport-debug-run:start] 创建 Transport 自动联调轮次",
    response_model=ResponseSchemaModel[TransportDebugRunResponse],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "自动联调请求不满足合同"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "已有活动轮次或并发状态冲突"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport-debug-run:start"))],
)
async def create_transport_debug_run(
    request: Request,
    payload: CreateTransportDebugRunRequest,
) -> ResponseSchemaModel[TransportDebugRunResponse]:
    service = _debug_run_service(request)
    try:
        snapshot = await service.create_run(_domain_request(payload), actor_id=request.state.user_id)
    except TransportDebugRunConflict as error:
        raise ConflictException(str(error)) from error
    except (TransportDebugRunContractError, ValueError) as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error
    data = TransportDebugRunResponse.model_validate(snapshot, from_attributes=True)
    return cast(
        "ResponseSchemaModel[TransportDebugRunResponse]",
        response_builder.success(data=data, code=SuccessCode.ACCEPTED),
    )


@router.get(
    "/debug-runs",
    summary="[ops:transport-debug-run:list] 查询 Transport 自动联调轮次",
    response_model=ResponseSchemaModel[TransportDebugRunPageResponse],
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "分页游标无效"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport-debug-run:list"))],
)
async def list_transport_debug_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
) -> ResponseSchemaModel[TransportDebugRunPageResponse]:
    try:
        page = await _debug_run_service(request).list_runs(limit=limit, cursor=cursor)
    except TransportDebugRunContractError as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error
    data = TransportDebugRunPageResponse.model_validate(page, from_attributes=True)
    return cast(
        "ResponseSchemaModel[TransportDebugRunPageResponse]",
        response_builder.success(data=data),
    )


@router.get(
    "/debug-runs/stream",
    summary="[ops:transport-debug-run:stream] 实时订阅 Transport 自动联调轮次状态",
    dependencies=[Depends(RequirePermission("ops:transport-debug-run:stream"))],
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
    },
)
async def transport_debug_run_stream(request: Request) -> StreamingResponse:
    async def event_generator():
        async for envelope in _stream_service(request).subscribe(
            TRANSPORT_DEBUG_RUN_STREAM_CHANNEL,
            timeout_seconds=SSE_HEARTBEAT_INTERVAL_SECONDS,
        ):
            if envelope is None:
                yield ": heartbeat\n\n"
                continue
            event = _parse_update(envelope.get("type"), envelope.get("payload"))
            if event is None:
                continue
            payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            yield f"event: transport_debug_run.updated\ndata: {payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/debug-runs/{run_id}",
    summary="[ops:transport-debug-run:read] 查询 Transport 自动联调轮次详情",
    response_model=ResponseSchemaModel[TransportDebugRunResponse],
    responses={
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "自动联调轮次不存在"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport-debug-run:read"))],
)
async def get_transport_debug_run(
    request: Request,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[TransportDebugRunResponse]:
    snapshot = await _debug_run_service(request).get_run(run_id)
    data = TransportDebugRunResponse.model_validate(snapshot, from_attributes=True)
    return cast("ResponseSchemaModel[TransportDebugRunResponse]", response_builder.success(data=data))


@router.post(
    "/debug-runs/{run_id}/abort",
    summary="[ops:transport-debug-run:abort] 安全终止 Transport 自动联调轮次",
    response_model=ResponseSchemaModel[TransportDebugRunResponse],
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "终止确认不满足合同"},
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "自动联调轮次不存在"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "关联 Transport 事实不允许终止"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Transport runtime 不可用"},
    },
    dependencies=[Depends(RequirePermission("ops:transport-debug-run:abort"))],
)
async def abort_transport_debug_run(
    request: Request,
    payload: AbortTransportDebugRunRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[TransportDebugRunResponse]:
    try:
        snapshot = await _debug_run_service(request).abort_run(
            run_id,
            assertion=payload.assertion,
            reason=payload.reason,
            actor_id=request.state.user_id,
        )
    except TransportDebugRunConflict as error:
        raise ConflictException(str(error)) from error
    except TransportDebugRunContractError as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error
    data = TransportDebugRunResponse.model_validate(snapshot, from_attributes=True)
    return cast("ResponseSchemaModel[TransportDebugRunResponse]", response_builder.success(data=data))


__all__ = ["router"]
