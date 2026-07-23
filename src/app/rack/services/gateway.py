"""WMS/RCS 货架操作 Gateway。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.wms_integration.services.transport_contract import (
    DEFAULT_RACK_OPERATION_ENDPOINT,
    WmsRackTaskRequest,
    WmsTransportContractService,
    freeze_legacy_transport_binding,
    wms_transport_contract_service,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.sys.external_http_binding import FrozenExternalHttpBinding
    from src.app.sys.models import DispatchEnvelope


class WmsRcsRackGateway:
    """构造货架操作下发包络。"""

    def __init__(self, contract_service: WmsTransportContractService = wms_transport_contract_service) -> None:
        self._contract_service = contract_service

    def build_task_request(
        self,
        *,
        operation_key: str,
        operation_type: str,
        sequence_no: int,
        task_type: str,
        trace_id: str,
        workline_code: str | None,
        rack_code: str | None,
        rack_kind: str | None,
        source_position_code: str | None,
        target_position_code: str | None,
        target_position_role: str | None,
        actions_json: Mapping[str, Any] | None = None,
        request_json: Mapping[str, Any] | None = None,
        target_code: str | None = None,
        dispatch_key: str | None = None,
    ) -> WmsRackTaskRequest:
        """构造不含 endpoint/credential 解析的 canonical rack request。"""

        return self._contract_service.build_rack_task_request(
            operation_key=operation_key,
            operation_type=operation_type,
            sequence_no=sequence_no,
            task_type=task_type,
            trace_id=trace_id,
            workline_code=workline_code,
            rack_code=rack_code,
            rack_kind=rack_kind,
            source_position_code=source_position_code,
            target_position_code=target_position_code,
            target_position_role=target_position_role,
            actions_json=actions_json,
            request_json=request_json,
            target_code=target_code,
            dispatch_key=dispatch_key,
        )

    def build_task_envelope(
        self,
        *,
        operation_key: str,
        operation_type: str,
        sequence_no: int,
        task_type: str,
        trace_id: str,
        workline_id: int | None,
        workline_code: str | None,
        material_session_id: int | None,
        rack_code: str | None,
        rack_kind: str | None,
        source_position_code: str | None,
        target_position_code: str | None,
        target_position_role: str | None,
        actions_json: Mapping[str, Any] | None = None,
        request_json: Mapping[str, Any] | None = None,
        target_code: str | None = None,
        dispatch_key: str | None = None,
    ) -> DispatchEnvelope:
        return self._contract_service.build_rack_task_envelope(
            operation_key=operation_key,
            operation_type=operation_type,
            sequence_no=sequence_no,
            task_type=task_type,
            trace_id=trace_id,
            workline_id=workline_id,
            workline_code=workline_code,
            material_session_id=material_session_id,
            rack_code=rack_code,
            rack_kind=rack_kind,
            source_position_code=source_position_code,
            target_position_code=target_position_code,
            target_position_role=target_position_role,
            actions_json=actions_json,
            request_json=request_json,
            target_code=target_code,
            dispatch_key=dispatch_key,
        )


wms_rcs_rack_gateway = WmsRcsRackGateway()


def freeze_rack_task_binding(target_code: str) -> FrozenExternalHttpBinding:
    """通过 Rack gateway 边界冻结 legacy rack target binding。"""

    return freeze_legacy_transport_binding(
        operation_identity="wms.transport.rack@v1",
        target_code=target_code,
    )


__all__ = [
    "DEFAULT_RACK_OPERATION_ENDPOINT",
    "WmsRcsRackGateway",
    "freeze_rack_task_binding",
    "wms_rcs_rack_gateway",
]
