"""货架离场的单次派发，不拥有物理离场或业务重求值。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.outbound_picking.departure_wire import (
    RACK_DEPARTURE_OPERATION,
    parse_rack_departure_request,
    parse_rack_departure_response,
)
from src.app.wms_adapter.wire_common import DECISION_PATH
from src.app.wms_diagnostics.observation import capture
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient
    from src.app.wms_diagnostics.observation import WmsCallObservation


class RackDepartureAdapter:
    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
        observation: WmsCallObservation | None = None,
    ) -> WmsDispatchResult:
        try:
            request = parse_rack_departure_request(request_payload, observation=observation)
        except (ValueError, TypeError):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        if (
            operation != RACK_DEPARTURE_OPERATION
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            capture(observation, error_code="FROZEN_REQUEST_MISMATCH")
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        access = await receive_json(
            self._client, DECISION_PATH, request.model_dump(mode="json"), observation=observation
        )
        if isinstance(access, WmsDispatchResult):
            return access
        try:
            response = parse_rack_departure_response(
                access.status_code or 0, access.json_body, request=request, observation=observation
            )
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
