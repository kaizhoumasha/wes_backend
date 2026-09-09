"""出库物料决定的封闭合同与静态派发接入。"""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.client import WmsAccessResult
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.core.outbound_http import OutboundHttpDeliveryState
from src.utils.canonical_json import canonical_json_digest

OPERATION = "outbound.material.decide@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request(source="BIN_CELL"):
    locator = {"type": source, "rack_id": "RACK-1", "rack_face": "面 A"}
    locator.update({"bin_code": "BIN-1", "cell_id": "CELL-1"} if source == "BIN_CELL" else {"slot_id": "SLOT-1"})
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 0,
        "data": {
            "task_id": "TASK-1",
            "source_locator": locator,
            "six_in_one": {
                "HHPN": "HHPN-1",
                "MfrPN": "MFR-1",
                "Qty": " 1e999999 ",
                "DateCode": "2610",
                "LotCode": "L-1",
                "PkgID": "PKG-1",
            },
            "scanned_at": 123,
        },
    }


def response(data, code="DECIDED"):
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}


def accepted(**overrides):
    return {
        "result": "ACCEPT",
        "target_locator": {"type": "RACK_SLOT", "rack_id": "TARGET-1", "rack_face": "B", "slot_id": "S-1"},
        "next_source_action": "CONTINUE",
    } | overrides


def rejected(**overrides):
    return {
        "result": "REJECT",
        "business_exception_code": "MATERIAL_REJECTED",
        "ng_locator": {"type": "NG_ZONE", "zone_code": "NG-1"},
        "source_disposition": "CONTINUE",
    } | overrides


def client_response(body, status=200):
    return AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=status,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=body,
                failure_kind=None,
                json_failure=None,
            )
        )
    )


async def dispatch(client, payload=None, **overrides):
    payload = request() if payload is None else payload
    args = {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "request_payload": payload,
        "request_digest": canonical_json_digest(payload),
    }
    return await WmsConfirmationAdapter(client).dispatch(**(args | overrides))


@pytest.mark.parametrize("source", ["BIN_CELL", "RACK_SLOT"])
def test_typed_request_preserves_scan_text_and_source(source):
    from src.app.wms_adapter.outbound_picking.material_decide_typed import encode_request

    body = request(source)
    locator = (
        sdk.PickingBinCell("RACK-1", "面 A", "BIN-1", "CELL-1")
        if source == "BIN_CELL"
        else sdk.PickingRackSlot("RACK-1", "面 A", "SLOT-1")
    )
    intent = wms_operations.outbound_material_decide(
        operation_id=OPERATION_ID,
        task_id="TASK-1",
        source_locator=locator,
        six_in_one=sdk.PickingSixInOne(**body["data"]["six_in_one"]),
        scanned_at=123,
    )
    assert encode_request(intent, timestamp=0) == body
    with pytest.raises(FrozenInstanceError):
        intent.six_in_one.Qty = "1"


@pytest.mark.parametrize("field,value", [("Qty", 1), ("Qty", ""), ("HHPN", "x" * 257), ("PkgID", None)])
def test_scan_requires_bounded_original_strings(field, value):
    from src.app.wms_adapter.outbound_picking.material_decide_wire import parse_material_decide_request

    body = request()
    body["data"]["six_in_one"][field] = value
    with pytest.raises(ValueError):
        parse_material_decide_request(body)
    with pytest.raises((ValueError, TypeError)):
        sdk.PickingSixInOne(**body["data"]["six_in_one"])


@pytest.mark.parametrize(
    "data",
    [
        accepted(),
        accepted(target_preparation={"mode": "ROTATE"}),
        accepted(
            target_preparation={
                "mode": "REPLACE",
                "rack_destination": {"type": "RACK_POSITION", "location_code": "STORE-1"},
            }
        ),
        rejected(),
        rejected(business_exception_code="SOURCE_CELL_MISMATCH", source_disposition="CLOSE"),
        {"result": "WAIT", "retry_after_ms": 1000},
    ],
)
@pytest.mark.asyncio
async def test_final_decisions_preserve_response_without_automatic_followup(data):
    from src.app.wms_adapter.outbound_picking.material_decide_typed import decode_outcome

    body = response(data)
    result = await dispatch(client_response(body))
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.normalized_response == body
    assert result.response_result == data["result"]
    assert result.retry_after_ms is None
    outcome = decode_outcome(body)
    assert isinstance(outcome, sdk.PickingMaterialOutcome)
    if data["result"] == "ACCEPT":
        assert outcome.result.target_locator == sdk.PickingRackSlot("TARGET-1", "B", "S-1")
        assert outcome.result.next_source_action == "CONTINUE"
        preparation = data.get("target_preparation")
        if preparation is None:
            assert outcome.result.target_preparation is None
        elif preparation["mode"] == "ROTATE":
            assert isinstance(outcome.result.target_preparation, sdk.PickingTargetRotate)
        else:
            assert outcome.result.target_preparation.rack_destination == sdk.TransportRackPosition("STORE-1")
    elif data["result"] == "REJECT":
        assert outcome.result.business_exception_code == data["business_exception_code"]
        assert outcome.result.ng_zone_code == "NG-1"
        assert outcome.result.source_disposition == data["source_disposition"]
    else:
        assert outcome.result.retry_after_ms == 1000


@pytest.mark.parametrize(
    "data",
    [accepted(), rejected(), rejected(business_exception_code="SOURCE_CELL_MISMATCH", source_disposition="CLOSE")],
)
@pytest.mark.asyncio
async def test_direct_pick_refuses_cell_only_actions(data):
    assert (await dispatch(client_response(response(data)), request("RACK_SLOT"))).code is WmsDispatchCode.RECONCILING


@pytest.mark.parametrize("data", [accepted(next_source_action="SOURCE_DONE"), rejected(source_disposition="CLOSE")])
@pytest.mark.asyncio
async def test_direct_pick_accepts_closed_source_results(data):
    assert (await dispatch(client_response(response(data)), request("RACK_SLOT"))).code is WmsDispatchCode.DETERMINATE


@pytest.mark.parametrize(
    "data",
    [
        accepted(target_preparation=None),
        accepted(target_preparation={"mode": "NONE"}),
        accepted(target_preparation={"mode": "REPLACE"}),
        accepted(
            target_preparation={"mode": "UNKNOWN", "rack_destination": {"type": "RACK_POSITION", "location_code": "X"}}
        ),
        accepted(next_source_action="CLOSE"),
        rejected(business_exception_code="UNKNOWN"),
        rejected(business_exception_code="SOURCE_CELL_MISMATCH"),
        {"result": "WAIT", "retry_after_ms": 60001},
        {"result": "WAIT", "retry_after_ms": True},
        {"result": "WAIT", "ng_locator": {}},
        {"result": "UNKNOWN"},
    ],
)
@pytest.mark.asyncio
async def test_rejects_unapproved_response_branches(data):
    result = await dispatch(client_response(response(data)))
    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == response(data)


@pytest.mark.parametrize(
    "code,status,data,expected",
    [
        ("UNAVAILABLE", 503, {}, WmsDispatchCode.RETRY),
        ("CONFLICT", 409, {"reason_code": "STATE_CONFLICT"}, WmsDispatchCode.RECONCILING),
        (
            "REJECTED",
            422,
            {"reason_code": "INVALID_DATA", "field_path": "/data/six_in_one/Qty"},
            WmsDispatchCode.RECONCILING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_shared_errors_keep_closed_wire_and_typed_outcome(code, status, data, expected):
    from src.app.wms_adapter.outbound_picking.material_decide_typed import decode_outcome

    body = response(data, code)
    assert (await dispatch(client_response(body, status))).code is expected
    assert isinstance(decode_outcome(body), sdk.PickingMaterialOutcome)


@pytest.mark.asyncio
async def test_response_identity_must_match_frozen_request():
    body = response(accepted()) | {"operation_id": "019f3405-2200-7b01-8b01-000000000002"}
    assert (await dispatch(client_response(body))).code is WmsDispatchCode.RECONCILING


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "bad id"),
        ("scanned_at", True),
        ("scanned_at", -1),
        ("scanned_at", 2**63),
        ("source_locator", {"type": "RACK_POSITION", "location_code": "X"}),
        ("six_in_one", {}),
    ],
)
@pytest.mark.asyncio
async def test_invalid_request_never_reaches_http(field, value):
    payload = request()
    payload["data"][field] = value
    client = client_response(response(accepted()))
    assert (await dispatch(client, payload)).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_called()


@pytest.mark.parametrize("field,value", [("rack_face", "x" * 11), ("rack_face", "\x00"), ("rack_id", "bad id")])
def test_source_locator_is_strict(field, value):
    from src.app.wms_adapter.outbound_picking.material_decide_wire import parse_material_decide_request

    body = request()
    body["data"]["source_locator"][field] = value
    with pytest.raises(ValueError):
        parse_material_decide_request(body)


@pytest.mark.asyncio
async def test_frozen_digest_drift_never_sends_request():
    client = client_response(response(accepted()))
    assert (await dispatch(client, request_digest="wrong")).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_called()


@pytest.mark.parametrize("field", ["HHPN", "MfrPN", "Qty", "DateCode", "LotCode", "PkgID"])
def test_all_scan_fields_preserve_unicode_at_length_boundary(field):
    from src.app.wms_adapter.outbound_picking.material_decide_wire import parse_material_decide_request

    body = request()
    body["data"]["six_in_one"][field] = "盘" * 256
    assert parse_material_decide_request(body).model_dump(mode="json") == body


def test_sdk_rejects_unapproved_result_and_mutable_source():
    with pytest.raises(TypeError):
        sdk.PickingMaterialOutcome({"result": "WAIT"})
    with pytest.raises(ValueError):
        sdk.PickingMaterialOutcome(sdk.OperationConflict("POSITION_CONFLICT"))
    with pytest.raises(TypeError):
        wms_operations.outbound_material_decide(
            operation_id=OPERATION_ID,
            task_id="TASK-1",
            source_locator=request()["data"]["source_locator"],
            six_in_one=sdk.PickingSixInOne(**request()["data"]["six_in_one"]),
            scanned_at=0,
        )
