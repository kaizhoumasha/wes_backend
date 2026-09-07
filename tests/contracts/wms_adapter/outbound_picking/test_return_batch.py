from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.app.wms_adapter.outbound_picking.return_batch_wire import (
    parse_bin_return_batch_request,
    parse_bin_return_batch_response,
)

OPERATION_ID = "019f3406-2200-7b03-8b01-000000000003"


def request():
    return {
        "operation": "outbound.bin.return_batch@v1",
        "operation_id": OPERATION_ID,
        "timestamp": 1,
        "data": {
            "workline_code": "LINE-1",
            "rack_id": "RACK-1",
            "rack_face": "A",
            "return_candidates": [
                {
                    "sequence_no": i,
                    "bin_code": f"BIN-{i}",
                    "source": {"type": "HANDOFF_POSITION", "location_code": "RETURN_BUFFER_01"},
                }
                for i in (1, 2)
            ],
        },
    }


def ready():
    return {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {
            "result": "READY",
            "moves": [
                {
                    "sequence_no": 1,
                    "bin_code": "BIN-1",
                    "target": {"type": "RACK_BIN_SLOT", "rack_id": "RACK-1", "rack_face": "A", "slot_id": "A-01"},
                }
            ],
        },
    }


def test_ready_accepts_only_a_fifo_prefix():
    response = parse_bin_return_batch_response(200, ready(), request=parse_bin_return_batch_request(request()))
    assert len(response.data.moves) == 1


@pytest.mark.parametrize("case", ["skip", "wrong_bin", "wrong_rack", "wrong_face", "duplicate_slot", "identity"])
def test_ready_rejects_response_outside_frozen_request(case):
    body = ready()
    move = body["data"]["moves"][0]
    if case == "skip":
        move["sequence_no"] = 2
    elif case == "wrong_bin":
        move["bin_code"] = "BIN-2"
    elif case == "wrong_rack":
        move["target"]["rack_id"] = "OTHER"
    elif case == "wrong_face":
        move["target"]["rack_face"] = "B"
    elif case == "identity":
        body["operation_id"] = "019f3406-2200-7b03-8b01-000000000004"
    else:
        second = deepcopy(move)
        second.update(sequence_no=2, bin_code="BIN-2")
        body["data"]["moves"].append(second)
    with pytest.raises(ValueError):
        parse_bin_return_batch_response(200, body, request=parse_bin_return_batch_request(request()))


@pytest.mark.parametrize("case", ["empty", "gap", "duplicate_bin", "too_many", "face", "source", "bool_sequence"])
def test_request_rejects_invalid_candidates(case):
    body = request()
    candidates = body["data"]["return_candidates"]
    if case == "empty":
        candidates.clear()
    elif case == "gap":
        candidates[1]["sequence_no"] = 3
    elif case == "duplicate_bin":
        candidates[1]["bin_code"] = "BIN-1"
    elif case == "too_many":
        candidates.extend(deepcopy(candidates) * 2)
    elif case == "face":
        body["data"]["rack_face"] = "12345678901"
    elif case == "source":
        candidates[0]["source"]["type"] = "RACK_POSITION"
    else:
        candidates[0]["sequence_no"] = True
    with pytest.raises(ValueError):
        parse_bin_return_batch_request(body)


def test_return_batch_sdk_roundtrip_and_immutable_candidates():
    from dataclasses import FrozenInstanceError

    from wes_plugin_sdk import BinReturnCandidate, wms_operations

    from src.app.wms_adapter.outbound_picking.return_batch_typed import decode_outcome, encode_request

    intent = wms_operations.outbound_bin_return_batch(
        operation_id=OPERATION_ID,
        workline_code="LINE-1",
        rack_id="RACK-1",
        rack_face="A",
        return_candidates=(
            BinReturnCandidate(1, "BIN-1", "RETURN_BUFFER_01"),
            BinReturnCandidate(2, "BIN-2", "RETURN_BUFFER_01"),
        ),
    )
    assert encode_request(intent, timestamp=1) == request()
    assert decode_outcome(ready()).result.moves[0].target.slot_id == "A-01"
    with pytest.raises(FrozenInstanceError):
        intent.return_candidates[0].bin_code = "changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_prefix", [False, True])
async def test_adapter_enforces_frozen_prefix_and_closes_no_batch(invalid_prefix):
    from src.app.wms_adapter.client import WmsAccessResult
    from src.app.wms_adapter.dispatch import WmsDispatchCode
    from src.app.wms_adapter.outbound_picking.return_batch_adapter import BinReturnBatchAdapter
    from src.core.outbound_http import OutboundHttpDeliveryState
    from src.utils.canonical_json import canonical_json_digest

    payload = request()
    response = ready()
    if invalid_prefix:
        response["data"]["moves"][0]["bin_code"] = "BIN-2"
    else:
        response["data"] = {"result": "NO_BATCH", "retry_after_ms": 1000}
    client = AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=200,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=response,
                failure_kind=None,
                json_failure=None,
            )
        )
    )
    result = await BinReturnBatchAdapter(client).dispatch(
        operation=payload["operation"],
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
    )
    assert result.code == (WmsDispatchCode.RECONCILING if invalid_prefix else WmsDispatchCode.DETERMINATE)
    assert result.retry_after_ms is None
    assert result.normalized_response == response
    client.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected",
    [
        ("active", True),
        ("closed", False),
        ("wrong_line", False),
        ("missing", False),
    ],
)
async def test_workline_owner_matches_frozen_wire_identity(case, expected):
    from types import SimpleNamespace

    from src.app.wms_integration.outbound_picking.services import ReturnBatchOwnerService

    workline = SimpleNamespace(id=7, line_code="LINE-1", is_active=True)
    if case == "closed":
        workline.is_active = False
    elif case == "wrong_line":
        workline.line_code = "OTHER"
    elif case == "missing":
        workline = None
    worklines = AsyncMock()
    worklines.get_for_update.return_value = workline
    owner = ReturnBatchOwnerService(worklines=worklines)
    assert await owner.validate_owner(object(), workline_id=7, request_payload=request()) is expected


def test_return_batch_rejects_retired_epoch_wire_field():
    body = request()
    body["data"]["line_run_epoch_id"] = "EPOCH-1"
    with pytest.raises(ValueError):
        parse_bin_return_batch_request(body)
