"""External callback 入站 ACK 以 RuntimeInbox 为唯一权威。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter


def _accepted(*, created: bool = True) -> SimpleNamespace:
    return SimpleNamespace(created=created, record=SimpleNamespace(id=301))


@pytest.mark.asyncio
async def test_external_writer_persists_canonical_processing_evidence() -> None:
    service = SimpleNamespace(accept_received=AsyncMock(return_value=_accepted()))
    writer = CallbackRuntimeInboxWriter(service=service)
    payload = {
        "callback_type": "AGV_TASK_RESULT",
        "source_system": "AGV",
        "trace_id": "trace-agv-001",
        "command_code": "AGV-CMD-001",
        "result": "SUCCESS",
    }

    await writer.write_external_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="req-external-evidence",
        trace_id="trace-external-evidence",
    )

    kwargs = service.accept_received.await_args.kwargs
    assert kwargs["kind"] == "EXTERNAL_HTTP"
    assert kwargs["payload_json"] == payload
    assert kwargs["payload_json"] is not payload
    assert kwargs["trace_id"] == "trace-external-evidence"


@pytest.mark.asyncio
async def test_external_writer_fallback_source_event_id_is_payload_stable() -> None:
    service = SimpleNamespace(accept_received=AsyncMock(side_effect=[_accepted(), _accepted()]))
    writer = CallbackRuntimeInboxWriter(service=service)
    payload = {
        "callback_type": "AGV_TASK_RESULT",
        "source_system": "AGV",
        "trace_id": "trace-agv-001",
        "command_code": "AGV-CMD-001",
        "result": "SUCCESS",
    }

    await writer.write_external_callback(SimpleNamespace(), payload=payload, request_id="http-1")
    await writer.write_external_callback(SimpleNamespace(), payload=payload, request_id="http-2")

    first, second = service.accept_received.await_args_list
    assert first.kwargs["source_event_id"] == second.kwargs["source_event_id"]


@pytest.mark.asyncio
async def test_external_duplicate_does_not_trigger_second_processor() -> None:
    writer = SimpleNamespace(write_external_callback=AsyncMock(return_value=_accepted(created=False)))
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()

    outcome = await service.process_external(
        SimpleNamespace(),
        callback_type="AGV_TASK_RESULT",
        payload={
            "callback_type": "AGV_TASK_RESULT",
            "source_system": "AGV",
            "trace_id": "trace-ext-dup",
            "command_code": "AGV-REQ-DUP-001",
            "result": "SUCCESS",
        },
        request_id="req-external-dup",
        trace_id="trace-ext-dup",
    )

    assert outcome.is_duplicate is True
    service._commit_and_enqueue_runtime_inbox_processing.assert_not_awaited()
