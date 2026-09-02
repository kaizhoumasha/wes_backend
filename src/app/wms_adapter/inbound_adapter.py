"""共享 WMS operation 可靠派发适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

from src.app.wms_adapter.client import (
    OutboundHttpClosedError,
    WmsClient,
    WmsRequestBodyTooLargeError,
)
from src.app.wms_adapter.inbound_wire import (
    DECISION_OPERATIONS,
    DECISION_PATH,
    FACT_OPERATIONS,
    FACT_PATH,
    MAX_INBOUND_BODY_BYTES,
    parse_outbound_request,
    parse_outbound_response,
)
from src.app.wms_adapter.strict_json import valid_json_response_headers
from src.core.outbound_http import OutboundHttpDeliveryState
from src.utils.canonical_json import canonical_json_digest


class InboundDispatchCode(str, Enum):
    DETERMINATE = "DETERMINATE"
    RETRY = "RETRY"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class InboundDispatchResult:
    code: InboundDispatchCode
    normalized_response: dict[str, Any] | None = None
    response_result: str | None = None
    retry_after_ms: int | None = None


class WmsInboundAdapter:
    """按共享 operation 合同校验请求、调用 WmsClient 并解释响应。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(  # noqa: PLR0911 - 每个 fail-closed 分支保留明确传输语义。
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> InboundDispatchResult:
        try:
            request = parse_outbound_request(request_payload)
        except (ValidationError, ValueError, TypeError):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        if (
            request.operation != operation
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        if operation in DECISION_OPERATIONS:
            path = DECISION_PATH
        elif operation in FACT_OPERATIONS:
            path = FACT_PATH
        else:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)

        try:
            access = await self._client.post(
                path,
                json=request.model_dump(mode="json", exclude_none=True),
                max_request_body_bytes=MAX_INBOUND_BODY_BYTES,
                max_response_body_bytes=MAX_INBOUND_BODY_BYTES,
            )
        except WmsRequestBodyTooLargeError:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        except OutboundHttpClosedError:
            return InboundDispatchResult(InboundDispatchCode.NOT_SENT)

        if access.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
            return InboundDispatchResult(InboundDispatchCode.NOT_SENT)
        if access.delivery_state is not OutboundHttpDeliveryState.RESPONSE_RECEIVED:
            return InboundDispatchResult(InboundDispatchCode.DELIVERY_UNKNOWN)
        received_json = dict(access.json_body) if isinstance(access.json_body, dict) else None
        if access.status_code in {400, 413} and not access.body_present:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        if access.failure_kind is not None or not valid_json_response_headers(access.response_headers):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=received_json)
        if access.json_failure is not None or not access.body_present or not isinstance(access.json_body, dict):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        try:
            response = parse_outbound_response(operation, access.status_code or 0, access.json_body)
        except (ValidationError, ValueError, TypeError):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=received_json)
        normalized = response.model_dump(mode="json")
        if response.operation_id != operation_id:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=normalized)

        if response.code in {"DECIDED", "RECORDED", "DUPLICATE"}:
            response_result = response.data.result if response.code == "DECIDED" else response.code
            return InboundDispatchResult(
                InboundDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response_result,
                retry_after_ms=(
                    response.data.retry_after_ms if response.code == "DECIDED" and response_result == "WAIT" else None
                ),
            )
        if response.code == "BUSY":
            return InboundDispatchResult(
                InboundDispatchCode.RETRY,
                normalized_response=normalized,
                retry_after_ms=response.data.retry_after_ms,
            )
        if response.code == "UNAVAILABLE":
            return InboundDispatchResult(InboundDispatchCode.RETRY, normalized_response=normalized)
        return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=normalized)


__all__ = ["InboundDispatchCode", "InboundDispatchResult", "WmsInboundAdapter"]
