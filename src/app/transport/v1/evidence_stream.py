"""Transport ingress 与 evidence 应用状态的 live-only SSE。"""

from __future__ import annotations

import json
from typing import Protocol, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError
from starlette.responses import StreamingResponse

from src.app.sys.services.event_stream_service import (
    TRANSPORT_EVIDENCE_STREAM_CHANNEL,
    event_stream_service,
)
from src.app.transport.contracts import TransportEvidenceUpdate, TransportIngressAttempt
from src.core.logger import logger
from src.core.rbac import RequirePermission

router = APIRouter(tags=["Transport diagnostics"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 25.0


class EventStreamPort(Protocol):
    def subscribe(self, channel: str, *, timeout_seconds: float): ...


def _stream_service(request: Request) -> EventStreamPort:
    service = getattr(request.app.state, "transport_event_stream_service", event_stream_service)
    return cast("EventStreamPort", service)


def _parse_transport_event(event_type: object, payload: object) -> BaseModel | None:
    if not isinstance(payload, dict):
        return None
    try:
        if event_type == "transport_ingress.attempted":
            return TransportIngressAttempt.model_validate(payload)
        if event_type == "transport_evidence.updated":
            return TransportEvidenceUpdate.model_validate(payload)
    except ValidationError as error:
        logger.warning(f"Transport evidence SSE 跳过非法 payload: {error}")
    return None


@router.get(
    "/evidences/stream",
    summary="[ops:transport-evidence:stream] 实时订阅 WMS Transport callback 与 evidence 应用状态",
    dependencies=[Depends(RequirePermission("ops:transport-evidence:stream"))],
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
    },
)
async def evidence_stream(request: Request) -> StreamingResponse:
    async def event_generator():
        async for envelope in _stream_service(request).subscribe(
            TRANSPORT_EVIDENCE_STREAM_CHANNEL,
            timeout_seconds=SSE_HEARTBEAT_INTERVAL_SECONDS,
        ):
            if envelope is None:
                yield ": heartbeat\n\n"
                continue
            event = _parse_transport_event(envelope.get("type"), envelope.get("payload"))
            if event is None:
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
