from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.outbound_picking.openapi import PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA
from src.app.wms_adapter.outbound_picking.plan_delta_wire import (
    PickingTaskPlanDeltaEvent,
    PickingTaskPlanDeltaInvalidData,
    parse_picking_task_plan_delta_event,
    parse_picking_task_plan_delta_receipt,
)


def valid_event():
    return {
        "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
        "operation": "outbound.picking_task.plan_delta@v1",
        "timestamp": 1786060800000,
        "data": {"task_id": "PICK-1", "plan_revision": 1, "target_rack": {"rack_id": "RACK-1", "rack_face": "SIDE-3"}},
    }


@pytest.mark.parametrize("face", [" 面三 ", "x" * 10, "面" * 10])
def test_target_only_first_revision_and_opaque_face_roundtrip(face):
    payload = valid_event()
    payload["data"]["target_rack"]["rack_face"] = face
    parsed = parse_picking_task_plan_delta_event(payload)
    assert isinstance(parsed, PickingTaskPlanDeltaEvent)
    assert parsed.model_dump(mode="json", exclude_none=True) == payload


def test_later_revision_accepts_both_source_kinds():
    payload = valid_event()
    payload["data"] = {
        "task_id": "PICK-1",
        "plan_revision": 2,
        "added_bin_source_racks": [{"rack_id": "BIN-RACK", "rack_face": ["90", "270"]}],
        "added_direct_picks": [
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "RETURN", "rack_face": "C", "slot_id": "C-01"}}
        ],
    }
    assert parse_picking_task_plan_delta_event(payload).data.plan_revision == 2


def test_bin_source_rack_accepts_single_face_string() -> None:
    payload = valid_event()
    payload["data"] = {
        "task_id": "PICK-1",
        "plan_revision": 2,
        "added_bin_source_racks": [{"rack_id": "BIN-RACK", "rack_face": "270"}],
    }
    parsed = parse_picking_task_plan_delta_event(payload)
    assert parsed.data.added_bin_source_racks
    assert parsed.data.added_bin_source_racks[0].rack_face == ("270",)


@pytest.mark.parametrize("rack_face", [[""], ["x" * 11], [90], ["90", None], ""])
def test_bin_source_rack_rejects_invalid_face_array_members(rack_face):
    payload = valid_event()
    payload["data"]["added_bin_source_racks"] = [{"rack_id": "BIN-RACK", "rack_face": rack_face}]

    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"plan_revision": True},
        {"plan_revision": False},
        {"plan_revision": 0},
        {"plan_revision": 2**63},
        {"plan_revision": 1.0},
        {"plan_revision": "1"},
        {"target_rack": None},
        {"target_rack": {}},
        {"added_direct_picks": None},
        {"added_bin_source_racks": None},
        {"added_direct_picks": []},
        {"added_bin_source_racks": []},
        {"added_bin_source_racks": [{"rack_id": "BIN-RACK", "rack_face": []}]},
        {"plan_revision": 2},
        {"plan_revision": None},
        {"added_direct_picks": [{"source_locator": {"type": "BIN_CELL"}}]},
    ],
)
def test_invalid_data_preserves_original_envelope_for_evidence(patch):
    payload = valid_event()
    payload["data"].update(patch)
    original = deepcopy(payload)
    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)
    receipt = parse_picking_task_plan_delta_receipt(payload)
    assert isinstance(receipt, PickingTaskPlanDeltaInvalidData)
    assert receipt.raw_envelope == original


@pytest.mark.parametrize(
    "patch",
    [
        {"added_bin_source_racks": [{"rack_id": "BIN-RACK", "rack_face": []}]},
        {"added_bin_source_racks": [{"rack_id": "BIN-RACK", "rack_face": ["90", "90"]}]},
    ],
)
def test_bin_source_rack_rejects_empty_or_duplicate(patch: dict[str, object]) -> None:
    payload = valid_event()
    payload["data"]["plan_revision"] = 2
    payload["data"].update(patch)
    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)


@pytest.mark.parametrize("revision", [1, 2])
def test_revision_requires_its_applicable_change(revision):
    payload = valid_event()
    payload["data"] = {"task_id": "PICK-1", "plan_revision": revision}
    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rack_id", " "),
        ("rack_id", "x" * 101),
        ("rack_id", "a\x00b"),
        ("rack_face", ""),
        ("rack_face", "x" * 11),
        ("rack_face", "面" * 11),
        ("rack_face", "a\x00b"),
        ("rack_face", "\ud800"),
    ],
)
def test_locator_validation_matches_persistable_identifiers(field, value):
    payload = valid_event()
    payload["data"]["target_rack"][field] = value
    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)


def test_openapi_publishes_closed_revision_and_locator_constraints():
    schema = PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA
    assert schema["additionalProperties"] is True
    data = schema["properties"]["data"]
    assert data["additionalProperties"] is True
    assert data["properties"]["plan_revision"]["maximum"] == 2**63 - 1
    first, later = data["oneOf"]
    assert first == {"properties": {"plan_revision": {"const": 1}}, "required": ["target_rack"]}
    assert later["not"] == {"required": ["target_rack"]}
    assert later["anyOf"] == [{"required": ["added_bin_source_racks"]}, {"required": ["added_direct_picks"]}]
    for key in ("added_bin_source_racks", "added_direct_picks"):
        assert data["properties"][key]["minItems"] == 1
        assert data["properties"][key]["items"]["additionalProperties"] is True


@pytest.mark.parametrize("face", ["x" * 10, "面" * 10, "x" * 11, "面" * 11])
@pytest.mark.parametrize("source_kind", ["bin_rack", "direct_pick"])
def test_source_faces_share_the_ten_character_boundary(face, source_kind):
    payload = valid_event()
    payload["data"] = {"task_id": "PICK-1", "plan_revision": 2}
    if source_kind == "bin_rack":
        payload["data"]["added_bin_source_racks"] = [{"rack_id": "SOURCE", "rack_face": [face]}]
    else:
        payload["data"]["added_direct_picks"] = [
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "SOURCE", "rack_face": face, "slot_id": "1"}}
        ]
    if len(face) <= 10:
        assert parse_picking_task_plan_delta_event(payload).model_dump(mode="json", exclude_none=True) == payload
    else:
        with pytest.raises(ValidationError):
            parse_picking_task_plan_delta_event(payload)


@pytest.mark.parametrize("timestamp", [0, 2**63 - 1])
def test_timestamp_accepts_nonnegative_int64(timestamp):
    payload = valid_event()
    payload["timestamp"] = timestamp
    assert parse_picking_task_plan_delta_event(payload).timestamp == timestamp


@pytest.mark.parametrize("timestamp", [-1, 2**63, True, "0", 0.0])
def test_timestamp_rejects_invalid_int64(timestamp):
    payload = valid_event()
    payload["timestamp"] = timestamp
    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)


@pytest.mark.parametrize("field", ["rack_id", "slot_id"])
@pytest.mark.parametrize("identifier", ["退料架 1", "RACK 1", "_RACK", "RACK\n", "x" * 101])
def test_direct_pick_identifiers_follow_business_contract(field, identifier):
    payload = valid_event()
    locator = {"type": "RACK_SLOT", "rack_id": "RETURN", "rack_face": " 面三 ", "slot_id": "C-01"}
    locator[field] = identifier
    payload["data"] = {
        "task_id": "PICK-1",
        "plan_revision": 2,
        "added_direct_picks": [{"source_locator": locator}],
    }
    with pytest.raises(ValidationError):
        parse_picking_task_plan_delta_event(payload)


def test_openapi_uses_business_identifiers_and_nonnegative_timestamp():
    from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN

    schema = PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA
    assert schema["properties"]["timestamp"]["minimum"] == 0
    fields = schema["properties"]["data"]["properties"]
    for rack in (fields["target_rack"], fields["added_bin_source_racks"]["items"]):
        assert rack["properties"]["rack_id"]["pattern"] == BUSINESS_IDENTIFIER_PATTERN
    source_faces = fields["added_bin_source_racks"]["items"]["properties"]["rack_face"]
    assert "oneOf" in source_faces
    array_branch = next(branch for branch in source_faces["oneOf"] if branch.get("type") == "array")
    assert array_branch["minItems"] == 1
    assert array_branch["items"]["maxLength"] == 10
    slot = fields["added_direct_picks"]["items"]["properties"]["source_locator"]["properties"]
    for field in ("rack_id", "slot_id"):
        assert slot[field]["pattern"] == BUSINESS_IDENTIFIER_PATTERN


def test_multiple_direct_picks_in_same_array_are_accepted_per_contract():
    """合同 §1.1：多个退料货架放在同一 added_direct_picks 数组中，不重复字段。"""
    payload = valid_event()
    payload["data"] = {
        "task_id": "PICK-1",
        "plan_revision": 1,
        "target_rack": {"rack_id": "TRANSFER-1", "rack_face": "90"},
        "added_direct_picks": [
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "RET-1", "rack_face": "A", "slot_id": "A-03"}},
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "RET-2", "rack_face": "A", "slot_id": "A-01"}},
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "RET-1", "rack_face": "B", "slot_id": "B-05"}},
        ],
    }

    parsed = parse_picking_task_plan_delta_event(payload)

    assert isinstance(parsed, PickingTaskPlanDeltaEvent)
    assert parsed.data.added_direct_picks is not None
    rack_face_by_rack = {
        pick.source_locator.rack_id: pick.source_locator.rack_face for pick in parsed.data.added_direct_picks
    }
    assert rack_face_by_rack == {"RET-1": "B", "RET-2": "A"}


def test_duplicate_added_direct_picks_key_keeps_last_value_in_json_payload():
    """重复字段名按 JSON 协议取最后一个值；合同要求 WMS 不原地补字段重发。"""
    payload = valid_event()
    payload["data"] = {
        "task_id": "PICK-1",
        "plan_revision": 1,
        "added_direct_picks": [
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "RET-1", "rack_face": "A", "slot_id": "A-03"}}
        ],
    }
    text = (
        '{"operation":"outbound.picking_task.plan_delta@v1",'
        '"operation_id":"019f33f0-58d7-7b4d-a23a-1b90aa5d4473",'
        '"timestamp":1786060800000,'
        '"data":{"task_id":"PICK-1","plan_revision":1,'
        '"target_rack":{"rack_id":"TRANSFER-1","rack_face":"90"},'
        '"added_direct_picks":[{"source_locator":{"type":"RACK_SLOT","rack_id":"RET-1","rack_face":"A","slot_id":"A-03"}}],'
        '"added_direct_picks":[{"source_locator":{"type":"RACK_SLOT","rack_id":"RET-2","rack_face":"B","slot_id":"B-09"}}]}}'
    )

    parsed = PickingTaskPlanDeltaEvent.model_validate_json(text)

    assert parsed.data.added_direct_picks is not None
    assert [pick.source_locator.rack_id for pick in parsed.data.added_direct_picks] == ["RET-2"]
