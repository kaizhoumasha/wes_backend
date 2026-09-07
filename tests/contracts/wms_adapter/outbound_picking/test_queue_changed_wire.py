"""队列更新只校验 wire 形状，当前版本与实际变化由事务服务判断。"""

import pytest

from src.app.wms_adapter.outbound_picking.queue_changed_wire import (
    PickingTaskQueueChangedInvalidData,
    parse_picking_task_queue_changed_event,
    parse_picking_task_queue_changed_receipt,
)


def valid_event():
    return {
        "operation_id": "019f12d1-1198-72cb-a980-d83af6ab9df8",
        "operation": "outbound.picking_task.queue_changed@v1",
        "timestamp": 1786061000000,
        "data": {"task_id": "PICK-001", "queue_revision": 2, "dispatch_sequence": 90},
    }


@pytest.mark.parametrize(
    "updates", [{"dispatch_sequence": 90}, {"not_before": 0}, {"dispatch_sequence": 1, "not_before": 2**63 - 1}]
)
def test_optional_updates_preserve_omission_and_values(updates):
    raw = valid_event()
    raw["data"] = {"task_id": "PICK-001", "queue_revision": 2**63 - 1, **updates}
    parsed = parse_picking_task_queue_changed_event(raw)
    assert parsed.model_dump(exclude_unset=True) == raw


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("queue_revision", 1),
        ("queue_revision", 0),
        ("queue_revision", True),
        ("queue_revision", "2"),
        ("queue_revision", 2.0),
        ("queue_revision", 2**63),
        ("dispatch_sequence", 0),
        ("dispatch_sequence", -1),
        ("dispatch_sequence", True),
        ("dispatch_sequence", "1"),
        ("dispatch_sequence", 1.0),
        ("dispatch_sequence", 2**63),
        ("dispatch_sequence", None),
        ("not_before", None),
        ("not_before", -1),
        ("not_before", True),
        ("not_before", "0"),
        ("not_before", 0.0),
        ("not_before", 2**63),
        ("task_id", ""),
        ("task_id", "x" * 101),
        ("task_id", " PICK"),
        ("task_id", "PICK\u0000"),
        ("task_id", 1),
        ("task_type", "MANUAL"),
        ("unknown", 1),
    ],
)
def test_invalid_fields_retain_original_receipt(field, value):
    raw = valid_event()
    raw["data"][field] = value
    with pytest.raises(ValueError):
        parse_picking_task_queue_changed_event(raw)
    receipt = parse_picking_task_queue_changed_receipt(raw)
    assert isinstance(receipt, PickingTaskQueueChangedInvalidData)
    assert receipt.raw_envelope is raw


def test_at_least_one_update_is_required():
    raw = valid_event()
    del raw["data"]["dispatch_sequence"]
    with pytest.raises(ValueError):
        parse_picking_task_queue_changed_event(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [("operation", "outbound.picking_task.issued@v1"), ("timestamp", -1), ("timestamp", True), ("unknown", 1)],
)
def test_envelope_is_strict(field, value):
    raw = valid_event()
    raw[field] = value
    with pytest.raises(ValueError):
        parse_picking_task_queue_changed_event(raw)


@pytest.mark.parametrize("timestamp", [0, 2**63 - 1])
def test_timestamp_accepts_nonnegative_int64(timestamp):
    raw = valid_event()
    raw["timestamp"] = timestamp
    assert parse_picking_task_queue_changed_event(raw).timestamp == timestamp


@pytest.mark.parametrize("timestamp", [2**63, "0", 0.0])
def test_timestamp_rejects_invalid_int64(timestamp):
    raw = valid_event()
    raw["timestamp"] = timestamp
    with pytest.raises(ValueError):
        parse_picking_task_queue_changed_event(raw)
