"""ECS → WES 的两个固定 callback route。"""

from __future__ import annotations

import json
import re
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from src.app.device.contracts import DeviceEvidenceReceipt, EcsCommandResult, EcsDeviceEvent
from src.app.device.services.device_evidence_service import (
    DeviceEventContractMismatchError,
    DeviceEvidenceConflictError,
    DeviceResultConflictError,
    UnknownDeviceCommandError,
)

_BODY_LIMIT_BYTES = 256 * 1024

router = APIRouter()


class EvidenceIngressPort(Protocol):
    async def accept_result(self, result: EcsCommandResult) -> DeviceEvidenceReceipt: ...

    async def accept_event(self, event: EcsDeviceEvent) -> DeviceEvidenceReceipt: ...


class EcsCallbackAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    trace_id: str | None = None


def _evidence_service(request: Request) -> EvidenceIngressPort:
    service = getattr(request.app.state, "device_evidence_service", None)
    if service is None:
        raise EcsCallbackRejection(503, "TEMPORARILY_UNAVAILABLE")
    return service


class EcsCallbackRejection(RuntimeError):
    def __init__(self, status_code: int, message: str, *, trace_id: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.trace_id = trace_id


async def _decode_closed_body(request: Request, model: type[BaseModel]) -> BaseModel:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise EcsCallbackRejection(400, "INVALID_ENVELOPE")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _BODY_LIMIT_BYTES:
            raise EcsCallbackRejection(413, "PAYLOAD_TOO_LARGE")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EcsCallbackRejection(400, "INVALID_ENVELOPE") from error
    trace_id = payload.get("trace_id") if isinstance(payload, dict) else None
    if not isinstance(trace_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", trace_id):
        trace_id = None
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise EcsCallbackRejection(400, "INVALID_ENVELOPE", trace_id=trace_id) from error


def _ack(receipt: DeviceEvidenceReceipt) -> EcsCallbackAck:
    return EcsCallbackAck(code=200, message="ACK", trace_id=receipt.trace_id)


def _raise_ingress_error(error: Exception) -> None:
    if isinstance(error, UnknownDeviceCommandError):
        raise EcsCallbackRejection(404, "COMMAND_NOT_FOUND") from error
    if isinstance(error, (DeviceEvidenceConflictError, DeviceResultConflictError)):
        raise EcsCallbackRejection(409, "IDEMPOTENCY_CONFLICT") from error
    if isinstance(error, DeviceEventContractMismatchError):
        raise EcsCallbackRejection(422, "ANNEX_VALIDATION_FAILED") from error
    raise error


def _rejection_response(error: EcsCallbackRejection) -> JSONResponse:
    content = {"code": error.status_code, "message": error.message}
    if error.trace_id is not None:
        content["trace_id"] = error.trace_id
    return JSONResponse(
        status_code=error.status_code,
        content=content,
    )


@router.post("/result", response_model=EcsCallbackAck, response_model_exclude_none=True)
async def accept_device_result(request: Request) -> EcsCallbackAck | JSONResponse:
    trace_id = None
    try:
        result = await _decode_closed_body(request, EcsCommandResult)
        trace_id = result.trace_id  # type: ignore[attr-defined]
        receipt = await _evidence_service(request).accept_result(result)  # type: ignore[arg-type]
    except Exception as error:
        try:
            _raise_ingress_error(error)
        except EcsCallbackRejection as rejection:
            rejection.trace_id = rejection.trace_id or trace_id
            return _rejection_response(rejection)
        raise AssertionError("unreachable") from error
    return _ack(receipt)


@router.post("/event", response_model=EcsCallbackAck, response_model_exclude_none=True)
async def accept_device_event(request: Request) -> EcsCallbackAck | JSONResponse:
    trace_id = None
    try:
        event = await _decode_closed_body(request, EcsDeviceEvent)
        trace_id = event.trace_id  # type: ignore[attr-defined]
        receipt = await _evidence_service(request).accept_event(event)  # type: ignore[arg-type]
    except Exception as error:
        try:
            _raise_ingress_error(error)
        except EcsCallbackRejection as rejection:
            rejection.trace_id = rejection.trace_id or trace_id
            return _rejection_response(rejection)
        raise AssertionError("unreachable") from error
    return _ack(receipt)


__all__ = ["EcsCallbackAck", "router"]
