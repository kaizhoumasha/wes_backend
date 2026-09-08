"""完成确认只产生封闭业务结果，不自动结束任务。"""

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

OPERATION = "outbound.picking_task.completion_confirm@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request(revision=5):
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 0,
        "data": {"task_id": "TASK-1", "last_applied_plan_revision": revision},
    }


def response(data, code="DECIDED"):
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}


@pytest.mark.parametrize("revision", [0, 5, 2**63 - 1])
def test_typed_request_preserves_revision(revision):
    from src.app.wms_adapter.outbound_picking.completion_confirm_typed import encode_request

    intent = wms_operations.outbound_picking_task_completion_confirm(
        operation_id=OPERATION_ID,
        task_id="TASK-1",
        last_applied_plan_revision=revision,
    )
    assert encode_request(intent, timestamp=0) == request(revision)
    with pytest.raises(FrozenInstanceError):
        intent.last_applied_plan_revision = 1


@pytest.mark.parametrize("revision", [-1, True, 2**63, None, "5"])
def test_invalid_revision_rejected_at_both_boundaries(revision):
    from src.app.wms_adapter.outbound_picking.completion_confirm_wire import parse_completion_confirm_request

    with pytest.raises((ValueError, TypeError)):
        wms_operations.outbound_picking_task_completion_confirm(
            operation_id=OPERATION_ID,
            task_id="TASK-1",
            last_applied_plan_revision=revision,
        )
    with pytest.raises((ValueError, TypeError)):
        parse_completion_confirm_request(request(revision))


@pytest.mark.parametrize(
    "data,kind",
    [
        ({"result": "COMPLETED"}, "PickingTaskCompleted"),
        ({"result": "PLAN_REVISION_STALE", "current_plan_revision": 6}, "PickingTaskPlanRevisionStale"),
        ({"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 1000}, "PickingTaskBusinessInProgress"),
    ],
)
@pytest.mark.parametrize("revision", [0, 5])
@pytest.mark.asyncio
async def test_closed_results_end_obligation_without_technical_retry(data, kind, revision):
    from src.app.wms_adapter.outbound_picking.completion_confirm_typed import decode_outcome

    payload = request(revision)
    body = response(data)
    client = AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=200,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=body,
                failure_kind=None,
                json_failure=None,
            )
        )
    )
    result = await WmsConfirmationAdapter(client).dispatch(
        operation=OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
    )
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == data["result"]
    assert result.retry_after_ms is None
    assert result.normalized_response == body
    assert type(decode_outcome(body).result) is getattr(sdk, kind)
    client.post.assert_awaited_once_with(
        "/api/v1/wes/decisions",
        json=payload,
        max_request_body_bytes=262144,
        max_response_body_bytes=262144,
        observation=None,
    )


@pytest.mark.parametrize(
    "data",
    [
        {"result": "COMPLETED", "retry_after_ms": 1},
        {"result": "COMPLETED", "current_plan_revision": 5},
        {"result": "PLAN_REVISION_STALE", "current_plan_revision": 0},
        {"result": "PLAN_REVISION_STALE", "current_plan_revision": 5},
        {"result": "PLAN_REVISION_STALE", "current_plan_revision": True},
        {"result": "PLAN_REVISION_STALE", "current_plan_revision": 2**63},
        {"result": "PLAN_REVISION_STALE", "current_plan_revision": 6, "retry_after_ms": 1},
        {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 0},
        {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 60001},
        {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": True},
        {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 1, "current_plan_revision": 5},
        {"result": "FAILED"},
    ],
)
def test_invalid_or_nonadvancing_business_results_fail_closed(data):
    from src.app.wms_adapter.outbound_picking.completion_confirm_wire import (
        parse_completion_confirm_request,
        parse_completion_confirm_response,
    )

    with pytest.raises(ValueError):
        parse_completion_confirm_response(200, response(data), request=parse_completion_confirm_request(request()))


@pytest.mark.parametrize(
    "state,accepted",
    [
        ("QUEUED", False),
        ("PREPARING", True),
        ("EXECUTING", True),
        ("EXECUTION_COMPLETED", False),
    ],
)
@pytest.mark.asyncio
async def test_completion_owner_allows_preparing_without_plan_and_executing(state, accepted):
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(
            return_value=SimpleNamespace(status=state, workline_id=1),
        )
    )
    assert (
        await PickingTaskConfirmationOwnerService(repository).validate_response_owner(
            object(),
            picking_task_id=1,
            operation=OPERATION,
        )
        is accepted
    )
