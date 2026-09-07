"""共享 WMS operation 可靠派发适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.inbound_material.wire import (
    DECISION_OPERATIONS,
    FACT_OPERATIONS,
    OutboundRequest,
    parse_outbound_response,
)
from src.app.wms_adapter.wire_common import DECISION_PATH, FACT_PATH
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient


class InboundMaterialAdapter:
    """按共享 operation 合同校验请求、调用 WmsClient 并解释响应。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def send(  # noqa: PLR0911 - 每个 fail-closed 分支保留明确传输语义。
        self,
        *,
        request: OutboundRequest,
        request_digest: str,
    ) -> WmsDispatchResult:
        request_payload = request.model_dump(mode="json", exclude_none=True)
        operation, operation_id = request.operation, request.operation_id
        if canonical_json_digest(request_payload) != request_digest:
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        if operation in DECISION_OPERATIONS:
            path = DECISION_PATH
        elif operation in FACT_OPERATIONS:
            path = FACT_PATH
        else:
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)

        access = await receive_json(self._client, path, request_payload)
        if isinstance(access, WmsDispatchResult):
            return access
        received_json = dict(access.json_body)
        try:
            response = parse_outbound_response(operation, access.status_code or 0, access.json_body)
        except (ValidationError, ValueError, TypeError):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING, normalized_response=received_json)
        normalized = response.model_dump(mode="json")
        if response.operation_id != operation_id:
            return WmsDispatchResult(WmsDispatchCode.RECONCILING, normalized_response=normalized)

        if response.code in {"DECIDED", "RECORDED", "DUPLICATE"}:
            response_result = response.data.result if response.code == "DECIDED" else response.code
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response_result,
                retry_after_ms=(
                    response.data.retry_after_ms if response.code == "DECIDED" and response_result == "WAIT" else None
                ),
            )
        if response.code == "BUSY":
            return WmsDispatchResult(
                WmsDispatchCode.RETRY,
                normalized_response=normalized,
                retry_after_ms=response.data.retry_after_ms,
            )
        if response.code == "UNAVAILABLE":
            return WmsDispatchResult(WmsDispatchCode.RETRY, normalized_response=normalized)
        return WmsDispatchResult(WmsDispatchCode.RECONCILING, normalized_response=normalized)


__all__ = ["InboundMaterialAdapter", "WmsDispatchCode", "WmsDispatchResult"]
