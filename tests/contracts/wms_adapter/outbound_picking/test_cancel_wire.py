from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.outbound_picking.cancel_wire import (
    PickingTaskCancelMembersData,
    PickingTaskCancelTaskData,
    parse_picking_task_cancel_event,
)


def _event(data: dict[str, object]) -> dict[str, object]:
    return {
        "operation_id": "019f3410-0000-7000-8000-000000000001",
        "operation": "outbound.picking_task.cancel@v1",
        "timestamp": 1786065200000,
        "data": data,
    }


def test_task_cancel_is_the_minimal_closed_branch() -> None:
    parsed = parse_picking_task_cancel_event(_event({"task_id": "PICK-1", "cancel_scope": "TASK"}))
    assert type(parsed.data) is PickingTaskCancelTaskData


def test_plan_member_cancel_preserves_ordered_typed_selectors() -> None:
    parsed = parse_picking_task_cancel_event(
        _event(
            {
                "task_id": "PICK-1",
                "cancel_scope": "PLAN_MEMBERS",
                "bin_source_racks": [{"plan_revision": 1, "rack_id": "R1", "rack_face": ["90", "270"]}],
                "direct_pick_sources": [
                    {"plan_revision": 1, "rack_id": "D1", "rack_face": "A", "slot_ids": ["A-1", "A-2"]}
                ],
            }
        )
    )
    assert type(parsed.data) is PickingTaskCancelMembersData
    assert parsed.data.bin_source_racks[0].rack_face == ("90", "270")


def test_plan_member_cancel_accepts_single_face_string() -> None:
    parsed = parse_picking_task_cancel_event(
        _event(
            {
                "task_id": "PICK-1",
                "cancel_scope": "PLAN_MEMBERS",
                "bin_source_racks": [{"plan_revision": 1, "rack_id": "R1", "rack_face": "270"}],
            }
        )
    )
    assert parsed.data.bin_source_racks[0].rack_face == ("270",)


@pytest.mark.parametrize(
    "patch",
    [
        {"cancel_scope": "TASK", "bin_source_racks": [{"rack_id": "R1", "rack_face": ["90"]}]},
        {"cancel_scope": "PLAN_MEMBERS"},
        {"cancel_scope": "PLAN_MEMBERS", "bin_source_racks": []},
        {
            "cancel_scope": "PLAN_MEMBERS",
            "bin_source_racks": [{"rack_id": "R1", "rack_face": ["90", "90"]}],
        },
        {
            "cancel_scope": "PLAN_MEMBERS",
            "bin_source_racks": [{"rack_id": "R1", "rack_face": []}],
        },
        {
            "cancel_scope": "PLAN_MEMBERS",
            "bin_source_racks": [{"rack_id": "R1", "rack_face": [""]}],
        },
    ],
)
def test_cancel_rejects_mixed_empty_or_duplicate_selection(patch: dict[str, object]) -> None:
    data = {"task_id": "PICK-1"} | deepcopy(patch)
    with pytest.raises(ValidationError):
        parse_picking_task_cancel_event(_event(data))


@pytest.mark.parametrize("slots", [None, ["A-1", "A-2"]])
def test_direct_pick_cancel_optional_slots_preserve_request_identity_and_body(slots) -> None:
    selector = {"plan_revision": 2, "rack_id": "D1", "rack_face": "A"}
    if slots is not None:
        selector["slot_ids"] = slots
    payload = _event({"task_id": "PICK-1", "cancel_scope": "PLAN_MEMBERS", "direct_pick_sources": [selector]})
    parsed = parse_picking_task_cancel_event(payload)
    assert parsed.data.direct_pick_sources[0].slot_ids == slots
    assert parsed.model_dump(mode="json", exclude_none=True) == payload


@pytest.mark.parametrize("slots", [None, [], ["A-1", "A-1"], "A-1", [""]])
def test_direct_pick_cancel_rejects_explicit_null_empty_or_invalid_slots(slots) -> None:
    payload = _event(
        {
            "task_id": "PICK-1",
            "cancel_scope": "PLAN_MEMBERS",
            "direct_pick_sources": [{"plan_revision": 2, "rack_id": "D1", "rack_face": "A", "slot_ids": slots}],
        }
    )
    with pytest.raises(ValidationError):
        parse_picking_task_cancel_event(payload)


def test_direct_pick_cancel_openapi_allows_omission_but_not_null_or_empty_slots() -> None:
    from src.app.wms_adapter.outbound_picking.openapi import PICKING_TASK_CANCEL_EVENT_REQUEST_SCHEMA

    branch = PICKING_TASK_CANCEL_EVENT_REQUEST_SCHEMA["properties"]["data"]["oneOf"][1]
    selector = branch["properties"]["direct_pick_sources"]["items"]
    assert selector["required"] == ["plan_revision", "rack_id", "rack_face"]
    assert selector["properties"]["slot_ids"]["type"] == "array"
    assert selector["properties"]["slot_ids"]["minItems"] == 1
    assert selector["properties"]["slot_ids"]["uniqueItems"] is True
