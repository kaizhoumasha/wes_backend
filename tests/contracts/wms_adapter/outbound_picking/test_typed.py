"""prepare typed lowering 的接入差异，不重复 HTTP 可靠性测试。"""

import pytest
import wes_plugin_sdk as sdk

from src.app.wms_adapter.outbound_picking.typed import decode_outcome, encode_request


def test_prepare_request_lowers_picking_task_identity_into_strict_wire() -> None:
    intent = sdk.wms_operations.outbound_picking_task_prepare(
        operation_id="0198e729-34bf-7001-8a01-000000000001",
        task_id="task-1",
        work_line_code="line-1",
    )
    assert encode_request(intent, timestamp=1000) == {
        "operation": "outbound.picking_task.prepare@v1",
        "operation_id": "0198e729-34bf-7001-8a01-000000000001",
        "timestamp": 1000,
        "data": {"task_id": "task-1", "workline_code": "line-1"},
    }
    with pytest.raises(ValueError):
        encode_request(intent, timestamp=True)
    with pytest.raises(TypeError):
        encode_request({"task_id": "task-1"}, timestamp=1000)


@pytest.mark.parametrize(
    ("code", "data", "expected"),
    [
        ("PREPARE_ACCEPTED", {}, sdk.PrepareAccepted()),
        ("UNAVAILABLE", {}, sdk.OperationUnavailable()),
        ("CONFLICT", {"reason_code": "REVISION_CONFLICT"}, sdk.OperationConflict("REVISION_CONFLICT")),
        (
            "REJECTED",
            {"reason_code": "INVALID_DATA", "field_path": "/data/task_id"},
            sdk.OperationRejected("INVALID_DATA", "/data/task_id"),
        ),
    ],
)
def test_prepare_response_becomes_typed_outcome(code: str, data: dict, expected: object) -> None:
    outcome = decode_outcome(
        {"operation_id": "0198e729-34bf-7001-8a01-000000000001", "timestamp": 1000, "code": code, "data": data}
    )
    assert type(outcome) is sdk.PickingTaskPrepareOutcome
    assert outcome.result == expected
    assert not hasattr(outcome, "payload")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"code": "BUSY"},
        {
            "operation_id": "0198e729-34bf-7001-8a01-000000000001",
            "timestamp": 1000,
            "code": "PREPARE_ACCEPTED",
            "data": None,
        },
        {
            "operation_id": "0198e729-34bf-7001-8a01-000000000001",
            "timestamp": 1000,
            "code": "CONFLICT",
            "data": {"reason_code": "POSITION_CONFLICT"},
        },
    ],
)
def test_prepare_outcome_rejects_unknown_or_malformed_persisted_response(payload: object) -> None:
    with pytest.raises(ValueError):
        decode_outcome(payload)
