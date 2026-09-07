"""单盘放置事实保持原始扫码、来源、去向与设备时间。"""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.client import WmsAccessResult
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_integration.outbound_picking.services.picking_task_confirmation_owner import (
    PickingTaskConfirmationOwnerService,
)
from src.core.outbound_http import OutboundHttpDeliveryState
from src.utils.canonical_json import canonical_json_digest

OPERATION = "outbound.material.movement_report@v1"
OPERATION_ID = "019f3422-f4a8-7247-98f0-8118dfb7f45e"


def request(ng=False):
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 0,
        "data": {
            "task_id": "TASK-1",
            "source_locator": {
                "type": "BIN_CELL",
                "rack_id": "RACK-1",
                "rack_face": "面 A",
                "bin_code": "BIN-1",
                "cell_id": "CELL-1",
            },
            "PkgID": " 原始扫码 ",
            "to_locator": {"type": "NG_ZONE", "zone_code": "NG-1"}
            if ng
            else {"type": "RACK_SLOT", "rack_id": "TARGET-1", "rack_face": "B", "slot_id": "SLOT-1"},
            "occurred_at": 123,
        },
    }


def response(code="RECORDED", data=None):
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": {} if data is None else data}


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


@pytest.mark.parametrize("ng", [False, True])
def test_typed_request_preserves_exact_fact(ng):
    from src.app.wms_adapter.outbound_picking.movement_report_typed import encode_request

    source = sdk.PickingBinCell("RACK-1", "面 A", "BIN-1", "CELL-1")
    target = sdk.PickingNgZone("NG-1") if ng else sdk.PickingRackSlot("TARGET-1", "B", "SLOT-1")
    intent = wms_operations.outbound_material_movement_report(
        operation_id=OPERATION_ID,
        task_id="TASK-1",
        source_locator=source,
        pkg_id=" 原始扫码 ",
        to_locator=target,
        occurred_at=123,
    )
    assert intent.source_locator is source
    assert intent.to_locator is target
    assert encode_request(intent, timestamp=0) == request(ng)
    with pytest.raises(FrozenInstanceError):
        intent.occurred_at = 124


@pytest.mark.parametrize("ng", [False, True])
@pytest.mark.parametrize("code", ["RECORDED", "DUPLICATE"])
@pytest.mark.asyncio
async def test_fact_ack_closes_obligation_through_static_facts_route(ng, code):
    from src.app.wms_adapter.outbound_picking.movement_report_typed import decode_outcome

    body = response(code)
    client = client_response(body)
    result = await dispatch(client, request(ng))
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == code
    assert result.normalized_response == body
    assert decode_outcome(body).result == sdk.FactRecorded(code == "DUPLICATE")
    assert client.post.await_args.args == ("/api/v1/wes/facts",)
    assert client.post.await_args.kwargs["json"] == request(ng)


@pytest.mark.parametrize(
    "field,value",
    [
        ("PkgID", ""),
        ("PkgID", "x" * 257),
        ("PkgID", 123),
        ("occurred_at", -1),
        ("occurred_at", 2**63),
        ("occurred_at", True),
        ("source_locator", {"type": "NG_ZONE", "zone_code": "NG-1"}),
        ("to_locator", {"type": "BIN_CELL", "rack_id": "R", "rack_face": "A", "bin_code": "B", "cell_id": "C"}),
        ("command_code", "CMD-1"),
        ("six_in_one", {}),
        ("business_exception_code", "MATERIAL_REJECTED"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_unapproved_fact_fields_never_send(field, value):
    payload = request()
    payload["data"][field] = value
    client = client_response(response())
    assert (await dispatch(client, payload)).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "status,code,data,expected",
    [
        (503, "UNAVAILABLE", {}, WmsDispatchCode.RETRY),
        (409, "CONFLICT", {"reason_code": "REFERENCE_CONFLICT"}, WmsDispatchCode.RECONCILING),
        (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/PkgID"}, WmsDispatchCode.RECONCILING),
        (202, "RECORDED", {}, WmsDispatchCode.RECONCILING),
        (200, "RECORDED", {"result": "COMPLETED"}, WmsDispatchCode.RECONCILING),
    ],
)
@pytest.mark.asyncio
async def test_response_is_closed_and_http_status_bound(status, code, data, expected):
    body = response(code, data)
    result = await dispatch(client_response(body, status))
    assert result.code is expected
    assert result.normalized_response == body


@pytest.mark.asyncio
async def test_identity_or_digest_drift_fails_closed():
    client = client_response(response())
    assert (await dispatch(client, request_digest="wrong")).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_awaited()
    body = response() | {"operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45f"}
    result = await dispatch(client_response(body))
    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == body


@pytest.mark.parametrize(
    "state,accepted", [("QUEUED", False), ("PREPARING", False), ("EXECUTING", True), ("EXECUTION_COMPLETED", True)]
)
@pytest.mark.asyncio
async def test_saved_movement_obligation_survives_task_completion(state, accepted):
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(status=state, workline_id=1))
    )
    assert (
        await PickingTaskConfirmationOwnerService(repository).validate_response_owner(
            object(),
            picking_task_id=1,
            operation=OPERATION,
        )
        is accepted
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("pkg_id", ""),
        ("pkg_id", "x" * 257),
        ("pkg_id", 1),
        ("occurred_at", True),
        ("occurred_at", -1),
        ("occurred_at", 2**63),
        ("task_id", "bad id"),
        ("source_locator", {}),
        ("to_locator", {}),
    ],
)
def test_sdk_rejects_invalid_facts(field, value):
    values = {
        "operation_id": OPERATION_ID,
        "task_id": "TASK-1",
        "source_locator": sdk.PickingRackSlot("RACK-1", "A", "SLOT-1"),
        "pkg_id": "PKG-1",
        "to_locator": sdk.PickingNgZone("NG-1"),
        "occurred_at": 123,
    }
    with pytest.raises((TypeError, ValueError)):
        wms_operations.outbound_material_movement_report(**(values | {field: value}))


def test_direct_pick_report_and_error_outcomes():
    from src.app.wms_adapter.outbound_picking.movement_report_typed import decode_outcome, encode_request

    intent = wms_operations.outbound_material_movement_report(
        operation_id=OPERATION_ID,
        task_id="TASK-1",
        source_locator=sdk.PickingRackSlot("RACK-1", "A", "SLOT-1"),
        pkg_id="x" * 256,
        to_locator=sdk.PickingNgZone("NG-1"),
        occurred_at=0,
    )
    assert encode_request(intent, timestamp=0)["data"] == {
        "task_id": "TASK-1",
        "source_locator": {"type": "RACK_SLOT", "rack_id": "RACK-1", "rack_face": "A", "slot_id": "SLOT-1"},
        "PkgID": "x" * 256,
        "to_locator": {"type": "NG_ZONE", "zone_code": "NG-1"},
        "occurred_at": 0,
    }
    assert decode_outcome(response("UNAVAILABLE")).result == sdk.OperationUnavailable()
    assert decode_outcome(response("CONFLICT", {"reason_code": "REFERENCE_CONFLICT"})).result == sdk.OperationConflict(
        "REFERENCE_CONFLICT"
    )
    assert decode_outcome(
        response("REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/PkgID"})
    ).result == sdk.OperationRejected("INVALID_DATA", "/data/PkgID")
    with pytest.raises(TypeError):
        sdk.MaterialMovementReportOutcome(sdk.OperationBusy(1))
    with pytest.raises(ValueError):
        sdk.MaterialMovementReportOutcome(sdk.OperationConflict("POSITION_CONFLICT"))
    with pytest.raises(ValueError):
        sdk.PickingNgZone("invalid zone")
