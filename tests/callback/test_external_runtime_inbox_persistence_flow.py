"""External callback -> RuntimeInbox persistence/claim/processor 真实链路。"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy import select

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.workline.models import LineType, WorkLine


@pytest.mark.asyncio
async def test_wms_event_persists_claims_and_processes_without_device_flow(db_session) -> None:
    """真实 RuntimeInbox 行必须被 claim/processor 消费，且无领域 lifecycle 直写。"""
    workline = WorkLine(
        line_code="LINE-RUNTIME-INBOX-EXT",
        line_name="Runtime Inbox External Integration",
        line_type=LineType.AUTO,
        is_active=True,
    )
    db_session.add(workline)
    await db_session.flush()
    session = WorklineSession(
        session_code="SESSION-RUNTIME-INBOX-EXT",
        workline_id=workline.id,
        status=SessionStatus.RUNNING,
        trace_id="trace-runtime-inbox-ext",
    )
    db_session.add(session)
    await db_session.commit()

    inbox_service = RuntimeInboxService()
    orchestration = CallbackOrchestrationService(
        runtime_inbox_writer=CallbackRuntimeInboxWriter(service=inbox_service),
    )
    payload = {
        "source_system": "WMS",
        "event_type": "WMS_INVENTORY_UPDATED",
        "source_event_id": "wms-runtime-inbox-ext-001",
        "source_version": "1",
        "occurred_at": "2026-07-30T08:00:00Z",
        "request_id": "req-runtime-inbox-ext",
        "data": {"inventory_reference": "inventory-runtime-001"},
    }
    enqueue_processing = Mock()

    outcome = await orchestration.process_wms_event(
        db_session,
        payload=payload,
        event_type="WMS_INVENTORY_UPDATED",
        request_id="req-runtime-inbox-ext",
        trace_id="trace-runtime-inbox-ext",
        event_id="wms-runtime-inbox-ext-001",
        causation_id=None,
        enqueue_processing=enqueue_processing,
    )
    duplicate = await orchestration.process_wms_event(
        db_session,
        payload=payload,
        event_type="WMS_INVENTORY_UPDATED",
        request_id="req-runtime-inbox-ext-duplicate",
        trace_id="trace-runtime-inbox-ext",
        event_id="wms-runtime-inbox-ext-001",
        causation_id=None,
        enqueue_processing=enqueue_processing,
    )

    assert outcome.is_duplicate is False
    assert duplicate.is_duplicate is True
    enqueue_processing.assert_called_once_with()

    claims = await inbox_service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="runtime-inbox-integration",
        stale_after_seconds=60,
    )
    assert len(claims) == 1
    assert claims[0]["kind"] == "EXTERNAL_HTTP"
    assert claims[0]["payload_json"] == payload

    result = await RuntimeInboxProcessorBridge(inbox_service=inbox_service).process_claimed(db_session, claim=claims[0])

    assert result["processed"] == 1
    assert result["success"] == 1
    assert result["failed"] == 0
    persisted = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.id == claims[0]["id"]))
    assert persisted is not None
    assert {"plugin_key", "contract_version"}.isdisjoint(WorklineSession.model_fields)
    assert not hasattr(persisted, "session_id")
    assert persisted.status == "PROCESSED"
    assert persisted.last_error_message is None
