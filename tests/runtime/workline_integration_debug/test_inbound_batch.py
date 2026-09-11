from types import SimpleNamespace

import pytest

from src.app.workline_integration_debug.service import IntegrationDebugConflict, IntegrationDebugService


@pytest.mark.parametrize("count", [1, 2, 4])
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
    with pytest.raises(IntegrationDebugConflict, match="1–4"):
        IntegrationDebugService._advance_inbound_batch(run, "READY", {"bins": bins})
    assert run.current_phase == "BIN_INBOUND_BATCH"
