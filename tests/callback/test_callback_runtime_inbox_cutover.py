"""Callback 入站 ACK 切换到 RuntimeInbox 的服务级回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _LegacyDuplicateError(ValueError):
    """模拟旧 Workline inbox duplicate 信号。"""

    def __init__(self, message: str, *, existing_inbox: object | None = None) -> None:
        super().__init__(message)
        self.existing_inbox = existing_inbox


def _runtime_accept_result(*, created: bool, record_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        created=created,
        record=SimpleNamespace(
            id=record_id,
            status="RECEIVED",
            provider_code="ECS",
            event_type="result",
            source_event_id="evt-001",
        ),
    )


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_uses_canonical_types_without_channel_collapse() -> None:
    """writer 必须把 canonical callback/event/result type 传给 RuntimeInbox。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]

    _ = await writer.write_result_callback(
        SimpleNamespace(),
        payload={"event_id": "evt-shared-001", "command_code": "CMD-001", "result": "SUCCESS"},
        request_id="req-shared-001",
        canonical_result_type="DEVICE_RESULT",
    )
    _ = await writer.write_event_callback(
        SimpleNamespace(),
        payload={"event_id": "evt-shared-001", "device_code": "ARM_01", "event_type": "SCAN_COMPLETED"},
        request_id="req-shared-001",
        canonical_event_type="SCAN_COMPLETED",
    )
    _ = await writer.write_external_callback(
        SimpleNamespace(),
        payload={"source_event_id": "evt-shared-001", "callback_type": "AGV_TASK_RESULT", "source_system": "AGV"},
        request_id="req-shared-001",
    )

    assert [call["event_type"] for call in accepted_calls] == [
        "DEVICE_RESULT",
        "SCAN_COMPLETED",
        "AGV_TASK_RESULT",
    ]


@pytest.mark.asyncio
async def test_process_result_writes_runtime_inbox_before_legacy_workline_inbox() -> None:
    """结果回调必须先写 RuntimeInbox，再委托旧 Workline inbox 过渡消费。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.app.device.models.command import CommandCallbackResult

    call_order: list[str] = []
    writer_kwargs: dict[str, object] = {}

    class WriterStub:
        async def write_result_callback(self, *_args, **kwargs):
            call_order.append("runtime")
            writer_kwargs.update(kwargs)
            return _runtime_accept_result(created=True, record_id=101)

    class InboxServiceStub:
        async def create_command_result_inbox(self, **_kwargs):
            call_order.append("legacy")
            return SimpleNamespace(id=301)

    callback = CommandCallbackResult.model_validate(
        {
            "command_code": "CMD-ACK-001",
            "device_code": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1_702_627_250_000,
            "data": {"task_type": "PICK_AND_PUT"},
        }
    )
    existing_command = SimpleNamespace(
        id=11,
        trace_id="trace-001",
        task_type="PICK_AND_PUT",
        params={},
        workline_id=1,
        device_id=7,
        status=SimpleNamespace(value="SUCCESS"),
        get_duration_ms=lambda: 100,
    )
    handled_command = MagicMock()
    handled_command.id = 11
    handled_command.device_id = 7
    handled_command.status = SimpleNamespace(value="SUCCESS")
    handled_command.get_duration_ms.return_value = 100
    handled_command.trace_id = "trace-001"

    service = CallbackOrchestrationService(runtime_inbox_writer=WriterStub())
    service._is_workline_command_callback = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]
    service._mark_callback_device_finished = AsyncMock(return_value=0)  # type: ignore[method-assign]
    service._load_command_session = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with patch(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service.record_late_callback_if_pending",
        new=AsyncMock(return_value=False),
    ):
        outcome = await service.process_result(
            SimpleNamespace(),  # type: ignore[arg-type]
            callback=callback,
            existing_command=existing_command,
            request_id="req-result-001",
            resolved_contract_version="1.0",
            command_service=SimpleNamespace(handle_callback_result=AsyncMock(return_value=handled_command)),
            device_service=SimpleNamespace(),
            inbox_service=InboxServiceStub(),  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is False
    assert outcome.trace_id == "trace-001"
    assert call_order[:2] == ["runtime", "legacy"]
    assert writer_kwargs["canonical_result_type"] == "DEVICE_RESULT"


@pytest.mark.asyncio
async def test_process_result_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources() -> None:
    """duplicate ACK 只能来自 RuntimeInbox，不能再触发旧 Workline inbox/processor。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.app.device.models.command import CommandCallbackResult

    writer = SimpleNamespace(write_result_callback=AsyncMock(return_value=_runtime_accept_result(created=False)))
    inbox_service = SimpleNamespace(create_command_result_inbox=AsyncMock())
    command_service = SimpleNamespace(handle_callback_result=AsyncMock())

    callback = CommandCallbackResult.model_validate(
        {
            "command_code": "CMD-DUP-001",
            "device_code": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1_702_627_250_000,
            "data": {"task_type": "PICK_AND_PUT"},
        }
    )
    existing_command = SimpleNamespace(
        id=12,
        trace_id="trace-dup-001",
        task_type="PICK_AND_PUT",
        params={},
        workline_id=1,
        device_id=7,
    )

    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    service._is_workline_command_callback = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_result(
        SimpleNamespace(),  # type: ignore[arg-type]
        callback=callback,
        existing_command=existing_command,
        request_id="req-result-dup",
        resolved_contract_version="1.0",
        command_service=command_service,  # type: ignore[arg-type]
        device_service=SimpleNamespace(),
        inbox_service=inbox_service,  # type: ignore[arg-type]
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is True
    writer.write_result_callback.assert_awaited_once()
    inbox_service.create_command_result_inbox.assert_not_awaited()
    command_service.handle_callback_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_event_writes_runtime_inbox_before_legacy_workline_inbox() -> None:
    """事件回调 ACK 先写 RuntimeInbox，旧 inbox 只能作为过渡消费。"""

    from src.app.callback.models import CallbackEventRequest
    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    call_order: list[str] = []
    writer_kwargs: dict[str, object] = {}

    class WriterStub:
        async def write_event_callback(self, *_args, **kwargs):
            call_order.append("runtime")
            writer_kwargs.update(kwargs)
            return _runtime_accept_result(created=True, record_id=201)

    class InboxServiceStub:
        async def create_device_event_inbox(self, **_kwargs):
            call_order.append("legacy")
            return SimpleNamespace(id=401)

    service = CallbackOrchestrationService(runtime_inbox_writer=WriterStub())
    service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_event(
        SimpleNamespace(),  # type: ignore[arg-type]
        event_request=CallbackEventRequest.model_validate(
            {
                "device_code": "ARM_01",
                "event_type": "SCAN_COMPLETED",
                "timestamp": 1_702_627_300_000,
                "data": {"LotCode": "LOT-001"},
            }
        ),
        request_id="req-event-001",
        is_workline_event=True,
        canonical_event_type="SCAN_COMPLETED",
        inbox_service=InboxServiceStub(),  # type: ignore[arg-type]
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is False
    assert call_order[:2] == ["runtime", "legacy"]
    assert writer_kwargs["canonical_event_type"] == "SCAN_COMPLETED"


@pytest.mark.asyncio
async def test_process_external_writes_runtime_inbox_before_legacy_transition_delegate() -> None:
    """external callback 必须先创建 RuntimeInbox，再过渡委托 legacy 消费链路。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    call_order: list[str] = []
    writer_kwargs: dict[str, object] = {}

    class WriterStub:
        async def write_external_callback(self, *_args, **kwargs):
            call_order.append("runtime")
            writer_kwargs.update(kwargs)
            return _runtime_accept_result(created=True, record_id=301)

    class InboxServiceStub:
        async def create_external_http_inbox(self, **kwargs):
            call_order.append("legacy")
            return SimpleNamespace(id=501, trace_id=kwargs["trace_id"])

        async def mark_as_processed(self, *_args, **_kwargs):
            call_order.append("legacy-processed")
            return SimpleNamespace(id=501)

    rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
    service = CallbackOrchestrationService(
        rack_task_service=rack_task_service,
        handling_operation_service=SimpleNamespace(record_callback_from_external_http=AsyncMock()),
        runtime_inbox_writer=WriterStub(),
    )
    service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_external(
        SimpleNamespace(),  # type: ignore[arg-type]
        callback_type="AGV_TASK_RESULT",
        payload={
            "callback_type": "AGV_TASK_RESULT",
            "trace_id": "trace-ext-001",
            "request_id": "REQ-EXT-001",
            "command_code": "AGV-REQ-001",
            "result": "SUCCESS",
            "data": {"to_location": "STATION-01"},
        },
        request_id="req-external-001",
        inbox_service=InboxServiceStub(),  # type: ignore[arg-type]
        trace_id="trace-ext-001",
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is False
    assert outcome.trace_id == "trace-ext-001"
    assert call_order[:2] == ["runtime", "legacy"]
    assert writer_kwargs["payload"]["callback_type"] == "AGV_TASK_RESULT"


@pytest.mark.asyncio
async def test_process_result_keeps_runtime_ack_when_legacy_inbox_reports_duplicate() -> None:
    """RuntimeInbox 已 created 时，legacy duplicate 不能反向把 ACK 改成 duplicate。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.app.device.models.command import CommandCallbackResult

    callback = CommandCallbackResult.model_validate(
        {
            "command_code": "CMD-LEGACY-DUP-001",
            "device_code": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1_702_627_250_000,
            "data": {"task_type": "PICK_AND_PUT"},
        }
    )
    existing_command = SimpleNamespace(
        id=21,
        trace_id="trace-legacy-result",
        task_type="PICK_AND_PUT",
        params={},
        workline_id=1,
        device_id=7,
    )

    writer = SimpleNamespace(write_result_callback=AsyncMock(return_value=_runtime_accept_result(created=True)))
    inbox_service = SimpleNamespace(
        create_command_result_inbox=AsyncMock(
            side_effect=_LegacyDuplicateError("已存在（幂等键重复）", existing_inbox=SimpleNamespace(id=611))
        )
    )
    command_service = SimpleNamespace(handle_callback_result=AsyncMock())
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    service._is_workline_command_callback = AsyncMock(return_value=True)  # type: ignore[method-assign]

    db = SimpleNamespace(commit=AsyncMock())
    with (
        patch(
            "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service.record_late_callback_if_pending",
            new=AsyncMock(return_value=False),
        ),
    ):
        outcome = await service.process_result(
            db,  # type: ignore[arg-type]
            callback=callback,
            existing_command=existing_command,
            request_id="req-legacy-result",
            resolved_contract_version="1.0",
            command_service=command_service,  # type: ignore[arg-type]
            device_service=SimpleNamespace(),
            inbox_service=inbox_service,  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is False
    command_service.handle_callback_result.assert_not_awaited()
    inbox_service.create_command_result_inbox.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_event_keeps_runtime_ack_when_legacy_inbox_reports_duplicate() -> None:
    """事件回调 RuntimeInbox 已 created 时，legacy duplicate 只能作为过渡消费副作用。"""

    from src.app.callback.models import CallbackEventRequest
    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    writer = SimpleNamespace(write_event_callback=AsyncMock(return_value=_runtime_accept_result(created=True)))
    inbox_service = SimpleNamespace(
        create_device_event_inbox=AsyncMock(
            side_effect=_LegacyDuplicateError("已存在（幂等键重复）", existing_inbox=SimpleNamespace(id=612))
        )
    )
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)

    db = SimpleNamespace(commit=AsyncMock())
    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        outcome = await service.process_event(
            db,  # type: ignore[arg-type]
            event_request=CallbackEventRequest.model_validate(
                {
                    "device_code": "ARM_01",
                    "event_type": "SCAN_COMPLETED",
                    "timestamp": 1_702_627_300_000,
                    "data": {"LotCode": "LOT-001"},
                }
            ),
            request_id="req-legacy-event",
            is_workline_event=True,
            canonical_event_type="SCAN_COMPLETED",
            inbox_service=inbox_service,  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is False
    inbox_service.create_device_event_inbox.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_external_keeps_runtime_ack_when_legacy_inbox_reports_duplicate() -> None:
    """external callback RuntimeInbox 已 created 时，legacy duplicate 不得改写 duplicate ACK。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    writer = SimpleNamespace(write_external_callback=AsyncMock(return_value=_runtime_accept_result(created=True)))
    inbox_service = SimpleNamespace(
        create_external_http_inbox=AsyncMock(
            side_effect=_LegacyDuplicateError("已存在（幂等键重复）", existing_inbox=SimpleNamespace(id=613))
        )
    )
    service = CallbackOrchestrationService(
        rack_task_service=SimpleNamespace(record_callback_from_external_http=AsyncMock()),
        handling_operation_service=SimpleNamespace(record_callback_from_external_http=AsyncMock()),
        runtime_inbox_writer=writer,
    )

    db = SimpleNamespace(commit=AsyncMock())
    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        outcome = await service.process_external(
            db,  # type: ignore[arg-type]
            callback_type="AGV_TASK_RESULT",
            payload={
                "callback_type": "AGV_TASK_RESULT",
                "trace_id": "trace-ext-dup",
                "request_id": "REQ-EXT-DUP-001",
                "command_code": "AGV-REQ-DUP-001",
                "result": "SUCCESS",
                "data": {"to_location": "STATION-01"},
            },
            request_id="req-legacy-external",
            inbox_service=inbox_service,  # type: ignore[arg-type]
            trace_id="trace-ext-dup",
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is False
    inbox_service.create_external_http_inbox.assert_awaited_once()
    db.commit.assert_awaited_once()
