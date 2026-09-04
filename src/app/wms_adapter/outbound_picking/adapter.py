"""`outbound.picking_task.prepare@v1` 可靠派发适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

from src.app.wms_adapter.client import OutboundHttpClosedError, WmsClient, WmsRequestBodyTooLargeError
from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_PREPARE_OPERATION,
    parse_picking_task_prepare_request,
    parse_picking_task_prepare_response,
)
from src.app.wms_adapter.strict_json import valid_json_response_headers
from src.app.wms_adapter.wire_common import MAX_WMS_EVENT_BODY_BYTES
from src.core.outbound_http import OutboundHttpDeliveryState
from src.utils.canonical_json import canonical_json_digest

PICKING_TASK_PREPARE_PATH = "/api/v1/wes/decisions"


class PickingTaskPrepareDispatchCode(str, Enum):
    DETERMINATE = "DETERMINATE"
    RETRY = "RETRY"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class PickingTaskPrepareDispatchResult:
    code: PickingTaskPrepareDispatchCode
    normalized_response: dict[str, Any] | None = None
    response_result: str | None = None
    retry_after_ms: int | None = None


class PickingTaskPrepareAdapter:
    """校验冻结请求，通过共享 WmsClient 单次发送并解释 prepare 响应。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(  # noqa: PLR0911 - 每个 fail-closed 分支保留明确的传输语义。
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> PickingTaskPrepareDispatchResult:
        try:
            request = parse_picking_task_prepare_request(request_payload)
        except (ValidationError, ValueError, TypeError):
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.RECONCILING)
        if (
            operation != PICKING_TASK_PREPARE_OPERATION
            or request.operation != operation
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.RECONCILING)

        try:
            access = await self._client.post(
                PICKING_TASK_PREPARE_PATH,
                json=request.model_dump(mode="json"),
                max_request_body_bytes=MAX_WMS_EVENT_BODY_BYTES,
                max_response_body_bytes=MAX_WMS_EVENT_BODY_BYTES,
            )
        except WmsRequestBodyTooLargeError:
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.RECONCILING)
        except OutboundHttpClosedError:
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.NOT_SENT)

        if access.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.NOT_SENT)
        if access.delivery_state is not OutboundHttpDeliveryState.RESPONSE_RECEIVED:
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.DELIVERY_UNKNOWN)
        received_json = dict(access.json_body) if isinstance(access.json_body, dict) else None
        if access.failure_kind is not None or not valid_json_response_headers(access.response_headers):
            return PickingTaskPrepareDispatchResult(
                PickingTaskPrepareDispatchCode.RECONCILING,
                normalized_response=received_json,
            )
        if access.json_failure is not None or not access.body_present or not isinstance(access.json_body, dict):
            return PickingTaskPrepareDispatchResult(PickingTaskPrepareDispatchCode.RECONCILING)
        try:
            response = parse_picking_task_prepare_response(access.status_code or 0, access.json_body)
        except (ValidationError, ValueError, TypeError):
            return PickingTaskPrepareDispatchResult(
                PickingTaskPrepareDispatchCode.RECONCILING,
                normalized_response=received_json,
            )
        normalized = response.model_dump(mode="json")
        if response.operation_id != operation_id:
            return PickingTaskPrepareDispatchResult(
                PickingTaskPrepareDispatchCode.RECONCILING,
                normalized_response=normalized,
            )
        if response.code == "PREPARE_ACCEPTED":
            return PickingTaskPrepareDispatchResult(
                PickingTaskPrepareDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response.code,
            )
        if response.code == "UNAVAILABLE":
            return PickingTaskPrepareDispatchResult(
                PickingTaskPrepareDispatchCode.RETRY,
                normalized_response=normalized,
            )
        return PickingTaskPrepareDispatchResult(
            PickingTaskPrepareDispatchCode.RECONCILING,
            normalized_response=normalized,
        )


__all__ = [
    "PICKING_TASK_PREPARE_PATH",
    "PickingTaskPrepareAdapter",
    "PickingTaskPrepareDispatchCode",
    "PickingTaskPrepareDispatchResult",
]
