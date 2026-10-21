"""6C 纯规则只依赖 SDK；设备和 WAIT 续问不在这里推断。"""

from dataclasses import FrozenInstanceError

import pytest
from automatic_picking.handlers.material_flow import (
    MaterialDecidedFact,
    MaterialScannedFact,
    material_decided,
    material_scanned,
)
from wes_plugin_sdk import (
    PickingBinCell,
    PickingMaterialAccept,
    PickingMaterialIntent,
    PickingMaterialWait,
    PickingTargetRackSlot,
    Wait,
)


def scanned(**changes):
    return MaterialScannedFact(
        fact_id="evidence:11",
        evidence_id="11",
        fact_version="1.0",
        material_execution_id="EXEC-1",
        task_id="TASK-1",
        source_locator=PickingBinCell("RACK", "A", "BIN", "CELL-7"),
        barcode=changes.get("barcode", " 原始条码\n/abc "),
        scanned_at=123,
    )


def test_initial_decide_preserves_scan_and_uses_frozen_execution_identity():
    fact = scanned()
    (first,) = material_scanned(fact)
    assert type(first) is PickingMaterialIntent
    assert first.operation_id == "EXEC-1"
    assert first.material_execution_id == "EXEC-1" and first.fact_id == fact.fact_id
    assert (first.barcode, first.scanned_at, first.source_locator) == (fact.barcode, 123, fact.source_locator)
    assert first.plan_revision is None
    assert material_scanned(fact) == (first,)
    with pytest.raises(FrozenInstanceError):
        fact.barcode = "changed"


@pytest.mark.parametrize("barcode", ["", "x" * 1001])
def test_initial_decide_rejects_unconfirmed_barcode_boundary(barcode):
    with pytest.raises(ValueError, match="barcode"):
        material_scanned(scanned(barcode=barcode))


@pytest.mark.parametrize(
    "result,reason",
    [
        (PickingMaterialWait(1234), "MATERIAL_DECIDE_WAIT"),
        (PickingMaterialAccept(PickingTargetRackSlot("TARGET", "A", "S1", 1, 2)), "MATERIAL_ACCEPTED"),
    ],
)
def test_determinate_result_does_not_create_a_new_decide_or_physical_command(result, reason):
    fact = MaterialDecidedFact(
        fact_id="evidence:12",
        evidence_id="12",
        fact_version="1.0",
        material_execution_id="EXEC-1",
        pickup_id="PICKUP-1",
        work_id=1,
        result=result,
    )
    assert material_decided(fact) == (Wait("EXEC-1", fact.fact_id, reason),)
