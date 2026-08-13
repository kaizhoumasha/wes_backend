"""Test: callback path 验证 RuntimeInbox 单写 (Plan Task 4 验收)

锁定 external callback 路径只写 RuntimeInbox。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.callback.services.callback_orchestration_service import (
    CallbackOrchestrationService,
)


@pytest.mark.asyncio
async def test_process_external_writes_only_runtime_inbox() -> None:
    """process_external 仅持久化 RuntimeInbox。"""
    runtime_record = SimpleNamespace(
        id=43,
        trace_id="trace-external-001",
        source_event_id="provider-external-001",
    )
    writer = SimpleNamespace(
        write_external_callback=AsyncMock(return_value=SimpleNamespace(created=True, record=runtime_record))
    )
    service = CallbackOrchestrationService(
        runtime_inbox_writer=writer,
    )
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]

    outcome = await service.process_external(
        SimpleNamespace(),  # type: ignore[arg-type]
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload={
            "callback_type": "WMS_EFFECT_STATUS_HINT",
            "source_system": "WMS",
            "source_event_id": "hint-event-001",
            "occurred_at": "2026-07-30T08:00:00Z",
            "data": {
                "operation_identity": "wms.fulfillment.request_rack_supply@v1",
                "idempotency_key": "idem-001",
                "dispatch_key": "dispatch-001",
            },
        },
        request_id="req-external-001",
        trace_id="trace-external-001",
        enqueue_processing=lambda: None,
    )

    assert outcome.trace_id == "trace-external-001"
    writer.write_external_callback.assert_awaited_once()
    service._commit_and_enqueue_runtime_inbox_processing.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_external_defers_wms_effect_hint_to_runtime_inbox() -> None:
    runtime_record = SimpleNamespace(
        id=44,
        trace_id="trace-typed-effect-001",
        source_event_id="wms-provider-event-001",
    )
    writer = SimpleNamespace(
        write_external_callback=AsyncMock(return_value=SimpleNamespace(created=True, record=runtime_record))
    )
    service = CallbackOrchestrationService(
        runtime_inbox_writer=writer,
    )
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "wms-event-001",
        "occurred_at": "2026-07-30T08:00:00Z",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-rack-supply-001",
            "dispatch_key": "rack-supply-001",
        },
    }

    await service.process_external(
        SimpleNamespace(),
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=payload,
        request_id="req-typed-effect-001",
        trace_id="trace-typed-effect-001",
        enqueue_processing=lambda: None,
    )

    service._commit_and_enqueue_runtime_inbox_processing.assert_awaited_once()


@pytest.mark.parametrize(
    "operation_identity",
    (
        "wms.inventory.confirm_inbound@v1",
        "wms.fulfillment.unknown_operation@v1",
    ),
)
@pytest.mark.asyncio
async def test_process_external_rejects_non_async_effect_hint_before_runtime_inbox(
    operation_identity: str,
) -> None:
    writer = SimpleNamespace(
        write_external_callback=AsyncMock(
            return_value=SimpleNamespace(
                created=True,
                record=SimpleNamespace(
                    id=45,
                    trace_id="trace-invalid-effect-001",
                    source_event_id="wms-invalid-effect-001",
                ),
            )
        )
    )
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)
    service._commit_and_enqueue_runtime_inbox_processing = AsyncMock()  # type: ignore[method-assign]
    db = SimpleNamespace(commit=AsyncMock())

    with pytest.raises(ValueError, match="WMS_EFFECT_STATUS_HINT_OPERATION_UNKNOWN"):
        await service.process_external(
            db,  # type: ignore[arg-type]
            callback_type="WMS_EFFECT_STATUS_HINT",
            payload={
                "callback_type": "WMS_EFFECT_STATUS_HINT",
                "source_system": "WMS",
                "source_event_id": "wms-invalid-effect-001",
                "occurred_at": "2026-07-30T08:00:00Z",
                "data": {
                    "operation_identity": operation_identity,
                    "idempotency_key": "idem-invalid-effect-001",
                    "dispatch_key": "invalid-effect-001",
                },
            },
            request_id="req-invalid-effect-001",
            trace_id="trace-invalid-effect-001",
            enqueue_processing=lambda: None,
        )

    writer.write_external_callback.assert_not_awaited()
    service._commit_and_enqueue_runtime_inbox_processing.assert_not_awaited()
    db.commit.assert_not_awaited()
