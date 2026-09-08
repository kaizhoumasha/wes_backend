"""观察原收发和校验，不能再发送或改变原异常。"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from src.app.wms_adapter.client import WmsClient
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult


class RequestModel(BaseModel):
    model_config = ConfigDict(strict=True)
    count: int


def test_observed_validation_retains_the_actual_failed_contract_and_error_path() -> None:
    observation = WmsCallObservation(direction="WES_TO_WMS")
    with pytest.raises(ValidationError):
        validate_observed(RequestModel, {"count": "bad"}, observation=observation, side="request")
    assert observation.request_contract is RequestModel
    assert observation.request_errors[0]["loc"] == ("count",)
    assert "input" not in observation.request_errors[0]
    assert observation.request_validated is False


def test_observed_validation_returns_the_original_typed_model() -> None:
    observation = WmsCallObservation(direction="WES_TO_WMS")
    result = validate_observed(RequestModel, {"count": 2}, observation=observation, side="request")
    assert isinstance(result, RequestModel) and result.count == 2
    assert observation.request_validated is True


def test_successful_union_observes_the_actual_selected_contract() -> None:
    class AlternateModel(BaseModel):
        message: str

    adapter = TypeAdapter(RequestModel | AlternateModel)
    observation = WmsCallObservation(direction="WES_TO_WMS")
    result = validate_observed(adapter, {"count": 2}, observation=observation, side="response")
    assert isinstance(result, RequestModel)
    assert observation.response_contract is RequestModel


def test_response_identity_postcondition_cannot_remain_validated() -> None:
    from src.app.wms_adapter.outbound_picking.completion_confirm_wire import (
        COMPLETION_CONFIRM_OPERATION,
        parse_completion_confirm_request,
        parse_completion_confirm_response,
    )
    from src.core.uuid7 import new_uuid7

    request = parse_completion_confirm_request(
        {
            "operation_id": new_uuid7(),
            "operation": COMPLETION_CONFIRM_OPERATION,
            "timestamp": 1,
            "data": {"task_id": "TASK-1", "last_applied_plan_revision": 0},
        }
    )
    observation = WmsCallObservation(direction="WES_TO_WMS")
    with pytest.raises(ValueError, match="operation_id"):
        parse_completion_confirm_response(
            200,
            {
                "operation_id": new_uuid7(),
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "COMPLETED"},
            },
            request=request,
            observation=observation,
        )
    assert observation.response_validated is False
    assert observation.response_errors[0]["loc"] == ("operation_id",)
    assert observation.response_errors[0]["expected_value"] == request.operation_id


async def test_client_captures_invalid_response_without_second_send() -> None:
    transport = AsyncMock()
    transport.send.return_value = OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        failure_kind=None,
        status_code=502,
        response_headers=(("Content-Type", "application/json"),),
        decoded_body=b"invalid-json",
    )
    observation = WmsCallObservation(direction="WES_TO_WMS")
    result = await WmsClient(transport).post("/sample", json={"count": 2}, observation=observation)
    assert result.json_failure == "INVALID_JSON"
    assert observation.request_body == b'{"count":2}'
    assert observation.response_body == b"invalid-json"
    assert observation.status_code == 502
    transport.send.assert_awaited_once()


async def test_concurrent_attempts_of_same_identity_do_not_share_wire() -> None:
    both_started = asyncio.Event()
    sent = []

    async def send(request):
        sent.append(request.body)
        if len(sent) == 2:
            both_started.set()
        await both_started.wait()
        return OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            failure_kind=None,
            status_code=200,
            response_headers=(),
            decoded_body=request.body,
        )

    transport = AsyncMock()
    transport.send.side_effect = send
    client = WmsClient(transport)
    attempts = [WmsCallObservation(direction="WES_TO_WMS", operation_id="same") for _ in range(2)]
    async with asyncio.timeout(1):
        await asyncio.gather(
            *(
                client.post("/sample", json={"count": index}, observation=observation)
                for index, observation in enumerate(attempts)
            )
        )
    assert attempts[0].attempt_id != attempts[1].attempt_id
    for index, observation in enumerate(attempts):
        assert observation.request_body == observation.response_body == f'{{"count":{index}}}'.encode()
    assert transport.send.await_count == 2


def test_rejected_picking_receipt_keeps_original_error_and_observation() -> None:
    from src.app.wms_adapter.outbound_picking.wire import (
        PickingTaskIssuedInvalidData,
        parse_picking_task_issued_receipt,
    )

    observation = WmsCallObservation(direction="WMS_TO_WES")
    result = parse_picking_task_issued_receipt({}, observation=observation)
    assert isinstance(result, PickingTaskIssuedInvalidData)
    assert isinstance(result.validation_error, ValidationError)
    assert observation.request_errors
    assert observation.request_contract is not None
