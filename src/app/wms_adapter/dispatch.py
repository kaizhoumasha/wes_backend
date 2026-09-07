"""WMS Adapter 共用的单次有界收发；业务响应仍由各 operation 解释。"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.app.wms_adapter.client import OutboundHttpClosedError, WmsAccessResult, WmsClient, WmsRequestBodyTooLargeError
from src.app.wms_adapter.strict_json import valid_json_response_headers
from src.app.wms_adapter.wire_common import MAX_WMS_EVENT_BODY_BYTES
from src.core.outbound_http import OutboundHttpDeliveryState


class WmsDispatchCode(str, Enum):
    DETERMINATE = "DETERMINATE"
    RETRY = "RETRY"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class WmsDispatchResult:
    code: WmsDispatchCode
    normalized_response: dict[str, Any] | None = None
    response_result: str | None = None
    retry_after_ms: int | None = None


async def receive_json(client: WmsClient, path: str, payload: dict[str, Any]) -> WmsAccessResult | WmsDispatchResult:
    try:
        access = await client.post(
            path,
            json=payload,
            max_request_body_bytes=MAX_WMS_EVENT_BODY_BYTES,
            max_response_body_bytes=MAX_WMS_EVENT_BODY_BYTES,
        )
    except WmsRequestBodyTooLargeError:
        return WmsDispatchResult(WmsDispatchCode.RECONCILING)
    except OutboundHttpClosedError:
        return WmsDispatchResult(WmsDispatchCode.NOT_SENT)
    if access.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
        return WmsDispatchResult(WmsDispatchCode.NOT_SENT)
    if access.delivery_state is not OutboundHttpDeliveryState.RESPONSE_RECEIVED:
        return WmsDispatchResult(WmsDispatchCode.DELIVERY_UNKNOWN)
    if access.failure_kind is not None or not valid_json_response_headers(access.response_headers):
        return WmsDispatchResult(
            WmsDispatchCode.RECONCILING,
            normalized_response=dict(access.json_body) if isinstance(access.json_body, dict) else None,
        )
    if access.json_failure is not None or not access.body_present or not isinstance(access.json_body, dict):
        return WmsDispatchResult(WmsDispatchCode.RECONCILING)
    return access
