"""旧 handling transport producer 的 fail-closed 回归。"""

from __future__ import annotations

import pytest

from src.app.handling.services import HandlingOperationService, WmsRcsHandlingGateway
from src.app.wms_integration.services.transport_contract import WmsTransportMigrationRequiredError


@pytest.mark.asyncio
async def test_handling_operation_rejects_before_persisting_legacy_transport() -> None:
    with pytest.raises(RuntimeError, match="T5 dispatcher is not implemented"):
        await HandlingOperationService().request_bin_operation(
            None,
            operation_type="BIN_MOVE",
            operation_key="handling-removed-001",
            moves=[],
            trace_id="trace-handling-removed",
        )


def test_handling_gateway_cannot_build_removed_transport_envelope() -> None:
    with pytest.raises(WmsTransportMigrationRequiredError, match="T5 dispatcher is not implemented"):
        WmsRcsHandlingGateway().build_ctu_move_envelope(
            operation=object(),
            move=object(),
            sequence_no=1,
        )
