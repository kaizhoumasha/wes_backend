"""WMS EFFECT 的共享 typed terminal response 解释器。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.app.runtime.system_capabilities.outcomes import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
)
from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
    WmsOperationDefinition,
    WmsOperationMode,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    CancelRequestRequest,
    CancelRequestResult,
    validate_cancel_terminal_result,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload

if TYPE_CHECKING:
    from src.app.sys.external_http_transport import ExternalHttpTransportResult

type WmsSyncEffectOutcome = Success[Any] | BusinessReject | RetryableFailure | ContractViolation


def _decode_response_body(result: ExternalHttpTransportResult) -> dict[str, Any] | None:
    body = result.response_body
    if not isinstance(body, bytes) or not body:
        return None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _stable_code(payload: dict[str, Any], result: ExternalHttpTransportResult) -> str | None:
    for value in (
        payload.get("protocol_error_code"),
        payload.get("reason_code"),
        result.protocol_error_code,
    ):
        if isinstance(value, str) and value:
            return value
    return None


def validate_effect_terminal_result(
    operation: WmsOperationDefinition,
    request: BaseModel,
    payload: object,
) -> BaseModel:
    """用 operation-specific model 和冻结 request identity 校验终态结果。"""

    result = validate_json_payload(operation.result_model, payload)
    if getattr(result, "dispatch_key", None) != getattr(request, "dispatch_key", None):
        raise ValueError("WMS terminal dispatch_key differs from the frozen request")
    if isinstance(request, CancelRequestRequest):
        if not isinstance(result, CancelRequestResult):
            raise TypeError("E16 request requires E16 terminal result")
        validate_cancel_terminal_result(request, result)
    return result


def interpret_sync_effect_response(
    operation: WmsOperationDefinition,
    request: BaseModel,
    transport_result: ExternalHttpTransportResult,
) -> WmsSyncEffectOutcome:
    """按静态 Definition 判别同步 submit 的唯一终态或原键重提语义。"""

    if (
        operation.mode is not WmsOperationMode.EFFECT
        or operation.completion_mode is not WmsCompletionMode.SYNC_RESULT
        or type(request) is not operation.request_model
    ):
        outcome: WmsSyncEffectOutcome = ContractViolation(
            error_code="WMS_SYNC_OPERATION_INVALID",
            message="sync response interpreter received a non-sync operation or mismatched request",
        )
    elif (payload := _decode_response_body(transport_result)) is None:
        outcome = ContractViolation(
            error_code="WMS_MALFORMED_RESPONSE",
            message="WMS sync response must be a bounded JSON object",
        )
    elif transport_result.http_status_code in {200, 201}:
        try:
            typed_result = validate_effect_terminal_result(operation, request, payload)
        except ValidationError:
            outcome = ContractViolation(
                error_code="WMS_MALFORMED_RESPONSE",
                message="WMS sync terminal result does not match the operation contract",
            )
        except (TypeError, ValueError) as exc:
            if "dispatch_key" in str(exc) or "terminal" in str(exc):
                outcome = ContractViolation(
                    error_code="WMS_RESULT_IDENTITY_MISMATCH",
                    message="WMS sync terminal result differs from the frozen request identity",
                )
            else:
                outcome = ContractViolation(
                    error_code="WMS_MALFORMED_RESPONSE",
                    message="WMS sync terminal result does not match the operation contract",
                )
        else:
            outcome = Success(payload=typed_result)
    elif (
        transport_result.http_status_code == 409
        and _stable_code(payload, transport_result) == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    ):
        outcome = RetryableFailure(
            error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
            message="WMS sync request is still processing; retry the frozen submit",
        )
    elif transport_result.http_status_code == 422 and _stable_code(payload, transport_result) == "IDEMPOTENCY_CONFLICT":
        outcome = ContractViolation(
            error_code="IDEMPOTENCY_CONFLICT",
            message="WMS rejected reuse of the idempotency key with a different request",
        )
    elif (
        (code := _stable_code(payload, transport_result)) in operation.reject_codes
        and transport_result.http_status_code is not None
        and 400 <= transport_result.http_status_code < 500
    ):
        message = payload.get("message")
        outcome = BusinessReject(
            reason_code=code,
            message=message if isinstance(message, str) and message else "WMS rejected the request",
        )
    else:
        outcome = ContractViolation(
            error_code="WMS_SYNC_RESPONSE_UNCLASSIFIED",
            message="WMS sync response status/code combination is not authored",
        )
    return outcome


__all__ = [
    "WmsSyncEffectOutcome",
    "interpret_sync_effect_response",
    "validate_effect_terminal_result",
]
