"""WMS inbound 的 RuntimeInbox 窄处理分支。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.inbox.wms_runtime_inbox_handler import WmsRuntimeInboxHandler
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)


def _grn_event() -> dict[str, object]:
    return {
        "source_system": "WMS",
        "event_type": "WMS_GRN_RECEIVED",
        "source_event_id": "grn-runtime-001",
        "source_version": "1",
        "occurred_at": "2026-07-30T08:00:00Z",
        "request_id": "req-runtime-001",
        "data": {
            "grn_id": "GRN-001",
            "po_number": "PO-001",
            "po_item": "10",
            "material_code": "MAT-001",
            "received_quantity": 5,
            "warehouse_code": "WH-A",
        },
    }


def _hint() -> dict[str, object]:
    return {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "hint-runtime-001",
        "occurred_at": "2026-07-30T08:00:00Z",
        "trace_id": "trace-runtime-001",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-runtime-001",
            "dispatch_key": "dispatch-runtime-001",
        },
    }


@pytest.mark.asyncio
async def test_canonical_ordinary_wms_event_is_processed_without_device_flow(db_session) -> None:
    service = RuntimeInboxService()
    accepted = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_GRN_RECEIVED",
        source_event_id="grn-runtime-001",
        payload_hash="hash-grn-runtime-001",
        kind="EXTERNAL_HTTP",
        payload_json=_grn_event(),
        payload_schema_version=1,
    )
    await db_session.commit()
    [claim] = await service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="wms-event-worker",
        stale_after_seconds=60,
    )

    result = await RuntimeInboxProcessorBridge(inbox_service=service).process_claimed(db_session, claim=claim)

    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    await db_session.refresh(accepted.record)
    assert accepted.record.status == "PROCESSED"


@pytest.mark.asyncio
async def test_hint_is_routed_only_by_runtime_inbox_worker(db_session) -> None:
    service = RuntimeInboxService()
    router = AsyncMock()
    router.route.return_value = True
    accepted = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_EFFECT_STATUS_HINT",
        source_event_id="hint-runtime-001",
        payload_hash="hash-hint-runtime-001",
        kind="EXTERNAL_HTTP",
        payload_json=_hint(),
        payload_schema_version=1,
    )
    await db_session.commit()
    [claim] = await service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="wms-hint-worker",
        stale_after_seconds=60,
    )

    result = await RuntimeInboxProcessorBridge(
        inbox_service=service,
        wms_inbound_handler=WmsRuntimeInboxHandler(callback_router=router),
    ).process_claimed(db_session, claim=claim)

    assert result["success"] == 1
    router.route.assert_awaited_once_with(
        db_session,
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_hint(),
    )
    await db_session.refresh(accepted.record)
    assert accepted.record.status == "PROCESSED"


@pytest.mark.asyncio
async def test_unknown_hint_handler_failure_retries_then_dead_letters(db_session) -> None:
    service = RuntimeInboxService()
    router = AsyncMock()
    router.route.side_effect = RuntimeError("provider unavailable")
    accepted = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_EFFECT_STATUS_HINT",
        source_event_id="hint-runtime-retry",
        payload_hash="hash-hint-runtime-retry",
        kind="EXTERNAL_HTTP",
        payload_json={**_hint(), "source_event_id": "hint-runtime-retry"},
        payload_schema_version=1,
        max_retries=2,
    )
    await db_session.commit()
    bridge = RuntimeInboxProcessorBridge(
        inbox_service=service,
        wms_inbound_handler=WmsRuntimeInboxHandler(callback_router=router),
    )

    for attempt in range(2):
        [claim] = await service.claim_for_processing(
            db_session,
            limit=1,
            processor_token=f"wms-hint-retry-{attempt}",
            stale_after_seconds=60,
        )
        await db_session.commit()
        result = await bridge.process_claimed(db_session, claim=claim)
        assert result["failed"] == 1
        await db_session.refresh(accepted.record)
        if attempt == 0:
            assert accepted.record.status == "FAILED"
            assert accepted.record.last_error_code == "UNKNOWN"
            await db_session.execute(
                update(RuntimeInbox).where(RuntimeInbox.id == accepted.record.id).values(next_retry_at=0)
            )
            await db_session.commit()

    await db_session.refresh(accepted.record)
    assert accepted.record.status == "DEAD_LETTER"
    assert router.route.await_count == 2
