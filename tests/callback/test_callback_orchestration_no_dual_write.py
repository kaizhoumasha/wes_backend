"""Test: callback path 验证无双写 (Plan Task 4 验收)

锁定 callback 路径不再写 WorklineInbox:
- process_event 路径: 只调 RuntimeInboxService.accept_received
- process_external 路径: 只调 CallbackRuntimeInboxWriter, RuntimeInbox 为唯一事实源
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.callback.services.callback_orchestration_service import (
    CallbackOrchestrationService,
)


@pytest.mark.asyncio
async def test_process_event_does_not_write_workline_inbox() -> None:
    """process_event 路径应只写 RuntimeInbox, 不调 inbox_service.create_device_event_inbox。"""
    runtime_inbox_record = SimpleNamespace(id=42, created=True)
    writer = SimpleNamespace(
        write_event_callback=AsyncMock(return_value=SimpleNamespace(created=True, record=runtime_inbox_record))
    )
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)

    # 模拟 WorklineInboxService. 任何调用 create_device_event_inbox 都应失败
    inbox_service = SimpleNamespace(
        create_device_event_inbox=AsyncMock(side_effect=AssertionError("双写被触发！违反 RuntimeInbox 单一事实源约束"))
    )

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
        inbox_service=inbox_service,  # type: ignore[arg-type]
        enqueue_processing=lambda: None,
    )

    # 验证: RuntimeInbox 已写
    writer.write_event_callback.assert_awaited_once()
    assert outcome.is_duplicate is False

    # 验证: inbox_service.create_device_event_inbox 未被调用 (no dual write)
    inbox_service.create_device_event_inbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_external_does_not_require_or_write_workline_inbox() -> None:
    """process_external 不再接受 legacy inbox service，也不产生 WorklineInbox 双写。"""
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
