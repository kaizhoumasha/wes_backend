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
        "added_bin_source_racks": [{"rack_id": "BIN-RACK", "rack_face": "3"}],
        "added_direct_picks": [
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "RETURN", "rack_face": "C", "slot_id": "C-01"}}
        ],
    }
    assert parse_picking_task_plan_delta_event(payload).data.plan_revision == 2


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
        payload["data"]["added_bin_source_racks"] = [{"rack_id": "SOURCE", "rack_face": face}]
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
    slot = fields["added_direct_picks"]["items"]["properties"]["source_locator"]["properties"]
    for field in ("rack_id", "slot_id"):
        assert slot[field]["pattern"] == BUSINESS_IDENTIFIER_PATTERN
