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
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
    WmsOperationDefinition,
    WmsOperationMode,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    WmsAsyncSubmitReject,
    WmsEffectAck,
    validate_effect_ack,
    validate_fulfillment_ack,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload

if TYPE_CHECKING:
    from src.app.sys.external_http_transport import ExternalHttpTransportResult

type WmsSyncEffectOutcome = Success[Any] | BusinessReject | RetryableFailure | ContractViolation
type WmsAsyncEffectAckOutcome = Success[WmsEffectAck] | BusinessReject | ContractViolation


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


def typed_wms_effect_ack_hash(ack: WmsEffectAck) -> str:
    """对 ACK 的稳定关联身份计算 hash；允许 submission_state 随合法重放演进。"""

    identity = ack.model_dump(mode="json", exclude={"submission_state"})
    return CanonicalPayload.from_projection(identity).sha256


def interpret_async_effect_ack_response(
    operation: WmsOperationDefinition,
    request_payload: dict[str, Any],
    *,
    idempotency_key: str,
    payload_hash: str,
    transport_result: ExternalHttpTransportResult,
) -> WmsAsyncEffectAckOutcome:
    """仅接受 ASYNC_TASK 静态矩阵中的 typed submit ACK。"""

    if operation.mode is not WmsOperationMode.EFFECT or operation.completion_mode is not WmsCompletionMode.ASYNC_TASK:
        return ContractViolation(
            error_code="WMS_ASYNC_OPERATION_INVALID",
            message="async ACK interpreter received a non-async operation",
        )
    try:
        if CanonicalPayload.from_projection(request_payload).sha256 != payload_hash:
            raise ValueError("payload fingerprint differs from the frozen request")
        request = validate_json_payload(operation.request_model, request_payload)
    except (TypeError, ValueError, ValidationError):
        return ContractViolation(
            error_code="WMS_ASYNC_FROZEN_REQUEST_INVALID",
            message="async ACK request identity differs from the frozen payload fingerprint",
        )
    payload = _decode_response_body(transport_result)
    if payload is None:
        return ContractViolation(
            error_code="WMS_ASYNC_ACK_MALFORMED",
            message="WMS async submit ACK must be a bounded JSON object",
        )
    protocol_error_code = transport_result.protocol_error_code
    body_reason_code = payload.get("reason_code")
    expected_submission_state: str | None
    direct_outcome: WmsAsyncEffectAckOutcome | None = None
    if transport_result.http_status_code == 202 and transport_result.protocol_result.value == "ACCEPTED":
        expected_submission_state = "ACCEPTED"
    elif transport_result.http_status_code == 409 and protocol_error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS":
        expected_submission_state = "IN_PROGRESS_REPLAY"
    elif transport_result.http_status_code == 200 and transport_result.protocol_result.value == "ACCEPTED":
        expected_submission_state = "REPLAY"
    elif transport_result.http_status_code == 422 and protocol_error_code == "IDEMPOTENCY_CONFLICT":
        return ContractViolation(
            error_code="IDEMPOTENCY_CONFLICT",
            message="WMS rejected reuse of the idempotency key with a different request",
        )
    elif (
        transport_result.http_status_code == 422
        and transport_result.protocol_result.value == "REJECTED"
        and body_reason_code in operation.reject_codes
    ):
        try:
            reject = WmsAsyncSubmitReject.model_validate(payload)
            if reject.operation_identity != operation.identity:
                raise ValueError("async reject operation identity differs from the frozen request")
            if reject.idempotency_key != idempotency_key:
                raise ValueError("async reject idempotency key differs from the frozen request")
            if reject.request_fingerprint != payload_hash:
                raise ValueError("async reject fingerprint differs from the frozen request")
        except (TypeError, ValueError, ValidationError):
            direct_outcome = ContractViolation(
                error_code="WMS_ASYNC_REJECT_IDENTITY_INVALID",
                message="WMS async business reject differs from the frozen request identity",
            )
        else:
            direct_outcome = BusinessReject(
                reason_code=reject.reason_code,
                message=reject.message,
                details={
                    "typed_reject_hash": CanonicalPayload.from_projection(reject.model_dump(mode="json")).sha256,
                },
            )
    else:
        return ContractViolation(
            error_code="WMS_ASYNC_ACK_UNCLASSIFIED",
            message="WMS async submit response status/code combination is not authored",
        )
    if direct_outcome is not None:
        return direct_outcome
    try:
        ack = WmsEffectAck.model_validate(payload)
        validate_effect_ack(
            operation_identity=operation.identity,
            idempotency_key=idempotency_key,
            ack=ack,
        )
        if ack.submission_state != expected_submission_state:
            raise ValueError("ACK submission_state differs from the authored HTTP matrix")
        if ack.accepted_scope is not None:
            validate_fulfillment_ack(request, ack)  # type: ignore[arg-type]
    except (TypeError, ValueError, ValidationError):
        return ContractViolation(
            error_code="WMS_ASYNC_ACK_IDENTITY_INVALID",
            message="WMS async submit ACK differs from the frozen operation identity",
        )
    return Success(payload=ack)


def validate_effect_terminal_result(
    operation: WmsOperationDefinition,
    request: BaseModel,
    payload: object,
) -> BaseModel:
    """用 operation-specific model 和冻结 request identity 校验终态结果。"""

    result = validate_json_payload(operation.result_model, payload)
    if getattr(result, "dispatch_key", None) != getattr(request, "dispatch_key", None):
        raise ValueError("WMS terminal dispatch_key differs from the frozen request")
    validator = operation.terminal_identity_validator
    if validator is None:
        raise ValueError("WMS terminal identity validator is missing from the static definition")
    validator(request, result)
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
    "WmsAsyncEffectAckOutcome",
    "WmsSyncEffectOutcome",
    "interpret_async_effect_ack_response",
    "interpret_sync_effect_response",
    "typed_wms_effect_ack_hash",
    "validate_effect_terminal_result",
]
