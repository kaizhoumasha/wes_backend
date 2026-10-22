"""已确认机械臂包络映射；完整参数仅为显式测试输入，不能当设备合同默认值。"""

from datetime import datetime, timedelta
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.picking_device_ports import PickingArmBinding, PickingArmCommandMapper
from automatic_picking.handlers.source_authorization import WorkSourceFact
from wes_plugin_sdk import PickingCellSource, PickingTargetRackSlot

NOW = datetime(2026, 10, 22)
SOURCE = PickingCellSource("CELL-7", 7, 2, 1, 20.0, 15.0)
TARGET = PickingTargetRackSlot("TARGET", "A", "S1", 1, 1)


def arm_mapping(*, available=True, bin_types=None, rack_sides=None):
    line = N(
        id=1,
        is_active=True,
        config={
            "device_bindings": {
                "SCAN1": "S1",
                "SCAN2": "S2",
                "SCAN3": "S3",
                "SCAN4": "S4",
                "SOURCE_ARM": "ARM-X",
                "TARGET_ARM": "ARM-Y",
            }
        },
    )
    pickup = N(work_id=11, material_execution_id=22, command_code="SOURCE-1")
    command = N(
        workline_id=1,
        command_code="SOURCE-1",
        task_type="PICK_AND_PUT",
        execution_ref_type="WORKLINE_BUSINESS",
        execution_ref_id="P1",
        material_execution_id=None,
        params={
            "source": {
                "location_id": "EXPLICIT-WORK",
                "location_type": "BIN",
                "reel_thickness": "0.1",
                "reel_diameter": "15",
            },
            "target": {"location_id": "EXPLICIT-PLATFORM", "location_type": "SCAN_PLATFORM"},
        },
    )
    db = N(scalar=AsyncMock(return_value=command), get=AsyncMock(return_value=N(workline_id=1)))
    binding = N(contract_key="third_party_integration", contract_version="1.1", command_timeout_ms=1234)
    worklines = N(
        get_for_authority_update=AsyncMock(return_value=line),
        get_binding_for_command_creation=AsyncMock(return_value=binding),
    )
    binding = PickingArmBinding(
        "EXPLICIT-WORK",
        "EXPLICIT-PLATFORM",
        "EXPLICIT-TARGET",
        {2: "TEST-BIN-TYPE"} if bin_types is None else bin_types,
        {"A": "B"} if rack_sides is None else rack_sides,
    )
    provider = N(read_in_session=AsyncMock(return_value=binding if available else None))
    db.scalar.side_effect = [pickup, command]
    return PickingArmCommandMapper(provider, worklines=worklines, clock=lambda: NOW), db, command


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["source", "put"])
async def test_mapping_uses_current_role_binding_identity_and_configured_deadline(role):
    mapper, db, _ = arm_mapping()
    if role == "source":
        source = PickingCellSource("CELL-7", 7, 2, 3, 0.1, 15.0)
        work = WorkSourceFact(11, 1, 31, source, None, True, None, True, True, True)
        request = await mapper.source_request(db, work=work, pickup_id="P1")
        assert request.execution_ref_id == "P1" and request.material_execution_id is None
        assert request.params == {
            "source": {
                "location_id": "EXPLICIT-WORK",
                "location_type": "BIN",
                "bin_type": "TEST-BIN-TYPE",
                "bin_cell_location": "7",
                "reel_layer": "3",
                "reel_thickness": "0.1",
                "reel_totalthickness": "0.3",
                "reel_diameter": "15",
            },
            "target": {"location_id": "EXPLICIT-PLATFORM", "location_type": "SCAN_PLATFORM"},
        }
    else:
        request = await mapper.put_request(
            db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW
        )
        assert request.execution_ref_id == "put:P1" and request.material_execution_id == 22
        assert request.params == {
            "source": {"location_id": "EXPLICIT-PLATFORM", "location_type": "SCAN_PLATFORM"},
            "target": {
                "location_id": "EXPLICIT-TARGET",
                "location_type": "FIVE_LAYER_RACK",
                "rack_id": "TARGET",
                "rack_side": "B",
                "rack_layer": "1",
                "rack_column": "1",
                "reel_thickness": "0.1",
                "reel_diameter": "15",
            },
        }
    assert request.device_code == ("ARM-X" if role == "source" else "ARM-Y")
    assert request.task_type == "PICK_AND_PUT"
    assert request.contract_key == "third_party_integration" and request.contract_version == "1.1"
    assert request.deadline_at == NOW + timedelta(milliseconds=1234)
    assert request.execution_ref_type == "WORKLINE_BUSINESS"


@pytest.mark.asyncio
async def test_missing_complete_parameters_never_manufactures_command_defaults():
    mapper, db, _ = arm_mapping(available=False)
    work = WorkSourceFact(11, 1, 31, SOURCE, None, True, None, True, True, True)
    assert await mapper.source_request(db, work=work, pickup_id="P1") is None
    assert (
        await mapper.put_request(db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW)
        is None
    )


@pytest.mark.asyncio
async def test_missing_bin_type_or_face_mapping_blocks_only_its_command():
    mapper, db, _ = arm_mapping(bin_types={}, rack_sides={})
    work = WorkSourceFact(11, 1, 31, SOURCE, None, True, None, True, True, True)
    assert await mapper.source_request(db, work=work, pickup_id="P1") is None
    assert (
        await mapper.put_request(db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW)
        is None
    )
    mapper, db, _ = arm_mapping(rack_sides={})
    assert await mapper.source_request(db, work=work, pickup_id="P1") is not None


@pytest.mark.asyncio
async def test_partial_locations_block_only_the_command_that_needs_them():
    mapper, db, _ = arm_mapping()
    mapper.bindings.read_in_session.return_value = PickingArmBinding(
        "EXPLICIT-WORK", "EXPLICIT-PLATFORM", None, {2: "TEST-TYPE"}, {}
    )
    work = WorkSourceFact(11, 1, 31, SOURCE, None, True, None, True, True, True)
    assert await mapper.source_request(db, work=work, pickup_id="P1") is not None
    assert (
        await mapper.put_request(db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW)
        is None
    )
    mapper.bindings.read_in_session.return_value = PickingArmBinding(
        None, "EXPLICIT-PLATFORM", "EXPLICIT-TARGET", {}, {"A": "A"}
    )
    assert await mapper.source_request(db, work=work, pickup_id="P1") is None
    assert (
        await mapper.put_request(db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW)
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["platform", "line", "pickup", "dimensions"])
async def test_put_rejects_wrong_frozen_source_identity_or_parameters(change):
    mapper, db, command = arm_mapping()
    if change == "platform":
        command.params["target"]["location_id"] = "OTHER"
    elif change == "line":
        command.workline_id = 2
    elif change == "pickup":
        command.execution_ref_id = "OTHER"
    else:
        command.params["source"].pop("reel_thickness")
    with pytest.raises(ValueError):
        await mapper.put_request(db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW)


@pytest.mark.asyncio
async def test_target_layer_outside_hardware_range_never_creates_put():
    mapper, db, _ = arm_mapping()
    with pytest.raises(ValueError):
        await mapper.put_request(
            db,
            workline_id=1,
            pickup_id="P1",
            material_execution_id=22,
            target=PickingTargetRackSlot("TARGET", "A", "S1", 6, 1),
            now=NOW,
        )


@pytest.mark.parametrize("field,value", [("work_location", ""), ("bin_types", {2: ""}), ("rack_sides", {"A": "90"})])
def test_binding_never_turns_unknown_or_invalid_values_into_defaults(field, value):
    values = {
        "work_location": "WORK",
        "scan_location": "SCAN",
        "target_location": "TARGET",
        "bin_types": {},
        "rack_sides": {},
    }
    values[field] = value
    with pytest.raises(ValueError):
        PickingArmBinding(**values)


def scan_fixture(*, code="SOURCE-1", data=None):
    from src.app.device.contracts import EcsDeviceEvent
    from src.app.execution.models import InboundEvidence
    from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload

    event = EcsDeviceEvent(
        device_code="EXPLICIT-SCANNER",
        contract_key="third_party_integration",
        contract_version="1.1",
        event_type="SCAN_COMPLETED",
        timestamp=457,
        source_event_id="scan:original",
        is_debug=False,
        data=data if data is not None else {"location": "EXPLICIT-PLATFORM", "barcode": " 原样,条码\n "},
    )
    payload, digest = normalize_payload(
        event.model_dump(mode="json", exclude_unset=True), digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE
    )
    evidence = N(
        id=21,
        kind="DEVICE_EVENT",
        apply_status="APPLIED",
        normalized_payload=payload,
        payload_digest=digest,
        device_code=event.device_code,
        contract_key=event.contract_key,
        contract_version=event.contract_version,
        source_identity=event.source_event_id,
        workline_id=1,
        command_code=code,
    )
    command = N(
        command_code="SOURCE-1",
        task_type="PICK_AND_PUT",
        workline_id=1,
        execution_ref_type="WORKLINE_BUSINESS",
        execution_ref_id="P1",
        material_execution_id=None,
        status="DISPATCHING",
        params={"target": {"location_type": "SCAN_PLATFORM", "location_id": "EXPLICIT-PLATFORM"}},
    )
    pickup = N(pickup_id="P1", work_id=11)
    work = N(id=11, workline_id=1)
    db = N(
        get=AsyncMock(side_effect=lambda model, key, **kw: evidence if model is InboundEvidence else work),
        scalar=AsyncMock(side_effect=[command, pickup]),
    )
    association = N(read_in_session=AsyncMock(return_value="SOURCE-1"))
    return db, evidence, command, association


@pytest.mark.asyncio
async def test_real_scan_reader_preserves_raw_text_and_frozen_command_platform():
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, _command, association = scan_fixture()
    matched = await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)
    assert (matched.pickup_id, matched.command_code, matched.workline_id) == ("P1", "SOURCE-1", 1)
    assert matched.barcode == " 原样,条码\n " and matched.scanned_at == 457
    association.read_in_session.assert_not_called()


@pytest.mark.asyncio
async def test_known_scan_without_authoritative_association_stays_pending():
    from automatic_picking.application.material_flow import MaterialScanAssociationPending
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, _command, association = scan_fixture(code=None)
    association.read_in_session.return_value = None
    with pytest.raises(MaterialScanAssociationPending):
        await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)
    db.scalar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"location": "OTHER"}, {"barcode": ""}, {"barcode": 123}, {"barcode": "x" * 1001}])
async def test_scan_reader_rejects_wrong_platform_or_invalid_raw_barcode(changes):
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, _command, association = scan_fixture(data={"location": "EXPLICIT-PLATFORM", "barcode": "raw", **changes})
    with pytest.raises(ValueError):
        await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)


@pytest.mark.asyncio
async def test_bin_scan_does_not_become_a_material_scan():
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, _command, association = scan_fixture(data={"bin_code": "A123456789-C"})
    assert await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id) is None
    db.scalar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [("workline_id", 2), ("execution_ref_id", "OTHER"), ("material_execution_id", 9), ("task_type", "OTHER")],
)
async def test_scan_reader_rejects_a_command_outside_its_original_source_identity(field, value):
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, command, association = scan_fixture()
    setattr(command, field, value)
    with pytest.raises(ValueError, match="identity"):
        await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)


@pytest.mark.asyncio
async def test_scan_reader_uses_explicit_association_but_never_assumes_pending_command_was_sent():
    from automatic_picking.application.material_flow import MaterialScanAssociationPending
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, command, association = scan_fixture(code=None)
    command.status = "PENDING"
    with pytest.raises(MaterialScanAssociationPending):
        await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)
    association.read_in_session.assert_awaited_once_with(db, evidence_id=fact.id)
