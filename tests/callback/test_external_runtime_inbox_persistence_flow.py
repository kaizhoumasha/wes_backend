"""External callback -> RuntimeInbox persistence/claim/processor 真实链路。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.runtime.normalization.contracts import NormalizedExternalCallback
from src.app.runtime.normalization.normalizers import normalize_inbox_input
from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.workline.models import LineType, WorkLine


class _SuccessfulExternalProcessor:
    """用 production normalizer 驱动真实 write callback，隔离无关业务 effect。"""

    def __init__(self) -> None:
        self.calls = 0
        self.normalized_input: NormalizedExternalCallback | None = None
        self.session: WorklineSession | None = None

    async def process(self, *_args, **kwargs) -> OrchestratorResult:
        self.calls += 1
        inbox = kwargs["inbox"]
        self.session = kwargs["session"]
        normalized_input = normalize_inbox_input(
            inbox,
            trace_id=kwargs["trace_id"],
            plugin_key=kwargs["workline"].plugin_key,
        )
        assert isinstance(normalized_input, NormalizedExternalCallback)
        self.normalized_input = normalized_input
        result = OrchestratorResult(success=True, intents=[])
        await kwargs["write_callback"](result)
        return result


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
    orchestration = CallbackOrchestrationService(
        rack_task_service=rack_lifecycle,
        handling_operation_service=handling_lifecycle,
    )
    payload = {
        "callback_type": "WMS_RACK_TASK_RESULT",
        "source_system": "WMS",
        "source_event_id": "wms-runtime-inbox-ext-001",
        "dispatch_key": "missing-rack-task:runtime-inbox-ext",
        "status": "SUCCEEDED",
    }

    outcome = await orchestration.process_external(
        db_session,
        callback_type="WMS_RACK_TASK_RESULT",
        payload=payload,
        request_id="req-runtime-inbox-ext",
        trace_id="trace-runtime-inbox-ext",
        enqueue_processing=lambda: None,
    )

    assert outcome.is_duplicate is False
    rack_lifecycle.record_callback_from_external_http.assert_awaited_once()
    handling_lifecycle.record_callback_from_external_http.assert_not_awaited()

    inbox_service = RuntimeInboxService()
    claims = await inbox_service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="runtime-inbox-integration",
        stale_after_seconds=60,
    )
    assert len(claims) == 1
    assert claims[0]["payload_json"] == payload

    processor = _SuccessfulExternalProcessor()
    result = await RuntimeInboxProcessorBridge(
        processor_service=processor,  # type: ignore[arg-type]
        inbox_service=inbox_service,
    ).process_claimed(db_session, claim=claims[0])

    assert result["processed"] == 1
    assert result["success"] == 1
    assert result["failed"] == 0
    assert processor.calls == 1
    assert processor.session is not None
    assert processor.session.id == session.id
    assert processor.normalized_input is not None
    assert processor.normalized_input.callback_type == "WMS_RACK_TASK_RESULT"
    assert processor.normalized_input.payload == payload
    persisted = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.id == claims[0]["id"]))
    assert persisted is not None
    assert not hasattr(persisted, "session_id")
    assert persisted.status == "PROCESSED"
    assert persisted.last_error_message is None
    rack_lifecycle.record_callback_from_external_http.assert_awaited_once()
    handling_lifecycle.record_callback_from_external_http.assert_not_awaited()
