"""Test: callback path 验证 RuntimeInbox 单写 (Plan Task 4 验收)

锁定 callback 路径只写 RuntimeInbox:
- process_event 路径: 只调 RuntimeInboxService.accept_received
- process_external 路径: 只调 CallbackRuntimeInboxWriter, RuntimeInbox 为唯一事实源
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.callback.services.callback_orchestration_service import (
    CallbackOrchestrationService,
)


@pytest.mark.asyncio
async def test_process_event_writes_only_runtime_inbox() -> None:
    """process_event 路径应只写 RuntimeInbox。"""
    runtime_inbox_record = SimpleNamespace(id=42, created=True)
    writer = SimpleNamespace(
        write_event_callback=AsyncMock(return_value=SimpleNamespace(created=True, record=runtime_inbox_record))
    )
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)

    from src.app.callback.models import CallbackEventRequest

    request = CallbackEventRequest.model_validate(
        {
            "device_code": "ARM_01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1_702_627_300_000,
            "data": {"HHPN": "X"},
        }
    )

    outcome = await service.process_event(
        AsyncMock(),  # type: ignore[arg-type]
        event_request=request,
        request_id="req-001",
        is_workline_event=True,
        canonical_event_type="SCAN_COMPLETED",
        enqueue_processing=lambda: None,
    )

    # 验证: RuntimeInbox 已写
    writer.write_event_callback.assert_awaited_once()
    assert outcome.is_duplicate is False


@pytest.mark.asyncio
async def test_process_event_terminalizes_non_workline_event_without_runtime_processor() -> None:
    """非工作线设备事件保留 RuntimeInbox 幂等证据，但不进入行动队列。"""
    from src.app.callback.models import CallbackEventRequest

    writer = SimpleNamespace(
        write_event_callback=AsyncMock(return_value=SimpleNamespace(created=True, record=SimpleNamespace(id=51)))
    )
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    db = SimpleNamespace(commit=AsyncMock())
    enqueue_processing = MagicMock()
    request = CallbackEventRequest.model_validate(
        {
            "device_code": "STANDALONE_SENSOR_01",
            "event_type": "DEVICE_STATUS_CHANGED",
            "timestamp": 1_702_627_300_000,
            "data": {"status": "ONLINE"},
        }
    )

    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ) as publish_events:
        outcome = await service.process_event(
            db,  # type: ignore[arg-type]
            event_request=request,
            request_id="req-non-workline-001",
            is_workline_event=False,
            canonical_event_type="DEVICE_STATUS_CHANGED",
            enqueue_processing=enqueue_processing,
        )

    assert outcome.is_duplicate is False
    assert outcome.trace_id is None
    writer.write_event_callback.assert_awaited_once()
    assert writer.write_event_callback.await_args.kwargs["processing_required"] is False
    db.commit.assert_awaited_once()
    publish_events.assert_awaited_once_with(db)
    enqueue_processing.assert_not_called()


@pytest.mark.asyncio
async def test_process_external_writes_only_runtime_inbox() -> None:
    """process_external 仅持久化 RuntimeInbox。"""
    runtime_record = SimpleNamespace(id=43, trace_id="trace-external-001")
    writer = SimpleNamespace(
        write_external_callback=AsyncMock(return_value=SimpleNamespace(created=True, record=runtime_record))
    )
    rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
    handling_operation_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
    service = CallbackOrchestrationService(
        runtime_inbox_writer=writer,
        rack_task_service=rack_task_service,
        handling_operation_service=handling_operation_service,
    )
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_external(
        SimpleNamespace(),  # type: ignore[arg-type]
        callback_type="WMS_RACK_TASK_RESULT",
        payload={"callback_type": "WMS_RACK_TASK_RESULT", "dispatch_key": "rack:001"},
        request_id="req-external-001",
        trace_id="trace-external-001",
        enqueue_processing=lambda: None,
    )

    assert outcome.trace_id == "trace-external-001"
    writer.write_external_callback.assert_awaited_once()
    rack_task_service.record_callback_from_external_http.assert_awaited_once()
    handling_operation_service.record_callback_from_external_http.assert_not_awaited()
    service._commit_and_enqueue_runtime_inbox_processing.assert_awaited_once()
