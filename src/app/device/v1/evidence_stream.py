"""超级用户专用的 device ingress 历史与 live-only SSE。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, StringConstraints, ValidationError
from starlette.responses import StreamingResponse

from src.app.device.contracts import (
    DeviceEvidenceKind,
    DeviceEvidenceUpdate,
    DeviceIngressAttempt,
    DeviceIngressHistoryPage,
)
from src.app.device.services.device_ingress_history_service import device_ingress_history_service
from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus  # noqa: TC001
from src.app.sys.services.event_stream_service import (
    DEVICE_EVIDENCE_STREAM_CHANNEL,
    event_stream_service,
)
from src.core.exceptions import ValidationException
from src.core.logger import logger
from src.core.rbac import require_superuser
from src.core.response import ResponseSchemaModel, response_builder

router = APIRouter(tags=["Device ingress diagnostics"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 25.0
_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"  # noqa: S105  # nosec B105 - token regex
_DEVICE_TOKEN = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=_TOKEN_PATTERN)]
_COMMAND_TOKEN = Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=_TOKEN_PATTERN)]


class EventStreamPort(Protocol):
    def subscribe(self, channel: str, *, timeout_seconds: float) -> AsyncIterator[dict[str, Any] | None]: ...


def _stream_service(request: Request) -> EventStreamPort:
    service = getattr(request.app.state, "device_event_stream_service", event_stream_service)
    return cast("EventStreamPort", service)


def _parse_device_event(event_type: object, payload: object) -> BaseModel | None:
    if not isinstance(payload, dict):
        return None
    try:
        if event_type == "device_ingress.attempted":
            return DeviceIngressAttempt.model_validate(payload)
        if event_type == "device_evidence.updated":
            return DeviceEvidenceUpdate.model_validate(payload)
    except ValidationError as error:
        logger.warning(f"Device evidence SSE 跳过非法 payload: {error}")
    return None


def _matches_filters(
    event: BaseModel,
    *,
    device_code: str | None,
    kind: DeviceEvidenceKind | None,
    command_code: str | None,
    apply_status: InboundEvidenceApplyStatus | None,
) -> bool:
    return (
        (device_code is None or getattr(event, "device_code", None) == device_code)
        and (kind is None or getattr(event, "kind", None) == kind)
        and (command_code is None or getattr(event, "command_code", None) == command_code)
        and (apply_status is None or getattr(event, "apply_status", None) == apply_status.value)
    )


@router.get(
    "/evidences/history",
    summary="查询设备 callback 近期历史与当前 Evidence 状态",
    dependencies=[Depends(require_superuser)],
    response_model=ResponseSchemaModel[DeviceIngressHistoryPage],
)
async def evidence_history(
    request: Request,
    device_code: _DEVICE_TOKEN | None = Query(default=None),
    kind: DeviceEvidenceKind | None = Query(default=None),
    command_code: _COMMAND_TOKEN | None = Query(default=None),
    apply_status: InboundEvidenceApplyStatus | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
) -> ResponseSchemaModel[DeviceIngressHistoryPage]:
    service = getattr(request.app.state, "device_ingress_history_service", device_ingress_history_service)
    try:
        page = await service.list_history(
            device_code=device_code,
            kind=kind,
            command_code=command_code,
            apply_status=apply_status,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error
    return cast("ResponseSchemaModel[DeviceIngressHistoryPage]", response_builder.success(data=page))


@router.get(
    "/evidences/stream",
    summary="实时订阅 ECS callback 与 evidence 应用状态",
    dependencies=[Depends(require_superuser)],
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def evidence_stream(
    request: Request,
    device_code: _DEVICE_TOKEN | None = Query(default=None),  # pyright: ignore[reportCallInDefaultInitializer]
    kind: DeviceEvidenceKind | None = Query(default=None),  # pyright: ignore[reportCallInDefaultInitializer]
    command_code: _COMMAND_TOKEN | None = Query(default=None),  # pyright: ignore[reportCallInDefaultInitializer]
    apply_status: InboundEvidenceApplyStatus | None = Query(  # pyright: ignore[reportCallInDefaultInitializer]
        default=None
    ),
) -> StreamingResponse:
    async def event_generator():
        async for envelope in _stream_service(request).subscribe(
            DEVICE_EVIDENCE_STREAM_CHANNEL,
            timeout_seconds=SSE_HEARTBEAT_INTERVAL_SECONDS,
        ):
            if envelope is None:
                yield ": heartbeat\n\n"
                continue
            event = _parse_device_event(envelope.get("type"), envelope.get("payload"))
            if event is None or not _matches_filters(
                event,
                device_code=device_code,
                kind=kind,
                command_code=command_code,
                apply_status=apply_status,
            ):
                continue
            payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            yield f"event: {envelope['type']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
