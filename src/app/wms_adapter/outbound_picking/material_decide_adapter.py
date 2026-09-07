"""出库物料决定的单次派发，不拥有扫码台或设备动作。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.outbound_picking.material_decide_wire import (
    MATERIAL_DECIDE_OPERATION,
    parse_material_decide_request,
    parse_material_decide_response,
)
from src.app.wms_adapter.wire_common import DECISION_PATH
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient


class PickingMaterialDecideAdapter:
    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(
        self, *, operation: str, operation_id: str, request_payload: dict[str, Any], request_digest: str
    ) -> WmsDispatchResult:
        try:
            request = parse_material_decide_request(request_payload)
        except (ValueError, TypeError):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        if (
            operation != MATERIAL_DECIDE_OPERATION
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        access = await receive_json(self._client, DECISION_PATH, request.model_dump(mode="json"))
        if isinstance(access, WmsDispatchResult):
            return access
        try:
            response = parse_material_decide_response(access.status_code or 0, access.json_body, request=request)
        except (ValueError, TypeError):
            return WmsDispatchResult(
                WmsDispatchCode.RECONCILING,
                normalized_response=dict(access.json_body) if isinstance(access.json_body, dict) else None,
            )
        normalized = response.model_dump(mode="json", exclude_unset=True)
        if response.code == "DECIDED":
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE, normalized_response=normalized, response_result=response.data.result
            )
        return WmsDispatchResult(
            WmsDispatchCode.RETRY if response.code == "UNAVAILABLE" else WmsDispatchCode.RECONCILING,
            normalized_response=normalized,
        )
