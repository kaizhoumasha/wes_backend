"""Callback 入站 ACK 以 RuntimeInbox 为唯一权威的服务级回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.parametrize("field_name", ("trace_id", "event_id", "causation_id"))
def test_callback_ingress_rejects_trace_identifiers_exceeding_runtime_inbox_limit(field_name: str) -> None:
    """入口必须在落库前拒绝超过 RuntimeInbox VARCHAR(120) 的追踪标识。"""

    from src.app.callback.services.callback_ingress_service import (
        _RESULT_CALLBACK_TOP_LEVEL_FIELDS,
        _validate_top_level_fields,
    )

    with pytest.raises(ValueError, match="最大长度 120"):
        _validate_top_level_fields(
            {field_name: "x" * 121},
            _RESULT_CALLBACK_TOP_LEVEL_FIELDS,
            "result",
        )


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
async def test_callback_runtime_inbox_writer_delegates_result_to_typed_command_entry() -> None:
    """Result writer 必须走 command-result 入口，禁止绕过权威 correlation/session 解析。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_kwargs: dict[str, object] = {}

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            raise AssertionError(f"result callback 不得直调 accept_received: {kwargs}")

        async def accept_command_result(self, _db, **kwargs):
            accepted_kwargs.update(kwargs)
            return _runtime_accept_result(created=True)

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    payload = {
        "source_event_id": "evt-result-typed-001",
        "command_code": "CMD-TYPED-001",
        "device_code": "ARM-01",
        "result": "SUCCESS",
    }

    await writer.write_result_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="req-result-typed-001",
        canonical_result_type="DEVICE_RESULT",
        trace_id="trace-result-typed-001",
        event_id="event-result-typed-001",
        causation_id="cause-result-typed-001",
        workline_id=11,
        device_id=12,
        command_id=13,
    )

    assert accepted_kwargs == {
        "command_code": "CMD-TYPED-001",
        "source_event_id": "evt-result-typed-001",
        "source_provider_code": "ECS",
        "source_event_type": "DEVICE_RESULT",
        "device_code": "ARM-01",
        "workline_id": 11,
        "device_id": 12,
        "command_id": 13,
        "correlation_id": None,
        "trace_id": "trace-result-typed-001",
        "event_id": "event-result-typed-001",
        "causation_id": "cause-result-typed-001",
        "payload_json": payload,
        "processing_required": True,
    }


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_preserves_pre_upgrade_result_identity() -> None:
    """跨部署重试必须继续使用升级前的 ECS + canonical result identity。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    legacy_identity = ("ECS", "DEVICE_RESULT", "evt-result-upgrade-001")
    accepted_identity: tuple[object, object, object] | None = None

    class RuntimeInboxServiceStub:
        async def accept_command_result(self, _db, **kwargs):
            nonlocal accepted_identity
            accepted_identity = (
                kwargs.get("source_provider_code", "DEVICE_RESULT"),
                kwargs.get("source_event_type", "COMMAND_RESULT"),
                kwargs["source_event_id"],
            )
            return _runtime_accept_result(created=accepted_identity != legacy_identity, record_id=41)

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    result = await writer.write_result_callback(
        SimpleNamespace(),
        payload={
            "source_event_id": "evt-result-upgrade-001",
            "command_code": "CMD-UPGRADE-001",
            "device_code": "ARM-01",
            "result": "SUCCESS",
        },
        request_id="req-result-upgrade-001",
        canonical_result_type="DEVICE_RESULT",
    )

    assert accepted_identity == legacy_identity
    assert result.created is False


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_uses_canonical_types_without_channel_collapse() -> None:
    """writer 必须把 canonical callback/event/result type 传给 RuntimeInbox。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_command_result(self, _db, **kwargs):
            accepted_calls.append(
                {
                    **kwargs,
                    "provider_code": kwargs["source_provider_code"],
                    "event_type": kwargs["source_event_type"],
                    "correlation_id": None,
                }
            )
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]

    _ = await writer.write_result_callback(
        SimpleNamespace(),
        payload={"source_event_id": "evt-shared-001", "command_code": "CMD-001", "result": "SUCCESS"},
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
async def test_callback_runtime_inbox_writer_does_not_synthesize_unverified_correlation_ids() -> None:
    """writer 不能为 event/external 合成未持久化的 correlation_id。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_command_result(self, _db, **kwargs):
            accepted_calls.append({**kwargs, "correlation_id": None})
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]

    _ = await writer.write_result_callback(
        SimpleNamespace(),
        payload={"source_event_id": "evt-result-001", "command_code": "CMD-001", "result": "SUCCESS"},
        request_id="req-result-001",
        canonical_result_type="DEVICE_RESULT",
        correlation_id=None,
    )
    _ = await writer.write_event_callback(
        SimpleNamespace(),
        payload={"event_id": "evt-event-001", "device_code": "ARM_01", "event_type": "SCAN_COMPLETED"},
        request_id="req-event-001",
        canonical_event_type="SCAN_COMPLETED",
    )
    _ = await writer.write_external_callback(
        SimpleNamespace(),
        payload={
            "callback_type": "AGV_TASK_RESULT",
            "source_system": "AGV",
            "request_id": "REQ-EXT-001",
            "dispatch_key": "external:agv:001",
            "trace_id": "trace-ext-001",
        },
        request_id="req-external-001",
    )

    assert [call["correlation_id"] for call in accepted_calls] == [None, None, None]


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_external_uses_data_source_event_id_before_request_id() -> None:
    """external writer 必须优先使用 payload.data.source_event_id，避免 request_id 塌缩幂等键。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]

    payload = {
        "callback_type": "WMS_RACK_TASK_RESULT",
        "source_system": "WMS",
        "request_id": "REQ-EXT-001",
        "data": {"source_event_id": "biz-source-evt-001"},
    }
    _ = await writer.write_external_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="req-external-001",
    )
    _ = await writer.write_external_callback(
        SimpleNamespace(),
        payload={**payload, "request_id": "REQ-EXT-002"},
        request_id="req-external-002",
    )

    assert [call["source_event_id"] for call in accepted_calls] == [
        "biz-source-evt-001",
        "biz-source-evt-001",
    ]


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_external_fallback_source_event_id_is_payload_stable() -> None:
    """external 缺少业务 source id 时，不能退回到每次 HTTP request_id。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    payload = {
        "callback_type": "AGV_TASK_RESULT",
        "source_system": "AGV",
        "trace_id": "trace-agv-001",
        "command_code": "AGV-CMD-001",
        "result": "SUCCESS",
        "data": {"to_location": "STATION-01"},
    }

    _ = await writer.write_external_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="http-ext-001",
    )
    _ = await writer.write_external_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="http-ext-002",
    )

    source_event_ids = [call["source_event_id"] for call in accepted_calls]
    assert source_event_ids[0] == source_event_ids[1]
    assert source_event_ids[0] not in {"http-ext-001", "http-ext-002"}


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_external_record_is_canonical_processing_evidence() -> None:
    """新 external RuntimeInbox record 必须携带 processor 所需 payload 与 trace 证据。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_kwargs: dict[str, object] = {}
    record = SimpleNamespace(id=301)

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            accepted_kwargs.update(kwargs)
            for field_name in (
                "kind",
                "payload_json",
                "payload_schema_version",
                "trace_id",
                "event_id",
                "causation_id",
            ):
                setattr(record, field_name, kwargs[field_name])
            return SimpleNamespace(created=True, record=record)

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    payload = {
        "callback_type": "WMS_RACK_TASK_RESULT",
        "source_event_id": "wms-event-001",
        "dispatch_key": "rack:001",
    }

    result = await writer.write_external_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="req-external-evidence",
        trace_id="trace-external-evidence",
        event_id="event-external-evidence",
        causation_id="cause-external-evidence",
    )

    assert result.record is record
    assert accepted_kwargs["kind"] == "EXTERNAL_HTTP"
    assert record.kind == "EXTERNAL_HTTP"
    assert record.payload_json == payload
    assert record.payload_json is not payload
    assert record.payload_schema_version == 1
    assert record.trace_id == "trace-external-evidence"
    assert record.event_id == "event-external-evidence"
    assert record.causation_id == "cause-external-evidence"


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_result_requires_explicit_source_event_id() -> None:
    """result 缺少 source_event_id 时必须拒绝，禁止合成不可验证的事实身份。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            raise AssertionError(f"缺少 source_event_id 时不得写 RuntimeInbox: {kwargs}")

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    payload = {
        "command_code": "CMD-STABLE-001",
        "device_code": "ARM_01",
        "result": "SUCCESS",
        "finish_time": 1_702_627_250_000,
        "data": {"pkg_id": "PKG-001"},
    }

    with pytest.raises(ValueError, match="source_event_id"):
        await writer.write_result_callback(
            SimpleNamespace(),
            payload=payload,
            request_id="http-req-001",
            canonical_result_type="DEVICE_RESULT",
        )


def test_command_callback_result_requires_source_event_id() -> None:
    """未发布的新 result 契约必须直接要求 provider 事件身份，不保留 legacy fallback。"""

    from pydantic import ValidationError

    from src.app.device.models.command import CommandCallbackResult

    with pytest.raises(ValidationError, match="source_event_id"):
        CommandCallbackResult.model_validate(
            {
                "command_code": "CMD-SOURCE-REQUIRED-001",
                "device_code": "ARM_01",
                "result": "SUCCESS",
                "finish_time": 1_702_627_250_000,
            }
        )


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_event_fallback_source_event_id_is_payload_stable() -> None:
    """event 缺少 event_id/source_event_id 时，重复上报必须命中同一个 RuntimeInbox key。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=len(accepted_calls))

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    payload = {
        "device_code": "ARM_01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_702_627_300_000,
        "data": {"LotCode": "LOT-001"},
    }

    _ = await writer.write_event_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="http-req-101",
        canonical_event_type="SCAN_COMPLETED",
    )
    _ = await writer.write_event_callback(
        SimpleNamespace(),
        payload=payload,
        request_id="http-req-102",
        canonical_event_type="SCAN_COMPLETED",
    )

    source_event_ids = [call["source_event_id"] for call in accepted_calls]
    assert source_event_ids[0] == source_event_ids[1]
    assert source_event_ids[0] not in {"http-req-101", "http-req-102"}


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_passes_stable_device_owner() -> None:
    """同设备不同事件必须落入同一 device FIFO bucket。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    accepted_calls: list[dict[str, object]] = []

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **kwargs):
            accepted_calls.append(kwargs)
            return _runtime_accept_result(created=True, record_id=1)

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]
    await writer.write_event_callback(
        SimpleNamespace(),
        payload={"event_id": "evt-001", "device_code": "ARM_01", "event_type": "SCAN_COMPLETED"},
        request_id="req-001",
        canonical_event_type="SCAN_COMPLETED",
        device_id=42,
    )

    assert accepted_calls[0]["device_id"] == 42


@pytest.mark.asyncio
async def test_callback_runtime_inbox_writer_rejects_blank_result_source_event_id() -> None:
    """空白 provider identity 属于回调契约错误，不能进入 RuntimeInbox。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter

    class RuntimeInboxServiceStub:
        async def accept_received(self, _db, **_kwargs):
            raise AssertionError("空白 source_event_id 不应进入 RuntimeInbox")

    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxServiceStub())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="source_event_id"):
        await writer.write_result_callback(
            SimpleNamespace(),
            payload={"source_event_id": "   ", "command_code": "CMD-001", "result": "SUCCESS"},
            request_id="req-001",
            canonical_result_type="DEVICE_RESULT",
        )


@pytest.mark.asyncio
async def test_process_result_uses_runtime_inbox_as_authority() -> None:
    """结果回调 accepted 后继续业务处理，并保持 RuntimeInbox 唯一权威。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.app.device.models.command import CommandCallbackResult

    call_order: list[str] = []
    writer_kwargs: dict[str, object] = {}

    class WriterStub:
        async def write_result_callback(self, *_args, **kwargs):
            call_order.append("runtime")
            writer_kwargs.update(kwargs)
            return _runtime_accept_result(created=True, record_id=101)

    callback = CommandCallbackResult.model_validate(
        {
            "command_code": "CMD-ACK-001",
            "device_code": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1_702_627_250_000,
            "source_event_id": "result-event-ack-001",
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

    command_service = SimpleNamespace(handle_callback_result=AsyncMock(return_value=handled_command))
    service = CallbackOrchestrationService(runtime_inbox_writer=WriterStub())
    service._is_workline_command_callback = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]
    service._mark_callback_device_finished = AsyncMock(return_value=0)  # type: ignore[method-assign]
    service._load_command_session = AsyncMock(return_value=SimpleNamespace(id=31))  # type: ignore[method-assign]
    service._append_command_acked_timeline = AsyncMock()  # type: ignore[method-assign]

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
            command_service=command_service,
            device_service=SimpleNamespace(),
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is False
    assert outcome.trace_id == "trace-001"
    assert call_order == ["runtime"]
    assert writer_kwargs["canonical_result_type"] == "DEVICE_RESULT"
    assert writer_kwargs["trace_id"] == "trace-001"
    assert writer_kwargs["event_id"] is None
    assert writer_kwargs["causation_id"] is None
    assert writer_kwargs["processing_required"] is True
    command_service.handle_callback_result.assert_awaited_once()
    service._commit_and_enqueue_runtime_inbox_processing.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_result_terminalizes_non_workline_command_without_runtime_processor() -> None:
    """非工作线指令结果同步完成后应直接终态化，不再进入 RuntimeInbox processor。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.app.device.models.command import CommandCallbackResult

    writer = SimpleNamespace(write_result_callback=AsyncMock(return_value=_runtime_accept_result(created=True)))
    callback = CommandCallbackResult.model_validate(
        {
            "command_code": "CMD-NON-WORKLINE-001",
            "device_code": "STANDALONE-DEVICE-01",
            "result": "SUCCESS",
            "finish_time": 1_702_627_250_000,
            "source_event_id": "result-event-non-workline-001",
        }
    )
    existing_command = SimpleNamespace(
        id=13,
        trace_id="trace-non-workline-001",
        task_type="DEVICE_SELF_TEST",
        params={},
        workline_id=None,
        device_id=9,
    )
    handled_command = MagicMock()
    handled_command.id = 13
    handled_command.device_id = 9
    handled_command.status = SimpleNamespace(value="SUCCESS")
    handled_command.get_duration_ms.return_value = 50
    handled_command.trace_id = "trace-non-workline-001"
    command_service = SimpleNamespace(handle_callback_result=AsyncMock(return_value=handled_command))
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    service._is_workline_command_callback = AsyncMock(return_value=False)  # type: ignore[method-assign]
    service._mark_callback_device_finished = AsyncMock(return_value=0)  # type: ignore[method-assign]
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]
    db = SimpleNamespace(commit=AsyncMock())

    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        outcome = await service.process_result(
            db,  # type: ignore[arg-type]
            callback=callback,
            existing_command=existing_command,
            request_id="req-result-non-workline",
            resolved_contract_version="1.0",
            command_service=command_service,  # type: ignore[arg-type]
            device_service=SimpleNamespace(),
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is False
    assert writer.write_result_callback.await_args.kwargs["processing_required"] is False
    command_service.handle_callback_result.assert_awaited_once()
    db.commit.assert_awaited_once()
    service._commit_and_enqueue_runtime_inbox_processing.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_result_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources() -> None:
    """duplicate ACK 只能来自 RuntimeInbox，且不得触发第二套 processor。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.app.device.models.command import CommandCallbackResult

    writer = SimpleNamespace(write_result_callback=AsyncMock(return_value=_runtime_accept_result(created=False)))
    command_service = SimpleNamespace(handle_callback_result=AsyncMock())

    callback = CommandCallbackResult.model_validate(
        {
            "command_code": "CMD-DUP-001",
            "device_code": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1_702_627_250_000,
            "source_event_id": "result-event-dup-001",
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
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_result(
        SimpleNamespace(),  # type: ignore[arg-type]
        callback=callback,
        existing_command=existing_command,
        request_id="req-result-dup",
        resolved_contract_version="1.0",
        command_service=command_service,  # type: ignore[arg-type]
        device_service=SimpleNamespace(),
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is True
    writer.write_result_callback.assert_awaited_once()
    command_service.handle_callback_result.assert_not_awaited()
    service._commit_and_enqueue_runtime_inbox_processing.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_event_uses_runtime_inbox_as_authority() -> None:
    """事件回调 accepted 后仅写 RuntimeInbox。"""

    from src.app.callback.models import CallbackEventRequest
    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    call_order: list[str] = []
    writer_kwargs: dict[str, object] = {}

    class WriterStub:
        async def write_event_callback(self, *_args, **kwargs):
            call_order.append("runtime")
            writer_kwargs.update(kwargs)
            return _runtime_accept_result(created=True, record_id=201)

    service = CallbackOrchestrationService(runtime_inbox_writer=WriterStub())

    outcome = await service.process_event(
        AsyncMock(),  # type: ignore[arg-type]
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
        enqueue_processing=lambda: None,
    )

    # Plan Task 4: 双写已删, RuntimeInbox 唯一事实源.
    assert outcome.is_duplicate is False
    assert call_order == ["runtime"]
    assert writer_kwargs["canonical_event_type"] == "SCAN_COMPLETED"
    assert writer_kwargs["trace_id"] == outcome.trace_id
    assert writer_kwargs["event_id"] is None
    assert writer_kwargs["causation_id"] is None


@pytest.mark.asyncio
async def test_process_external_uses_runtime_inbox_as_authority() -> None:
    """external callback accepted 后仅以 RuntimeInbox record 作为证据并记录 lifecycle。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    call_order: list[str] = []
    writer_kwargs: dict[str, object] = {}

    class WriterStub:
        async def write_external_callback(self, *_args, **kwargs):
            call_order.append("runtime")
            writer_kwargs.update(kwargs)
            return _runtime_accept_result(created=True, record_id=301)

    rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
    service = CallbackOrchestrationService(
        rack_task_service=rack_task_service,
        handling_operation_service=SimpleNamespace(record_callback_from_external_http=AsyncMock()),
        runtime_inbox_writer=WriterStub(),
    )
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]

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
        trace_id="trace-ext-001",
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is False
    assert outcome.trace_id == "trace-ext-001"
    assert call_order == ["runtime"]
    assert writer_kwargs["payload"]["callback_type"] == "AGV_TASK_RESULT"
    assert writer_kwargs["trace_id"] == "trace-ext-001"
    rack_task_service.record_callback_from_external_http.assert_awaited_once()
    service._commit_and_enqueue_runtime_inbox_processing.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_event_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources() -> None:
    """事件 duplicate ACK 只能来自 RuntimeInbox，不能触发 processor。"""

    from src.app.callback.models import CallbackEventRequest
    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    writer = SimpleNamespace(write_event_callback=AsyncMock(return_value=_runtime_accept_result(created=False)))
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]

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
        request_id="req-event-dup",
        is_workline_event=True,
        canonical_event_type="SCAN_COMPLETED",
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is True
    writer.write_event_callback.assert_awaited_once()
    service._commit_and_enqueue_runtime_inbox_processing.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_external_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources() -> None:
    """external duplicate ACK 只能来自 RuntimeInbox，且不得触发第二套 processor。"""

    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

    writer = SimpleNamespace(write_external_callback=AsyncMock(return_value=_runtime_accept_result(created=False)))
    rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
    handling_operation_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
    service = CallbackOrchestrationService(
        rack_task_service=rack_task_service,
        handling_operation_service=handling_operation_service,
        runtime_inbox_writer=writer,
    )
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_external(
        SimpleNamespace(),  # type: ignore[arg-type]
        callback_type="CTU_BIN_MOVE_COMPLETED",
        payload={
            "callback_type": "CTU_BIN_MOVE_COMPLETED",
            "trace_id": "trace-ext-dup",
            "request_id": "REQ-EXT-DUP-001",
            "command_code": "AGV-REQ-DUP-001",
            "result": "SUCCESS",
            "data": {"to_location": "STATION-01"},
        },
        request_id="req-external-dup",
        trace_id="trace-ext-dup",
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is True
    writer.write_external_callback.assert_awaited_once()
    rack_task_service.record_callback_from_external_http.assert_not_awaited()
    handling_operation_service.record_callback_from_external_http.assert_not_awaited()
    service._commit_and_enqueue_runtime_inbox_processing.assert_not_awaited()  # type: ignore[attr-defined]
