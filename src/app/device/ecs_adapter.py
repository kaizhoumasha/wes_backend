"""固定 uniform-wire 的唯一 ECS 出站 Adapter。"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.app.device.contracts import EcsDeviceStatus, EcsSubmitDisposition, EcsSubmitResult
from src.core.outbound_http import (
    OutboundHttpDeliveryState,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpResponseLimits,
    OutboundHttpResult,
    OutboundHttpTransport,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

COMMAND_PATH = "/api/v1/device/command"
STATUS_PATH = "/api/v1/device/status"
_WIRE_BODY_LIMIT_BYTES = 256 * 1024
_WIRE_RESPONSE_LIMITS = OutboundHttpResponseLimits(
    max_chunk_bytes=64 * 1024,
    max_wire_bytes=_WIRE_BODY_LIMIT_BYTES,
    max_decoded_bytes=_WIRE_BODY_LIMIT_BYTES,
    max_compression_ratio=20.0,
)
_CONTRACT_REJECTION_CODES = frozenset({400, 404, 405, 413, 422})
_RETRYABLE_NOT_ACCEPTED_CODES = frozenset({429, 503})
_DELIVERY_UNKNOWN_CODES = frozenset({409, 500, 502, 504})
_FIXED_ERROR_MESSAGES = {
    400: "INVALID_ENVELOPE",
    404: "DEVICE_NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "IDEMPOTENCY_CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "ANNEX_VALIDATION_FAILED",
    429: "CAPACITY_EXCEEDED",
    500: "TEMPORARILY_UNAVAILABLE",
    502: "TEMPORARILY_UNAVAILABLE",
    503: "TEMPORARILY_UNAVAILABLE",
    504: "TEMPORARILY_UNAVAILABLE",
}


class EcsStatusUnavailableError(RuntimeError):
    """无法获得可用于派发准入的可信设备状态。"""


class EcsAdapter:
    """把 DeviceCommand 快照映射到四路径合同中的两个出站路径。"""

    def __init__(self, transport: OutboundHttpTransport, *, clock=timezone.now_for_db) -> None:
        self._transport = transport
        self._clock = clock

    async def submit_command(
        self,
        *,
        device_code: str,
        command_code: str,
        contract_key: str,
        contract_version: str,
        task_type: str,
        timestamp_ms: int,
        params: dict[str, Any],
        trace_id: str | None,
        deadline_at: datetime | None = None,
    ) -> EcsSubmitResult:
        envelope: dict[str, Any] = {
            "device_code": device_code,
            "command_code": command_code,
            "contract_key": contract_key,
            "contract_version": contract_version,
            "task_type": task_type,
            "timestamp": timestamp_ms,
            "params": params,
        }
        if trace_id is not None:
            envelope["trace_id"] = trace_id
        if deadline_at is not None and self._clock() >= deadline_at:
            return EcsSubmitResult(EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED)
        result = await self._transport.send(
            OutboundHttpRequest(
                method=OutboundHttpMethod.POST,
                path=COMMAND_PATH,
                headers=(("content-type", "application/json"),),
                body=_canonical_json_bytes(envelope),
                response_limits=_WIRE_RESPONSE_LIMITS,
            )
        )
        return _classify_submit_result(result)

    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        result = await self._transport.send(
            OutboundHttpRequest(
                method=OutboundHttpMethod.GET,
                path=STATUS_PATH,
                query=(("device_code", device_code),),
                response_limits=_WIRE_RESPONSE_LIMITS,
            )
        )
        if result.delivery_state is not OutboundHttpDeliveryState.RESPONSE_RECEIVED or result.status_code != 200:
            raise EcsStatusUnavailableError("ECS 状态端点未返回可信成功响应")
        if not _is_json_response(result.response_headers):
            raise EcsStatusUnavailableError("ECS 状态响应不是 application/json")
        cache_control = [value.lower() for name, value in result.response_headers if name.lower() == "cache-control"]
        if len(cache_control) != 1 or "no-store" not in {item.strip() for item in cache_control[0].split(",")}:
            raise EcsStatusUnavailableError("ECS 状态响应未按合同禁止缓存")
        try:
            payload = _decode_json_object(result.decoded_body)
            status = EcsDeviceStatus.model_validate(payload)
        except (TypeError, ValueError, ValidationError) as error:
            raise EcsStatusUnavailableError("ECS 状态响应不符合统一合同") from error
        if status.device_code != device_code:
            raise EcsStatusUnavailableError("ECS 状态响应 device_code 与请求不一致")
        return status


def _classify_submit_result(result: OutboundHttpResult) -> EcsSubmitResult:  # noqa: PLR0911
    if result.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
        return EcsSubmitResult(EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED)
    if result.delivery_state is OutboundHttpDeliveryState.DELIVERY_UNKNOWN:
        return EcsSubmitResult(EcsSubmitDisposition.RECONCILING)
    status_code = result.status_code
    if status_code is None:
        return EcsSubmitResult(EcsSubmitDisposition.RECONCILING)
    if not _is_json_response(result.response_headers):
        return EcsSubmitResult(EcsSubmitDisposition.RECONCILING, code=status_code)
    response = _try_common_response(result.decoded_body)
    if status_code == 200:
        if response is None or response[0] != 200 or response[1] != "ACCEPTED":
            return EcsSubmitResult(EcsSubmitDisposition.RECONCILING)
        return EcsSubmitResult(
            EcsSubmitDisposition.ACKNOWLEDGED,
            code=response[0],
            message=response[1],
            trace_id=response[2],
        )
    if status_code in _RETRYABLE_NOT_ACCEPTED_CODES:
        classified = _with_common_response(EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED, response, status_code)
        if status_code != 429 or classified.disposition is not EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED:
            return classified
        retry_after_seconds = _retry_after_seconds(result.response_headers)
        if retry_after_seconds is None:
            return EcsSubmitResult(EcsSubmitDisposition.RECONCILING, code=status_code)
        return EcsSubmitResult(
            classified.disposition,
            code=classified.code,
            message=classified.message,
            trace_id=classified.trace_id,
            retry_after_seconds=retry_after_seconds,
        )
    if status_code in _CONTRACT_REJECTION_CODES:
        return _with_common_response(EcsSubmitDisposition.CONTRACT_REJECTED, response, status_code)
    if status_code in _DELIVERY_UNKNOWN_CODES:
        return _with_common_response(EcsSubmitDisposition.RECONCILING, response, status_code)
    return _with_common_response(EcsSubmitDisposition.RECONCILING, response, status_code)


def _with_common_response(
    disposition: EcsSubmitDisposition,
    response: tuple[int, str, str | None] | None,
    status_code: int,
) -> EcsSubmitResult:
    if response is None or response[0] != status_code or response[1] != _FIXED_ERROR_MESSAGES.get(status_code):
        return EcsSubmitResult(EcsSubmitDisposition.RECONCILING, code=status_code)
    return EcsSubmitResult(disposition, code=response[0], message=response[1], trace_id=response[2])


def _try_common_response(body: bytes | None) -> tuple[int, str, str | None] | None:
    try:
        payload = _decode_json_object(body)
        code = payload["code"]
        message = payload["message"]
        trace_id = payload.get("trace_id")
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(code, int) or isinstance(code, bool) or not isinstance(message, str):
        return None
    if ("trace_id" in payload and trace_id is None) or (
        trace_id is not None
        and (not isinstance(trace_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", trace_id))
    ):
        return None
    if set(payload) - {"code", "message", "trace_id"}:
        return None
    return code, message, trace_id


def _decode_json_object(body: bytes | None) -> dict[str, Any]:
    if body is None or len(body) > _WIRE_BODY_LIMIT_BYTES:
        raise ValueError("响应体缺失或超过上限")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("响应体不是合法 JSON") from error
    if not isinstance(payload, dict):
        raise TypeError("响应体必须是 JSON 对象")
    return payload


def _retry_after_seconds(headers: tuple[tuple[str, str], ...]) -> int | None:
    values = [value for name, value in headers if name.lower() == "retry-after"]
    if len(values) != 1 or not values[0].isdigit():
        return None
    return int(values[0])


def _is_json_response(headers: tuple[tuple[str, str], ...]) -> bool:
    values = [value for name, value in headers if name.lower() == "content-type"]
    return len(values) == 1 and values[0].split(";", 1)[0].strip().lower() == "application/json"


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


__all__ = ["COMMAND_PATH", "STATUS_PATH", "EcsAdapter", "EcsStatusUnavailableError"]
