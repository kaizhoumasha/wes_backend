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


def arm_mapping(*, available=True):
    line = N(
        id=1,
        is_active=True,
        config={
            "device_bindings": {
                "SCAN1": "S1",
                "SCAN2": "S2",
                "SCAN3": "S3",
                "SCAN4": "S4",
                "SOURCE_ARM": "ARM01",
                "TARGET_ARM": "ARM02",
            }
        },
    )
    pickup = N(work_id=11, direct_pick_id=None, material_execution_id=22, command_code="SOURCE-1")
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
    )
    provider = N(read_in_session=AsyncMock(return_value=binding if available else None))
    db.scalar.side_effect = [pickup, command]
    return PickingArmCommandMapper(provider, worklines=worklines, clock=lambda: NOW), db, command


def configured_line():
    return N(
        id=1,
        is_active=True,
        is_deleted=False,
        plugin_key="automatic-picking",
        plugin_version="0.1.0",
        position_bindings={
            "WORK_BIN": {"location_type": "BIN", "location_id": "EXPLICIT-WORK"},
            "SCAN_PLATFORM": {"location_type": "SCAN_PLATFORM", "location_id": "EXPLICIT-PLATFORM"},
            "TRANSFER_RACK": {"location_type": "RACK_POSITION", "location_id": "EXPLICIT-TARGET"},
        },
    )


@pytest.mark.asyncio
async def test_binding_reader_uses_current_activated_positions_without_inferring_device_codes():
    from automatic_picking.application.picking_device_ports import PickingArmBindingReader

    line = configured_line()
    lines = N(get_for_authority_update=AsyncMock(return_value=line))
    db = object()
    actual = await PickingArmBindingReader(worklines=lines).read_in_session(db, workline_id=1)
    assert actual == PickingArmBinding("EXPLICIT-WORK", "EXPLICIT-PLATFORM", "EXPLICIT-TARGET")
    lines.get_for_authority_update.assert_awaited_once_with(db, 1, populate_existing=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slot,field",
    [("WORK_BIN", "work_location"), ("SCAN_PLATFORM", "scan_location"), ("TRANSFER_RACK", "target_location")],
)
async def test_missing_binding_remains_unknown_only_for_its_dependent_command(slot, field):
    from automatic_picking.application.picking_device_ports import PickingArmBindingReader

    line = configured_line()
    del line.position_bindings[slot]
    lines = N(get_for_authority_update=AsyncMock(return_value=line))
    actual = await PickingArmBindingReader(worklines=lines).read_in_session(object(), workline_id=1)
    assert getattr(actual, field) is None
    assert sum(getattr(actual, name) is None for name in ("work_location", "scan_location", "target_location")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["inactive", "deleted", "other_plugin", "other_version", "other_line"])
async def test_binding_reader_never_uses_another_or_inactive_runtime(change):
    from automatic_picking.application.picking_device_ports import PickingArmBindingReader

    line = configured_line()
    key, value = {
        "inactive": ("is_active", False),
        "deleted": ("is_deleted", True),
        "other_plugin": ("plugin_key", "manual-picking"),
        "other_version": ("plugin_version", "unavailable"),
        "other_line": ("id", 2),
    }[change]
    setattr(line, key, value)
    lines = N(get_for_authority_update=AsyncMock(return_value=line))
    assert await PickingArmBindingReader(worklines=lines).read_in_session(object(), workline_id=1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"location_type": "BIN", "location_id": ""},
        {"location_type": "BIN", "location_id": "  "},
        {"location_type": "RACK_POSITION", "location_id": "wrong-kind"},
        {"location_type": "BIN", "location_id": 1},
        {"location_type": "BIN", "location_id": "EXPLICIT-PLATFORM"},
    ],
)
async def test_invalid_or_colliding_frozen_position_is_rejected(value):
    from automatic_picking.application.picking_device_ports import PickingArmBindingReader

    line = configured_line()
    line.position_bindings["WORK_BIN"] = value
    lines = N(get_for_authority_update=AsyncMock(return_value=line))
    with pytest.raises(ValueError, match="position"):
        await PickingArmBindingReader(worklines=lines).read_in_session(object(), workline_id=1)


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
                "bin_type": "2",
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
                "rack_face": "A",
                "rack_layer": "1",
                "rack_column": "1",
                "reel_thickness": "0.1",
                "reel_diameter": "15",
            },
        }
    assert request.device_code == ("ARM01" if role == "source" else "ARM02")
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
async def test_partial_locations_block_only_the_command_that_needs_them():
    mapper, db, _ = arm_mapping()
    mapper.bindings.read_in_session.return_value = PickingArmBinding("EXPLICIT-WORK", "EXPLICIT-PLATFORM", None)
    work = WorkSourceFact(11, 1, 31, SOURCE, None, True, None, True, True, True)
    assert await mapper.source_request(db, work=work, pickup_id="P1") is not None
    assert (
        await mapper.put_request(db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW)
        is None
    )
    mapper.bindings.read_in_session.return_value = PickingArmBinding(None, "EXPLICIT-PLATFORM", "EXPLICIT-TARGET")
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


@pytest.mark.parametrize("field,value", [("work_location", ""), ("scan_location", ""), ("target_location", "")])
def test_binding_never_turns_unknown_or_invalid_values_into_defaults(field, value):
    values = {
        "work_location": "WORK",
        "scan_location": "SCAN",
        "target_location": "TARGET",
    }
    values[field] = value
    with pytest.raises(ValueError):
        PickingArmBinding(**values)


def scan_fixture(*, code="SOURCE-1", data=None):
    from src.app.device.contracts import EcsDeviceEvent
    from src.app.execution.models import InboundEvidence
    from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload

    event = EcsDeviceEvent(
        device_code="ARM01",
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
        device_code="ARM01",
        task_type="PICK_AND_PUT",
        workline_id=1,
        execution_ref_type="WORKLINE_BUSINESS",
        execution_ref_id="P1",
        material_execution_id=None,
        status="DISPATCHING",
        params={"target": {"location_type": "SCAN_PLATFORM", "location_id": "EXPLICIT-PLATFORM"}},
    )
    pickup = N(pickup_id="P1", work_id=11, direct_pick_id=None)
    work = N(id=11, workline_id=1)
    db = N(
        get=AsyncMock(side_effect=lambda model, key, **kw: evidence if model is InboundEvidence else work),
        scalar=AsyncMock(side_effect=[command, pickup]),
    )
    association = N(read_in_session=AsyncMock(return_value="SOURCE-1"))
    return db, evidence, command, association


@pytest.mark.asyncio
@pytest.mark.parametrize("bin_type", [2, 3, 6])
async def test_confirmed_bin_codes_do_not_need_a_mapping_table(bin_type):
    mapper, db, _ = arm_mapping()
    source = PickingCellSource("CELL-7", 7, bin_type, 3, 0.1, 15.0)
    work = WorkSourceFact(11, 1, 31, source, None, True, None, True, True, True)
    request = await mapper.source_request(db, work=work, pickup_id="P1")
    assert request is not None
    assert request.params["source"]["bin_type"] == str(bin_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("face", ["A", "B", "90", "270", " 90 "])
async def test_confirmed_target_face_is_preserved_without_side_conversion(face):
    mapper, db, _ = arm_mapping()
    target = PickingTargetRackSlot("TARGET", face, "S1", 1, 1)
    request = await mapper.put_request(
        db, workline_id=1, pickup_id="P1", material_execution_id=22, target=target, now=NOW
    )
    assert request is not None
    assert request.params["target"]["rack_face"] == face
    assert "rack_side" not in request.params["target"]


@pytest.mark.asyncio
async def test_scan_from_the_other_arm_cannot_bind_the_normal_source_command():
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, command, association = scan_fixture()
    command.device_code = "ARM02"
    with pytest.raises(ValueError, match="identity"):
        await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)


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
@pytest.mark.parametrize("bound", [True, False])
async def test_scan_without_device_adapter_only_uses_its_persisted_exact_association(bound):
    from automatic_picking.application.material_flow import MaterialScanAssociationPending
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, command, _association = scan_fixture(code="SOURCE-1" if bound else None)
    reader = PickingMaterialScanReader()
    if bound:
        assert (await reader.read_in_session(db, evidence_id=fact.id)).command_code == command.command_code
    else:
        with pytest.raises(MaterialScanAssociationPending):
            await reader.read_in_session(db, evidence_id=fact.id)
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


@pytest.mark.asyncio
async def test_explicit_direct_pick_scan_uses_original_arm02_owner_without_work():
    from automatic_picking.application.picking_device_ports import PickingMaterialScanReader

    db, fact, command, association = scan_fixture()
    command.device_code = fact.device_code = "ARM02"
    payload = {**fact.normalized_payload, "device_code": "ARM02"}
    fact.normalized_payload = payload
    from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload

    fact.payload_digest = normalize_payload(payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
    # 显式关联提供原取盘命令；不增加或解释任何未冻结设备字段。
    direct = N(pickup_id="P1", work_id=None, direct_pick_id=17)
    db.scalar = AsyncMock(side_effect=[command, direct])
    matched = await PickingMaterialScanReader(association).read_in_session(db, evidence_id=fact.id)
    assert matched.pickup_id == "P1" and matched.command_code == command.command_code
    assert matched.barcode == payload["data"]["barcode"]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["ready", "wrong_face", "moving", "no_arrival", "unbound"])
async def test_direct_source_uses_its_return_round_without_bin_scan(boundary, monkeypatch):
    from automatic_picking.application.picking_device_ports import PickingSourceReader
    from automatic_picking.application.pickup_model import AutomaticPickingDirectPick
    from automatic_picking.application.return_rack_repository import ReturnRackRepository
    from wes_plugin_sdk import PickingRackSlot

    from src.app.workline.models import WorkLine

    owner = AutomaticPickingDirectPick(id=17, workline_id=1, picking_task_id=2, member_id=3)
    member = N(picking_task_id=2, rack_id="RETURN", rack_face="F2", slot_id="S1", cancelled_evidence_id=None)
    line = N(id=1, is_active=True, position_bindings={"RETURN_RACK": {"location_id": "RETURN-POS"}})
    current = N(departure_confirmation_id=None, departure_transport_task_id=None, rotation_transport_task_id=None)
    position = N(
        arrival_face="OTHER" if boundary == "wrong_face" else "F2",
        position_json={"kind": "RACK_POSITION", "location_code": "RETURN-POS"},
    )
    if boundary == "moving":
        current.rotation_transport_task_id = "ROTATE"
    if boundary == "unbound":
        line.position_bindings = {}
    arrival = AsyncMock(return_value=None if boundary == "no_arrival" else (object(), object(), position))
    monkeypatch.setattr(ReturnRackRepository, "current_arrival", arrival)
    monkeypatch.setattr(ReturnRackRepository, "member_done", AsyncMock(return_value=False))
    monkeypatch.setattr(ReturnRackRepository, "member_admissible", AsyncMock(return_value=True))
    db = N(
        get=AsyncMock(side_effect=lambda model, key, **kw: line if model is WorkLine else member),
        scalars=AsyncMock(return_value=N(all=lambda: [current])),
    )
    actual = await PickingSourceReader().read_in_session(
        db, work=owner, source=PickingRackSlot("RETURN", "F2", "S1"), pickup=None
    )
    assert actual.source_ready is (boundary == "ready")
    assert actual.removed_evidence_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_owner", [False, True])
async def test_direct_original_removal_is_independent_of_new_source_admission(wrong_owner, monkeypatch):
    from automatic_picking.application.picking_device_ports import PickingSourceReader
    from automatic_picking.application.pickup_model import AutomaticPickingDirectPick
    from wes_plugin_sdk import PickingRackSlot

    from src.app.workline.models import WorkLine

    owner = AutomaticPickingDirectPick(id=17, workline_id=1, picking_task_id=2, member_id=3)
    member = N(picking_task_id=2, rack_id="RETURN", rack_face="F2", slot_id="S1", cancelled_evidence_id=21)
    pickup = N(work_id=None, direct_pick_id=18 if wrong_owner else 17, pickup_id="P1", command_code="SOURCE-1")
    command = N(
        workline_id=1, execution_ref_type="WORKLINE_BUSINESS", execution_ref_id="P1", material_execution_id=None
    )
    fact = N(id=99, published_at=NOW)
    finished = AsyncMock(return_value=(fact, object()))
    monkeypatch.setattr("automatic_picking.application.picking_device_ports._finished_arm", finished)
    db = N(
        get=AsyncMock(side_effect=lambda model, key, **kw: N(is_active=False) if model is WorkLine else member),
        scalar=AsyncMock(return_value=command),
    )
    # 取消/暂停阻止新动作，原匹配结果的恢复仍继续。
    if wrong_owner:
        with pytest.raises(ValueError, match="identity"):
            await PickingSourceReader().read_in_session(
                db, work=owner, source=PickingRackSlot("RETURN", "F2", "S1"), pickup=pickup
            )
        finished.assert_not_awaited()
    else:
        actual = await PickingSourceReader().read_in_session(
            db, work=owner, source=PickingRackSlot("RETURN", "F2", "S1"), pickup=pickup
        )
        assert not actual.source_ready and actual.removed_evidence_id == 99
