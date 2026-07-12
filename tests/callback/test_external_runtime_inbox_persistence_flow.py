"""External callback -> RuntimeInbox persistence/claim/processor 真实链路。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorService
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    RuntimeInboxOrchestratorDelegate,
)
from src.app.workline.models import LineType, WorkLine


@asynccontextmanager
async def _noop_lock(_lock_key: str):
    """集成测试只替换基础设施锁，processor/orchestrator 保持生产实现。"""
    yield


def _production_orchestrator_factory(**_kwargs) -> OrchestratorService:
    return OrchestratorService(lock_provider=_noop_lock)


@pytest.mark.asyncio
async def test_external_callback_persists_claims_and_processes_without_repeating_lifecycle(db_session) -> None:
    """真实 RuntimeInbox 行必须被 claim/processor 消费，lifecycle 直接副作用仅一次。"""
    workline = WorkLine(
        line_code="LINE-RUNTIME-INBOX-EXT",
        line_name="Runtime Inbox External Integration",
        line_type=LineType.AUTO,
        plugin_key="default",
        contract_version="1.0",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.flush()
    session = WorklineSession(
        session_code="SESSION-RUNTIME-INBOX-EXT",
        workline_id=workline.id,
        plugin_key="default",
        contract_version="1.0",
        status=SessionStatus.RUNNING,
        trace_id="trace-runtime-inbox-ext",
    )
    db_session.add(session)
    await db_session.commit()

    rack_lifecycle = SimpleNamespace(record_callback_from_external_http=AsyncMock(return_value=None))
    handling_lifecycle = SimpleNamespace(record_callback_from_external_http=AsyncMock(return_value=None))
    inbox_service = RuntimeInboxService()
    orchestration = CallbackOrchestrationService(
        rack_task_service=rack_lifecycle,
        handling_operation_service=handling_lifecycle,
        runtime_inbox_writer=CallbackRuntimeInboxWriter(service=inbox_service),
    )
    payload = {
        "callback_type": "WMS_RACK_TASK_RESULT",
        "source_system": "WMS",
        "source_event_id": "wms-runtime-inbox-ext-001",
        "dispatch_key": "missing-rack-task:runtime-inbox-ext",
        "status": "SUCCEEDED",
    }
    enqueue_processing = Mock()

    outcome = await orchestration.process_external(
        db_session,
        callback_type="WMS_RACK_TASK_RESULT",
        payload=payload,
        request_id="req-runtime-inbox-ext",
        trace_id="trace-runtime-inbox-ext",
        enqueue_processing=enqueue_processing,
    )
    duplicate = await orchestration.process_external(
        db_session,
        callback_type="WMS_RACK_TASK_RESULT",
        payload=payload,
        request_id="req-runtime-inbox-ext-duplicate",
        trace_id="trace-runtime-inbox-ext",
        enqueue_processing=enqueue_processing,
    )

    assert outcome.is_duplicate is False
    assert duplicate.is_duplicate is True
    enqueue_processing.assert_called_once_with()
    rack_lifecycle.record_callback_from_external_http.assert_awaited_once()
    handling_lifecycle.record_callback_from_external_http.assert_not_awaited()

    claims = await inbox_service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="runtime-inbox-integration",
        stale_after_seconds=60,
    )
    assert len(claims) == 1
    assert claims[0]["payload_json"] == payload

    result = await RuntimeInboxProcessorBridge(
        processor_service=RuntimeInboxOrchestratorDelegate(
            orchestrator_factory=_production_orchestrator_factory,
        ),
        inbox_service=inbox_service,
    ).process_claimed(db_session, claim=claims[0])

    assert result["processed"] == 1
    assert result["success"] == 1
    assert result["failed"] == 0
    persisted = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.id == claims[0]["id"]))
    assert persisted is not None
    assert not hasattr(persisted, "session_id")
    assert persisted.status == "PROCESSED"
    assert persisted.last_error_message is None
    rack_lifecycle.record_callback_from_external_http.assert_awaited_once()
    handling_lifecycle.record_callback_from_external_http.assert_not_awaited()
