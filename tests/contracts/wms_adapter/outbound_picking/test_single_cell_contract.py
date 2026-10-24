"""获批单 Cell、原始条码与目标坐标在 wire/SDK 转换中完整保留。"""

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.outbound_picking.material_decide_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.material_decide_wire import parse_material_decide_response
from src.app.wms_adapter.outbound_picking.work_plan_typed import decode_outcome as decode_work_plan

IDENTITY = "019f3405-2200-7b01-8b01-000000000001"
CELL = {
    "cell_id": "BIN-0013",
    "cell_index": 3,
    "bin_type": 3,
    "reel_layer": 2,
    "reel_thickness": 0.5,
    "reel_diameter": 15.0,
}
TARGET = {
    "type": "RACK_SLOT",
    "rack_id": "TARGET-1",
    "rack_face": "A",
    "slot_id": "S-1",
    "rack_layer": 1,
    "rack_column": 2,
}


def response(data):
    return {"operation_id": IDENTITY, "code": "DECIDED", "timestamp": 0, "data": data}


def test_single_cell_and_next_source_share_typed_parameters_without_losing_coordinates():
    ready = decode_work_plan(response({"result": "READY", **CELL})).result
    assert ready.cell_id == "BIN-0013"
    assert ready.cell_index == 3
    assert ready.bin_type == 3
    assert ready.reel_layer == 2
    assert ready.reel_thickness == 0.5
    assert ready.reel_diameter == 15.0
    outcome = decode_outcome(
        response({"result": "ACCEPT", "target_locator": TARGET, "next_source_action": CELL})
    ).result
    assert outcome.target_locator == sdk.PickingTargetRackSlot("TARGET-1", "A", "S-1", 1, 2)
    assert outcome.next_source_action == sdk.PickingCellSource(**CELL)
    assert decode_outcome(response({"result": "ACCEPT", "target_locator": TARGET})).result.next_source_action is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("cell_index", 0),
        ("cell_index", 8),
        ("cell_index", True),
        ("cell_index", "3"),
        ("bin_type", 1),
        ("bin_type", 4),
        ("bin_type", True),
        ("bin_type", "3"),
        ("reel_layer", True),
        ("reel_layer", "2"),
        ("reel_layer", 1.5),
        ("reel_thickness", "0.5"),
        ("reel_thickness", True),
        ("reel_thickness", float("inf")),
        ("reel_diameter", "15"),
        ("reel_diameter", float("nan")),
    ],
)
def test_cell_parameters_reject_wrong_types_and_confirmed_enum_violations(field, value):
    cell = CELL | {field: value}
    with pytest.raises(ValueError):
        decode_work_plan(response({"result": "READY", **cell}))
    with pytest.raises(ValueError):
        decode_outcome(response({"result": "ACCEPT", "target_locator": TARGET, "next_source_action": cell}))


@pytest.mark.parametrize("field", ["rack_layer", "rack_column"])
@pytest.mark.parametrize("value", [True, "1", 1.5, None])
def test_target_coordinates_are_required_strict_integers(field, value):
    with pytest.raises(ValueError):
        decode_outcome(response({"result": "ACCEPT", "target_locator": TARGET | {field: value}}))


def test_source_object_must_be_omitted_instead_of_null_and_direct_pick_cannot_continue():
    with pytest.raises(ValueError):
        decode_outcome(response({"result": "ACCEPT", "target_locator": TARGET, "next_source_action": None}))
    intent = wms_operations.outbound_material_decide(
        operation_id=IDENTITY,
        task_id="TASK-1",
        plan_revision=2,
        source_locator=sdk.PickingRackSlot("SOURCE-1", "A", "S-1"),
        barcode="原始,条码",
        scanned_at=123,
    )
    from src.app.wms_adapter.outbound_picking.material_decide_wire import parse_material_decide_request

    request = parse_material_decide_request(encode_request(intent, timestamp=0))
    with pytest.raises(ValueError):
        parse_material_decide_response(
            200, response({"result": "ACCEPT", "target_locator": TARGET, "next_source_action": CELL}), request=request
        )


def test_barcode_is_preserved_at_1000_characters_without_parsing():
    barcode = " 盘,;" * 250
    intent = wms_operations.outbound_material_decide(
        operation_id=IDENTITY,
        task_id="TASK-1",
        source_locator=sdk.PickingBinCell("SOURCE-1", "A", "BIN-1", "CELL-1"),
        barcode=barcode,
        scanned_at=123,
    )
    assert encode_request(intent, timestamp=0)["data"]["barcode"] == barcode
    with pytest.raises(ValueError):
        wms_operations.outbound_material_decide(
            operation_id=IDENTITY,
            task_id="TASK-1",
            source_locator=intent.source_locator,
            barcode="盘" * 1001,
            scanned_at=123,
        )
