"""旧 WMS transport producer 的 fail-closed 回归。"""

from __future__ import annotations

import pytest

from src.app.wms_integration.services.transport_contract import (
    WmsTransportContractService,
    WmsTransportMigrationRequiredError,
    freeze_legacy_transport_binding,
)


@pytest.mark.parametrize(
    "builder_name",
    (
        "build_single_layer_rack_operation_request",
        "build_rack_task_request",
        "build_rack_task_envelope",
        "build_handling_ctu_move_envelope",
    ),
)
def test_removed_transport_builders_fail_closed(builder_name: str) -> None:
    builder = getattr(WmsTransportContractService(), builder_name)

    with pytest.raises(WmsTransportMigrationRequiredError, match="T5 dispatcher is not implemented"):
        builder()


def test_removed_transport_binding_cannot_be_frozen() -> None:
    with pytest.raises(WmsTransportMigrationRequiredError, match="T5 dispatcher is not implemented"):
        freeze_legacy_transport_binding(target_code="WMS_RCS_RACK_OPERATION")
