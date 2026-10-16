"""来源成员版本由纯 typed intent 保留，不依赖宿主 wire。"""

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations


def intent_values(source):
    return {
        "operation_id": "019f3405-2200-7b01-8b01-000000000001",
        "task_id": "TASK-1",
        "source_locator": source,
    }


FACTORIES = (
    (wms_operations.outbound_material_decide, {"barcode": "原始,条码", "scanned_at": 123}),
    (wms_operations.outbound_source_empty_decide, {"observed_at": 123}),
    (
        wms_operations.outbound_material_movement_report,
        {"pkg_id": "P", "to_locator": sdk.PickingRackSlot("TARGET", "B", "S-2"), "occurred_at": 123},
    ),
)


@pytest.mark.parametrize("factory,extra", FACTORIES)
@pytest.mark.parametrize("revision", [None, True, "1", 1.0, 0, -1, 2**63])
def test_rack_slot_intent_rejects_invalid_revision(factory, extra, revision):
    with pytest.raises(ValueError):
        factory(**intent_values(sdk.PickingRackSlot("SOURCE", "A", "S-1")), **extra, plan_revision=revision)


@pytest.mark.parametrize("factory,extra", FACTORIES)
def test_revision_is_bound_to_rack_member_not_bin(factory, extra):
    source = sdk.PickingRackSlot("SOURCE", "A", "S-1")
    intent = factory(**intent_values(source), **extra, plan_revision=2**63 - 1)
    assert intent.source_locator is source  # nosec B101 - pytest assertion
    assert intent.plan_revision == 2**63 - 1  # nosec B101 - pytest assertion
    bin_source = sdk.PickingBinCell("SOURCE", "A", "BIN-1", "CELL-1")
    assert factory(**intent_values(bin_source), **extra).plan_revision is None  # nosec B101 - pytest assertion
    with pytest.raises(ValueError):
        factory(**intent_values(bin_source), **extra, plan_revision=1)
