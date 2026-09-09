"""退料货架到位纯 SDK 到宿主 wire 的边界。"""

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.outbound_picking.arrival_report_typed import decode_outcome, encode_request

OPERATION_ID = "019f3404-a100-7b01-8b01-000000000001"


def test_encode_preserves_complete_immutable_fact() -> None:
    intent = wms_operations.outbound_return_rack_arrival_report(
        operation_id=OPERATION_ID,
        task_id="task",
        transport_task_id="transport",
        outcome_revision=2,
        rack_id="rack",
        final_position=sdk.TransportRackPosition("work"),
        arrival_face="000A",
    )
    payload = encode_request(intent, timestamp=0)
    assert payload == {
        "operation_id": OPERATION_ID,
        "operation": "outbound.return_rack.arrival_report@v1",
        "timestamp": 0,
        "data": {
            "task_id": "task",
            "transport_task_id": "transport",
            "outcome_revision": 2,
            "rack_id": "rack",
            "final_position": {"type": "RACK_POSITION", "location_code": "work"},
            "arrival_face": "000A",
        },
    }
    payload["data"]["final_position"]["location_code"] = "changed"
    assert intent.final_position.location_code == "work"
    with pytest.raises(TypeError):
        encode_request({"task_id": "task"}, timestamp=0)


@pytest.mark.parametrize(
    "code,data,result_type",
    [
        ("RECORDED", {}, sdk.FactRecorded),
        ("DUPLICATE", {}, sdk.FactRecorded),
        ("UNAVAILABLE", {}, sdk.OperationUnavailable),
        ("CONFLICT", {"reason_code": "REVISION_CONFLICT"}, sdk.OperationConflict),
        ("REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/rack_id"}, sdk.OperationRejected),
    ],
)
def test_decode_closed_persisted_responses(code, data, result_type) -> None:
    outcome = decode_outcome({"operation_id": OPERATION_ID, "timestamp": 0, "code": code, "data": data})
    assert type(outcome) is sdk.ReturnRackArrivalReportOutcome
    assert type(outcome.result) is result_type
    if result_type is sdk.FactRecorded:
        assert outcome.result.duplicate is (code == "DUPLICATE")


@pytest.mark.parametrize(
    "patch",
    [
        {"code": "BUSY"},
        {"code": "PREPARE_ACCEPTED"},
        {"code": None},
        {"data": None},
        {"operation_id": "bad"},
        {"code": "CONFLICT", "data": {"reason_code": "POSITION_CONFLICT"}},
        {"code": "REJECTED", "data": {"reason_code": "INVALID_DATA", "field_path": "/bad~2"}},
    ],
)
def test_decode_rejects_unapproved_or_malformed_persisted_response(patch) -> None:
    with pytest.raises(ValueError):
        decode_outcome({"operation_id": OPERATION_ID, "timestamp": 0, "code": "RECORDED", "data": {}} | patch)
