"""External callback ingress route 与编排兜底合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.callback.models import CallbackExternalIngressResponse
from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from tests.api.callback_test_support import _get_route, create_wms_external_payload


def _writer(*, created: bool = True) -> SimpleNamespace:
    result = SimpleNamespace(created=created, record=SimpleNamespace(id=901))
    return SimpleNamespace(write_external_callback=AsyncMock(return_value=result))


def test_external_route_declares_named_response_model() -> None:
    assert _get_route("/external", "POST").response_model == CallbackExternalIngressResponse


def test_external_route_publishes_runtime_inbox_http_error_contracts() -> None:
    from main import app

    responses = app.openapi()["paths"]["/api/v1/callback/external"]["post"]["responses"]
    assert {"200", "400", "409", "413"} <= responses.keys()


@pytest.mark.asyncio
async def test_external_broker_failure_does_not_rollback_committed_runtime_inbox() -> None:
    service = CallbackOrchestrationService(runtime_inbox_writer=_writer())
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    enqueue = MagicMock(side_effect=RuntimeError("broker unavailable"))

    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        outcome = await service.process_external(
            db,
            callback_type="AGV_TASK_RESULT",
            payload=create_wms_external_payload(
                callback_type="AGV_TASK_RESULT",
                source_system="AGV",
                dispatch_key="agv:transport:broker-fail",
            ),
            request_id="req-agv-broker-fail",
            trace_id="trace-agv-broker-fail",
            enqueue_processing=enqueue,
        )

    assert outcome.is_duplicate is False
    db.commit.assert_awaited_once()
    enqueue.assert_called_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_rejects_retired_callback_before_runtime_inbox() -> None:
    writer = _writer()
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)

    with pytest.raises(ValueError, match="callback_type is not allowed"):
        await service.process_external(
            SimpleNamespace(),
            callback_type="WMS_RACK_TASK_RESULT",
            payload=create_wms_external_payload(callback_type="WMS_RACK_TASK_RESULT"),
            request_id="req-retired",
        )

    writer.write_external_callback.assert_not_awaited()
