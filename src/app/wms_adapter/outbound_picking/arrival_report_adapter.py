"""`outbound.return_rack.arrival_report@v1` 可靠派发适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.outbound_picking.arrival_report_wire import (
    RETURN_RACK_ARRIVAL_REPORT_OPERATION,
    parse_return_rack_arrival_report_request,
    parse_return_rack_arrival_report_response,
)
from src.app.wms_adapter.wire_common import FACT_PATH
from src.app.wms_diagnostics.observation import capture, observed_contract_error
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient
    from src.app.wms_diagnostics.observation import WmsCallObservation


class ReturnRackArrivalReportAdapter:
    """校验冻结请求，通过共享 WmsClient 单次发送并解释 arrival_report 响应。"""

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
            request = parse_return_rack_arrival_report_request(request_payload, observation=observation)
        except (ValidationError, ValueError, TypeError):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        if (
            operation != RETURN_RACK_ARRIVAL_REPORT_OPERATION
            or request.operation != operation
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            capture(observation, error_code="FROZEN_REQUEST_MISMATCH")
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)

        access = await receive_json(self._client, FACT_PATH, request.model_dump(mode="json"), observation=observation)
        if isinstance(access, WmsDispatchResult):
            return access
        received_json = dict(access.json_body) if isinstance(access.json_body, dict) else None
        try:
            response = parse_return_rack_arrival_report_response(
                access.status_code or 0, access.json_body, observation=observation
            )
        except (ValidationError, ValueError, TypeError):
            return WmsDispatchResult(
                WmsDispatchCode.RECONCILING,
                normalized_response=received_json,
            )
        normalized = response.model_dump(mode="json", exclude_unset=True)
        if response.operation_id != operation_id:
            _ = observed_contract_error(
                observation, "响应 operation_id 必须匹配请求", path=("operation_id",), expected_value=operation_id
            )
            return WmsDispatchResult(
                WmsDispatchCode.RECONCILING,
                normalized_response=normalized,
            )
        if response.code in {"RECORDED", "DUPLICATE"}:
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response.code,
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
    "ReturnRackArrivalReportAdapter",
]
