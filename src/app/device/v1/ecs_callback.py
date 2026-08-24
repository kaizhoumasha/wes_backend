"""ECS → WES 的两个固定 callback route。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from src.app.device.contracts import (
    DeviceEvidenceReceipt,
    DeviceIngressAttempt,
    DeviceIngressDisposition,
    DeviceIngressKind,
    EcsCommandResultReport,
    EcsDeviceEventReport,
)
from src.app.device.services.device_evidence_service import (
    DeviceEvidenceConflictError,
    DeviceResultConflictError,
    DeviceResultOutOfOrderError,
    UnknownDeviceCommandError,
)
from src.app.sys.services.event_stream_service import DEVICE_EVIDENCE_STREAM_CHANNEL, event_stream_service
from src.core.logger import logger
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

_BODY_LIMIT_BYTES = 256 * 1024
_ATTEMPT_EVENT_TYPE = "device_ingress.attempted"
_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "cookie", "password", "refresh_token", "secret", "set_cookie", "token"}
)
_SENSITIVE_PAYLOAD_SEGMENTS = frozenset({"authorization", "cookie", "password", "secret", "token"})

router = APIRouter()


class EvidenceIngressPort(Protocol):
    async def accept_result(self, result: EcsCommandResultReport) -> DeviceEvidenceReceipt: ...

    async def accept_event(self, event: EcsDeviceEventReport) -> DeviceEvidenceReceipt: ...


class EventPublisherPort(Protocol):
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, Any]) -> bool: ...


class EcsCallbackValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    code: str
    expected: str | None = None


class EcsCallbackRejectionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issues: tuple[EcsCallbackValidationIssue, ...]


class EcsCallbackAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    error_detail: EcsCallbackRejectionDetail | None = None


@dataclass(frozen=True, slots=True)
class _DecodedCallback:
    model: BaseModel
    raw_payload: dict[str, Any]
    observed_body_bytes: int


class EcsCallbackRejection(RuntimeError):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        observed_body_bytes: int = 0,
        issues: tuple[EcsCallbackValidationIssue, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.observed_body_bytes = observed_body_bytes
        self.issues = issues


def _evidence_service(request: Request) -> EvidenceIngressPort:
    service = getattr(request.app.state, "device_evidence_service", None)
    if service is None:
        raise EcsCallbackRejection(503, "TEMPORARILY_UNAVAILABLE")
    return cast("EvidenceIngressPort", service)


def _event_publisher(request: Request) -> EventPublisherPort:
    publisher = getattr(request.app.state, "device_event_stream_service", event_stream_service)
    return cast("EventPublisherPort", publisher)


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_json_issue() -> EcsCallbackValidationIssue:
    return EcsCallbackValidationIssue(field="$", code="INVALID_JSON")


def _log_invalid_envelope(model: type[BaseModel], issues: tuple[EcsCallbackValidationIssue, ...]) -> None:
    summary = ",".join(f"{issue.field}:{issue.code}" for issue in issues)
    logger.warning(f"device.ingress.invalid_envelope model={model.__name__} issues={summary}")


def _validation_issue(issue: dict[str, Any], payload: dict[str, Any]) -> EcsCallbackValidationIssue:
    issue_type = issue["type"]
    location = issue["loc"]
    if issue_type == "extra_forbidden":
        parent = ".".join(str(part) for part in location[:-1])
        field = f"{parent}.<extra>" if parent else "$.<extra>"
        return EcsCallbackValidationIssue(field=field, code="EXTRA_FORBIDDEN")

    field = ".".join(str(part) for part in location) or "$"
    if issue_type == "missing":
        return EcsCallbackValidationIssue(field=field, code="FIELD_REQUIRED")
    expected = {"dict_type": "object", "model_type": "object", "int_type": "integer", "string_type": "string"}.get(
        issue_type
    )
    if expected is not None:
        return EcsCallbackValidationIssue(field=field, code="INVALID_TYPE", expected=expected)
    if (
        issue_type == "value_error"
        and field == "$"
        and payload.get("result") == "FAILED"
        and payload.get("error_detail") is None
    ):
        return EcsCallbackValidationIssue(field="error_detail", code="FIELD_REQUIRED")
    return EcsCallbackValidationIssue(field=field, code="INVALID_VALUE")


async def _decode_closed_body(request: Request, model: type[BaseModel]) -> _DecodedCallback:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _BODY_LIMIT_BYTES:
            raise EcsCallbackRejection(413, "PAYLOAD_TOO_LARGE", observed_body_bytes=size)
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_standard_json_constant,
            parse_float=_parse_finite_json_float,
        )
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        issues = (_invalid_json_issue(),)
        _log_invalid_envelope(model, issues)
        raise EcsCallbackRejection(
            400,
            "INVALID_ENVELOPE",
            observed_body_bytes=size,
            issues=issues,
        ) from error
    if not isinstance(payload, dict):
        issues = (EcsCallbackValidationIssue(field="$", code="INVALID_TYPE", expected="object"),)
        _log_invalid_envelope(model, issues)
        raise EcsCallbackRejection(
            400,
            "INVALID_ENVELOPE",
            observed_body_bytes=size,
            issues=issues,
        )
    try:
        validated = model.model_validate(payload)
    except ValidationError as error:
        validation_errors = error.errors(include_url=False, include_context=False, include_input=False)
        mapped_issues = tuple(_validation_issue(issue, payload) for issue in validation_errors)
        _log_invalid_envelope(model, mapped_issues)
        raise EcsCallbackRejection(
            400,
            "INVALID_ENVELOPE",
            observed_body_bytes=size,
            issues=mapped_issues,
        ) from error
    except RecursionError as error:
        issues = (_invalid_json_issue(),)
        _log_invalid_envelope(model, issues)
        raise EcsCallbackRejection(
            400,
            "INVALID_ENVELOPE",
            observed_body_bytes=size,
            issues=issues,
        ) from error
    return _DecodedCallback(
        model=validated,
        raw_payload=cast("dict[str, Any]", payload),
        observed_body_bytes=size,
    )


def _ack(_receipt: DeviceEvidenceReceipt) -> EcsCallbackAck:
    return EcsCallbackAck(code=200, message="ACK")


def _as_ingress_rejection(error: Exception) -> EcsCallbackRejection | None:
    if isinstance(error, EcsCallbackRejection):
        return error
    if isinstance(error, UnknownDeviceCommandError):
        return EcsCallbackRejection(404, "COMMAND_NOT_FOUND")
    if isinstance(error, DeviceResultOutOfOrderError):
        return EcsCallbackRejection(409, "RESULT_BEFORE_DISPATCH")
    if isinstance(error, (DeviceEvidenceConflictError, DeviceResultConflictError)):
        return EcsCallbackRejection(409, "IDEMPOTENCY_CONFLICT")
    return None


def _rejection_response(error: EcsCallbackRejection) -> JSONResponse:
    error_detail = EcsCallbackRejectionDetail(issues=error.issues) if error.issues else None
    return JSONResponse(
        status_code=error.status_code,
        content=EcsCallbackAck(
            code=error.status_code,
            message=error.message,
            error_detail=error_detail,
        ).model_dump(mode="json", exclude_none=True),
    )


def _redact_diagnostic_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            snake_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
            normalized_key = re.sub(r"[^a-zA-Z0-9]+", "_", snake_key).casefold().strip("_")
            key_segments = frozenset(normalized_key.split("_"))
            redacted[key] = (
                "[REDACTED]"
                if normalized_key in _SENSITIVE_PAYLOAD_KEYS
                or "api_key" in normalized_key
                or bool(key_segments & _SENSITIVE_PAYLOAD_SEGMENTS)
                else _redact_diagnostic_payload(item)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_diagnostic_payload(item) for item in value]
    return value


async def _publish_attempt(
    request: Request,
    *,
    request_id: str,
    received_at: str,
    kind: DeviceIngressKind,
    decoded: _DecodedCallback | None,
    receipt: DeviceEvidenceReceipt | None,
    disposition: DeviceIngressDisposition,
    status_code: int,
    error_code: str | None,
    observed_body_bytes: int,
) -> None:
    model = decoded.model if decoded is not None else None
    attempt = DeviceIngressAttempt(
        request_id=request_id,
        kind=kind,
        path=request.url.path,
        received_at=received_at,
        disposition=disposition,
        status_code=status_code,
        evidence_id=receipt.evidence_id if receipt is not None else None,
        source_event_id=receipt.source_event_id if receipt is not None else None,
        device_code=getattr(model, "device_code", None),
        command_code=getattr(model, "command_code", None),
        event_type=getattr(model, "event_type", None),
        apply_status=receipt.apply_status if receipt is not None else None,
        error_code=error_code,
        observed_body_bytes=observed_body_bytes,
        raw_payload=_redact_diagnostic_payload(decoded.raw_payload) if decoded is not None else None,
    )
    try:
        _ = await _event_publisher(request).publish_to(
            DEVICE_EVIDENCE_STREAM_CHANNEL,
            _ATTEMPT_EVENT_TYPE,
            attempt.model_dump(mode="json"),
        )
    except Exception:
        logger.exception("device.ingress.attempt_publish_failed")


async def _handle_callback(
    request: Request,
    *,
    model: type[BaseModel],
    kind: DeviceIngressKind,
    accept_method: str,
) -> EcsCallbackAck | JSONResponse:
    request_id = new_uuid7()
    received_at = timezone.now_utc().isoformat()
    decoded: _DecodedCallback | None = None
    receipt: DeviceEvidenceReceipt | None = None
    try:
        decoded = await _decode_closed_body(request, model)
        accept = getattr(_evidence_service(request), accept_method)
        receipt = await accept(decoded.model)
    except Exception as error:
        rejection = _as_ingress_rejection(error)
        rejection_receipt = getattr(error, "receipt", None)
        if rejection is None:
            await _publish_attempt(
                request,
                request_id=request_id,
                received_at=received_at,
                kind=kind,
                decoded=decoded,
                receipt=None,
                disposition=DeviceIngressDisposition.REJECTED,
                status_code=500,
                error_code="TEMPORARILY_UNAVAILABLE",
                observed_body_bytes=decoded.observed_body_bytes if decoded is not None else 0,
            )
            raise
        await _publish_attempt(
            request,
            request_id=request_id,
            received_at=received_at,
            kind=kind,
            decoded=decoded,
            receipt=rejection_receipt,
            disposition=(
                DeviceIngressDisposition.CONFLICT if rejection.status_code == 409 else DeviceIngressDisposition.REJECTED
            ),
            status_code=rejection.status_code,
            error_code=rejection.message,
            observed_body_bytes=(decoded.observed_body_bytes if decoded is not None else rejection.observed_body_bytes),
        )
        return _rejection_response(rejection)

    if receipt is None or decoded is None:
        raise RuntimeError("callback evidence ingress 未产生确定结果")
    await _publish_attempt(
        request,
        request_id=request_id,
        received_at=received_at,
        kind=kind,
        decoded=decoded,
        receipt=receipt,
        disposition=(DeviceIngressDisposition.DUPLICATE if receipt.duplicate else DeviceIngressDisposition.ACCEPTED),
        status_code=200,
        error_code=None,
        observed_body_bytes=decoded.observed_body_bytes,
    )
    return _ack(receipt)


@router.post("/result", response_model=EcsCallbackAck, response_model_exclude_none=True)
async def accept_device_result(request: Request) -> EcsCallbackAck | JSONResponse:
    return await _handle_callback(
        request,
        model=EcsCommandResultReport,
        kind=DeviceIngressKind.DEVICE_RESULT,
        accept_method="accept_result",
    )


@router.post("/event", response_model=EcsCallbackAck, response_model_exclude_none=True)
async def accept_device_event(request: Request) -> EcsCallbackAck | JSONResponse:
    return await _handle_callback(
        request,
        model=EcsDeviceEventReport,
        kind=DeviceIngressKind.DEVICE_EVENT,
        accept_method="accept_event",
    )


__all__ = ["EcsCallbackAck", "router"]
