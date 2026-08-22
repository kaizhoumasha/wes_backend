"""ECS → WES 的两个固定 callback route。"""

from __future__ import annotations

import json
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from src.app.device.contracts import DeviceEvidenceReceipt, EcsCommandResultReport, EcsDeviceEventReport
from src.app.device.services.device_evidence_service import (
    DeviceEvidenceConflictError,
    DeviceResultConflictError,
    UnknownDeviceCommandError,
)

_BODY_LIMIT_BYTES = 256 * 1024

router = APIRouter()


class EvidenceIngressPort(Protocol):
    async def accept_result(self, result: EcsCommandResultReport) -> DeviceEvidenceReceipt: ...

    async def accept_event(self, event: EcsDeviceEventReport) -> DeviceEvidenceReceipt: ...


class EcsCallbackAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str


def _evidence_service(request: Request) -> EvidenceIngressPort:
    service = getattr(request.app.state, "device_evidence_service", None)
    if service is None:
        raise EcsCallbackRejection(503, "TEMPORARILY_UNAVAILABLE")
    return service


class EcsCallbackRejection(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def _decode_closed_body(request: Request, model: type[BaseModel]) -> BaseModel:
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
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise EcsCallbackRejection(400, "INVALID_ENVELOPE") from error


def _ack(receipt: DeviceEvidenceReceipt) -> EcsCallbackAck:
    return EcsCallbackAck(code=200, message="ACK")


def _raise_ingress_error(error: Exception) -> None:
    if isinstance(error, UnknownDeviceCommandError):
        raise EcsCallbackRejection(404, "COMMAND_NOT_FOUND") from error
    if isinstance(error, (DeviceEvidenceConflictError, DeviceResultConflictError)):
        raise EcsCallbackRejection(409, "IDEMPOTENCY_CONFLICT") from error
    raise error


def _rejection_response(error: EcsCallbackRejection) -> JSONResponse:
    content = {"code": error.status_code, "message": error.message}
    return JSONResponse(
        status_code=error.status_code,
        content=content,
    )


@router.post("/result", response_model=EcsCallbackAck, response_model_exclude_none=True)
async def accept_device_result(request: Request) -> EcsCallbackAck | JSONResponse:
    try:
        result = await _decode_closed_body(request, EcsCommandResultReport)
        receipt = await _evidence_service(request).accept_result(result)  # type: ignore[arg-type]
    except Exception as error:
        try:
            _raise_ingress_error(error)
        except EcsCallbackRejection as rejection:
            return _rejection_response(rejection)
        raise AssertionError("unreachable") from error
    return _ack(receipt)


@router.post("/event", response_model=EcsCallbackAck, response_model_exclude_none=True)
async def accept_device_event(request: Request) -> EcsCallbackAck | JSONResponse:
    try:
        event = await _decode_closed_body(request, EcsDeviceEventReport)
        receipt = await _evidence_service(request).accept_event(event)  # type: ignore[arg-type]
    except Exception as error:
        try:
            _raise_ingress_error(error)
        except EcsCallbackRejection as rejection:
            return _rejection_response(rejection)
        raise AssertionError("unreachable") from error
    return _ack(receipt)


__all__ = ["EcsCallbackAck", "router"]
