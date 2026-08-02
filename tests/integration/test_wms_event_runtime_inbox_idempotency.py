"""普通 WMS event 的跨类型稳定源事件幂等合同。"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxConflict,
    RuntimeInboxCorrelationUnavailable,
    RuntimeInboxService,
)


def _event(event_type: str = "WMS_INVENTORY_UPDATED") -> dict[str, object]:
    data: dict[str, object]
    if event_type == "WMS_INVENTORY_UPDATED":
        data = {"inventory_reference": "inventory-change-001", "material_code": "MAT-001"}
    else:
        data = {"pallet_id": "PALLET-001", "arrived_station": "STATION-A"}
    return {
        "source_system": "WMS",
        "event_type": event_type,
        "source_event_id": "wms-business-event-001",
        "source_version": "1",
        "occurred_at": "2026-07-30T08:00:00Z",
        "request_id": "request-business-event-001",
        "data": data,
    }


@pytest.mark.asyncio
async def test_same_wms_source_event_replay_returns_existing_record(db_session) -> None:
    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxService())
    payload = _event()

    first = await writer.write_wms_event_callback(
        db_session,
        payload=payload,
        request_id="request-1",
        event_type="WMS_INVENTORY_UPDATED",
    )
    second = await writer.write_wms_event_callback(
        db_session,
        payload=payload,
        request_id="request-2",
        event_type="WMS_INVENTORY_UPDATED",
    )

    assert first.created is True
    assert second.created is False
    assert second.record.id == first.record.id


@pytest.mark.asyncio
async def test_same_wms_source_event_with_changed_payload_conflicts(db_session) -> None:
    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxService())
    payload = _event()
    await writer.write_wms_event_callback(
        db_session,
        payload=payload,
        request_id="request-1",
        event_type="WMS_INVENTORY_UPDATED",
    )
    changed = deepcopy(payload)
    changed["data"]["material_code"] = "MAT-CHANGED"

    with pytest.raises(RuntimeInboxConflict):
        await writer.write_wms_event_callback(
            db_session,
            payload=changed,
            request_id="request-2",
            event_type="WMS_INVENTORY_UPDATED",
        )


@pytest.mark.asyncio
async def test_same_wms_source_event_cannot_be_reused_across_event_types(db_session) -> None:
    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxService())
    await writer.write_wms_event_callback(
        db_session,
        payload=_event(),
        request_id="request-1",
        event_type="WMS_INVENTORY_UPDATED",
    )

    with pytest.raises(RuntimeInboxConflict):
        await writer.write_wms_event_callback(
            db_session,
            payload=_event("WMS_PALLET_ARRIVED"),
            request_id="request-2",
            event_type="WMS_PALLET_ARRIVED",
        )


@pytest.mark.asyncio
async def test_wms_event_persists_existing_explicit_correlation(db_session) -> None:
    correlation = ExecutionCorrelation(correlation_id="corr-wms-event-001", trace_id="trace-wms-event-001")
    db_session.add(correlation)
    await db_session.flush()
    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxService())

    result = await writer.write_wms_event_callback(
        db_session,
        payload=_event(),
        request_id="request-1",
        event_type="WMS_INVENTORY_UPDATED",
        correlation_id="corr-wms-event-001",
    )

    assert result.record.correlation_id == "corr-wms-event-001"


@pytest.mark.asyncio
async def test_wms_event_rejects_unknown_explicit_correlation(db_session) -> None:
    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxService())

    with pytest.raises(RuntimeInboxCorrelationUnavailable):
        await writer.write_wms_event_callback(
            db_session,
            payload=_event(),
            request_id="request-1",
            event_type="WMS_INVENTORY_UPDATED",
            correlation_id="corr-wms-event-missing",
        )


@pytest.mark.asyncio
async def test_wms_event_allows_missing_correlation(db_session) -> None:
    writer = CallbackRuntimeInboxWriter(service=RuntimeInboxService())

    result = await writer.write_wms_event_callback(
        db_session,
        payload=_event(),
        request_id="request-1",
        event_type="WMS_INVENTORY_UPDATED",
        correlation_id=None,
    )

    assert result.record.correlation_id is None


@pytest.mark.asyncio
async def test_wms_event_ack_survives_immediate_enqueue_failure() -> None:
    class _Db:
        commit_count = 0

        async def commit(self) -> None:
            self.commit_count += 1

    writer = type("_Writer", (), {})()
    writer.write_wms_event_callback = AsyncMock(return_value=await _accepted_result())
    service = CallbackOrchestrationService(runtime_inbox_writer=writer)

    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        outcome = await service.process_wms_event(
            _Db(),
            payload=_event(),
            event_type="WMS_INVENTORY_UPDATED",
            request_id="request-1",
            trace_id="trace-1",
            event_id="wms-business-event-001",
            causation_id=None,
            correlation_id="corr-wms-event-001",
            enqueue_processing=lambda: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
        )

    assert outcome.is_duplicate is False
    assert writer.write_wms_event_callback.await_args.kwargs["correlation_id"] == "corr-wms-event-001"


async def _accepted_result():
    return type("_Accepted", (), {"created": True, "record": object()})()
