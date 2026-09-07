"""入站批次纯 SDK 与已持久化 wire 边界。"""

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.outbound_picking.inbound_batch_typed import decode_outcome, encode_request

OPERATION_ID = "019f3404-a100-7b01-8b01-000000000001"


def test_encode_complete_request_and_timestamp_boundaries() -> None:
    intent = wms_operations.outbound_bin_inbound_batch(
        operation_id=OPERATION_ID,
        task_id="task",
        rack_id="rack",
        rack_face="000A",
        max_bin_count=2,
    )
    assert encode_request(intent, timestamp=0) == {
        "operation_id": OPERATION_ID,
        "operation": "outbound.bin.inbound_batch@v1",
        "timestamp": 0,
        "data": {"task_id": "task", "rack_id": "rack", "rack_face": "000A", "max_bin_count": 2},
    }
    for value in (-1, 2**63, True):
        with pytest.raises(ValueError):
            encode_request(intent, timestamp=value)
    with pytest.raises(TypeError):
        encode_request({}, timestamp=0)


def test_decode_ready_detaches_mutable_wire() -> None:
    payload = {
        "operation_id": OPERATION_ID,
        "timestamp": 0,
        "code": "DECIDED",
        "data": {
            "result": "READY",
            "bins": [
                {
                    "bin_code": "bin",
                    "source_locator": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": "rack",
                        "rack_face": "来源面",
                        "slot_id": "slot",
                    },
                }
            ],
        },
    }
    result = decode_outcome(payload).result
    assert type(result) is sdk.BinInboundBatchReady
    assert type(result.bins) is tuple
    assert result.bins[0].source_locator == sdk.TransportRackBinSlot("rack", "来源面", "slot")
    payload["data"]["bins"][0]["bin_code"] = "changed"
    assert result.bins[0].bin_code == "bin"


@pytest.mark.parametrize(
    "code,data,kind",
    [
        ("DECIDED", {"result": "NO_BATCH", "retry_after_ms": 60000}, sdk.BinBatchNoBatch),
        ("DECIDED", {"result": "RACK_FACE_DONE"}, sdk.BinInboundBatchRackFaceDone),
        ("UNAVAILABLE", {}, sdk.OperationUnavailable),
        ("CONFLICT", {"reason_code": "REVISION_CONFLICT"}, sdk.OperationConflict),
        ("REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/rack_id"}, sdk.OperationRejected),
    ],
)
def test_decode_closed_responses(code, data, kind) -> None:
    outcome = decode_outcome({"operation_id": OPERATION_ID, "timestamp": 0, "code": code, "data": data})
    assert type(outcome) is sdk.BinInboundBatchOutcome
    assert type(outcome.result) is kind


@pytest.mark.parametrize(
    "code,data",
    [
        ("DUPLICATE", {}),
        (None, {}),
        ("BUSY", {"retry_after_ms": 1}),
        ("DECIDED", {"result": "READY", "bins": []}),
        ("DECIDED", {"result": "NO_BATCH", "retry_after_ms": 0}),
        ("DECIDED", {"result": "RACK_FACE_DONE", "bins": []}),
        ("CONFLICT", {"reason_code": "POSITION_CONFLICT"}),
    ],
)
def test_decode_rejects_unapproved_persisted_response(code, data) -> None:
    with pytest.raises(ValueError):
        decode_outcome({"operation_id": OPERATION_ID, "timestamp": 0, "code": code, "data": data})
