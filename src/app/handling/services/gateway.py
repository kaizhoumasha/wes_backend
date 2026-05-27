"""Handling 外部系统协议网关。"""

from __future__ import annotations

from typing import Any

from src.app.handling.services.completion_policy import is_full_box_exchange_operation_type
from src.app.wms_integration.services.transport_contract import (
    WmsTransportContractService,
    wms_transport_contract_service,
)


class WmsRcsHandlingGateway:
    """将内部 Handling move 转换为 WMS/RCS 请求包络。"""

    def __init__(self, contract_service: WmsTransportContractService = wms_transport_contract_service) -> None:
        self._contract_service = contract_service

    def build_ctu_move_envelope(self, *, operation: Any, move: Any, sequence_no: int) -> dict[str, Any]:
        return self._contract_service.build_handling_ctu_move_envelope(
            operation=operation,
            move=move,
            sequence_no=sequence_no,
            is_full_box_exchange=is_full_box_exchange_operation_type(
                str(getattr(operation, "operation_type", "") or "")
            ),
        )


wms_rcs_handling_gateway = WmsRcsHandlingGateway()


__all__ = ["WmsRcsHandlingGateway", "wms_rcs_handling_gateway"]
