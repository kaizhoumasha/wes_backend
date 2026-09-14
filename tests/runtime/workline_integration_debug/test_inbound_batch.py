from types import SimpleNamespace

import pytest

from src.app.workline_integration_debug.contracts import (
    IntegrationDebugPhase,
    IntegrationTransportAction,
    IntegrationTransportActionKind,
)
from src.app.workline_integration_debug.service import (
    IntegrationDebugConflict,
    IntegrationDebugContractError,
    IntegrationDebugService,
)
from src.app.workline_integration_debug.transport import build_transport_request


@pytest.mark.parametrize("count", [1, 2, 4, 5])
def test_inbound_ready_preserves_all_members(count: int) -> None:
    run = SimpleNamespace(configuration_json={}, current_phase="BIN_INBOUND_BATCH", status="WAITING_EXTERNAL")
    bins = [
        {
            "bin_code": f"BIN-{index}",
            "source_locator": {
                "type": "RACK_BIN_SLOT",
                "rack_id": "510002",
                "rack_face": "270",
                "slot_id": f"SLOT-{index}",
            },
        }
        for index in range(count)
    ]
    IntegrationDebugService._advance_inbound_batch(run, "READY", {"bins": bins})
    assert run.configuration_json["inbound_bins"] == bins
    assert run.current_phase == "BIN_TRANSPORT"


@pytest.mark.parametrize("bins", [[], [{"bin_code": "BIN-1"}]])
def test_inbound_ready_rejects_incomplete_batches(bins: list[dict[str, str]]) -> None:
    run = SimpleNamespace(configuration_json={}, current_phase="BIN_INBOUND_BATCH", status="WAITING_EXTERNAL")
    with pytest.raises(IntegrationDebugConflict, match="完整且不重复"):
        IntegrationDebugService._advance_inbound_batch(run, "READY", {"bins": bins})
    assert run.current_phase == "BIN_INBOUND_BATCH"


@pytest.mark.parametrize("count", [1, 2, 4, 5])
def test_bin_transport_uses_next_four_from_complete_wms_face(count: int) -> None:
    inbound = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_BINS,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4480",
        rack_id="RACK-01",
        source={"kind": "RACK", "location_code": "RACK-01"},
        target={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
        rcs_template_id="CTU01",
    )
    configuration = {
        "inbound_bins": [
            {
                "bin_code": f"BIN-{index}",
                "source_locator": {
                    "type": "RACK_BIN_SLOT",
                    "rack_id": "RACK-01",
                    "rack_face": "90",
                    "slot_id": f"SLOT-{index}",
                },
            }
            for index in range(count)
        ]
    }
    moves = IntegrationDebugService._validate_batch_transport(
        IntegrationDebugPhase.BIN_TRANSPORT, configuration, inbound
    )
    request = build_transport_request(inbound, bin_moves=moves)
    assert [move.bin_code for move in request.moves] == [f"BIN-{index}" for index in range(min(count, 4))]
    assert [move.source.slot_id for move in request.moves] == [f"SLOT-{index}" for index in range(min(count, 4))]
    assert all(move.target.location_code == "CNV0301" for move in request.moves)
    if count > 4:
        configuration["inbound_transport_offset"] = 4
        next_moves = IntegrationDebugService._validate_batch_transport(
            IntegrationDebugPhase.BIN_TRANSPORT, configuration, inbound
        )
        assert [move.bin_code for move in next_moves] == ["BIN-4"]

    inbound.source["location_code"] = "RACK-OTHER"
    with pytest.raises(IntegrationDebugContractError, match="当前分段料箱"):
        IntegrationDebugService._validate_batch_transport(IntegrationDebugPhase.BIN_TRANSPORT, configuration, inbound)
