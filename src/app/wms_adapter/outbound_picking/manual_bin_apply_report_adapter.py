"""人工工作位完成决定应用结果的单次派发。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.outbound_picking.manual_bin_apply_report_wire import (
    MANUAL_BIN_APPLY_REPORT_OPERATION,
    parse_manual_bin_apply_report_request,
    parse_manual_bin_apply_report_response,
)
from src.app.wms_adapter.wire_common import FACT_PATH
from src.app.wms_diagnostics.observation import capture
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient
    from src.app.wms_diagnostics.observation import WmsCallObservation


class ManualBinApplyReportAdapter:
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
            request = parse_manual_bin_apply_report_request(request_payload, observation=observation)
        except (ValueError, TypeError):
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        if (
            operation != MANUAL_BIN_APPLY_REPORT_OPERATION
            or request.operation_id != operation_id
            or canonical_json_digest(request_payload) != request_digest
        ):
            capture(observation, error_code="FROZEN_REQUEST_MISMATCH")
            return WmsDispatchResult(WmsDispatchCode.RECONCILING)
        access = await receive_json(
            self._client,
            FACT_PATH,
            request.model_dump(mode="json"),
            observation=observation,
        )
        if isinstance(access, WmsDispatchResult):
            return access
        try:
            response = parse_manual_bin_apply_report_response(
                access.status_code or 0,
                access.json_body,
                request=request,
                observation=observation,
            )
        except (ValueError, TypeError):
            return WmsDispatchResult(
                WmsDispatchCode.RECONCILING,
                normalized_response=dict(access.json_body) if isinstance(access.json_body, dict) else None,
            )
        normalized = response.model_dump(mode="json", exclude_unset=True)
        if response.code in {"RECORDED", "DUPLICATE"}:
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response.code,
            )
        return WmsDispatchResult(
            WmsDispatchCode.RETRY if response.code == "UNAVAILABLE" else WmsDispatchCode.RECONCILING,
            normalized_response=normalized,
        )


__all__ = ["ManualBinApplyReportAdapter"]
