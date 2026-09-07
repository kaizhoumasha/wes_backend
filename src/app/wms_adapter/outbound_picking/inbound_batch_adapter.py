"""`outbound.bin.inbound_batch@v1` 可靠派发适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import (
    BIN_INBOUND_BATCH_OPERATION,
    parse_bin_inbound_batch_request,
    parse_bin_inbound_batch_response,
)
from src.app.wms_adapter.wire_common import DECISION_PATH
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient


class BinInboundBatchAdapter:
    """校验冻结请求，通过共享 WmsClient 单次发送并解释 inbound_batch 响应。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> WmsDispatchResult:
        try:
            request = parse_bin_inbound_batch_request(request_payload)
        except (ValidationError, ValueError, TypeError):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        if (
            operation != BIN_INBOUND_BATCH_OPERATION
            or request.operation != operation
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)

        access = await receive_json(self._client, DECISION_PATH, request.model_dump(mode="json"))
        if isinstance(access, WmsDispatchResult):
            return access
        received_json = dict(access.json_body) if isinstance(access.json_body, dict) else None
        try:
            response = parse_bin_inbound_batch_response(access.status_code or 0, access.json_body, request=request)
        except (ValidationError, ValueError, TypeError):
            return WmsDispatchResult(
                WmsDispatchCode.RECONCILING,
                normalized_response=received_json,
            )
        normalized = response.model_dump(mode="json", exclude_unset=True)
        if response.code == "DECIDED":
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response.data.result,
            )
        if response.code == "UNAVAILABLE":
            return WmsDispatchResult(
                WmsDispatchCode.RETRY,
                normalized_response=normalized,
            )
        return WmsDispatchResult(
            WmsDispatchCode.RECONCILING,
            normalized_response=normalized,
        )


__all__ = [
    "BinInboundBatchAdapter",
]
