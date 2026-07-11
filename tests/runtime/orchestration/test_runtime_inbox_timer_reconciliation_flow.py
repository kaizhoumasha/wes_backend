"""RuntimeInbox TIMER_TIMEOUT producer 到 fenced terminal 的集成合同。"""

from __future__ import annotations

import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.app.device.models.command import CommandStatus
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_timer_timeout_producer_claim_bridge_uses_runtime_fenced_terminal(db_session, monkeypatch) -> None:
    """真实 producer/claim/bridge 必须解析 canonical timeout 并写 RuntimeInbox 终态。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
        RuntimeInboxProcessorBridge,
    )

    bridge_module = importlib.import_module(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge"
    )
    reconciliation_module = importlib.import_module(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl"
    )
    command_repository_module = importlib.import_module("src.app.device.repositories.command_repository")
    legacy_inbox_service_module = importlib.import_module("src.app.runtime.orchestration.services.inbox.inbox_service")

    execution_session = ExecutionSession(workline_id=45, manifest_version="manifest-v1", state="RUNNING")
    db_session.add(execution_session)
    await db_session.flush()
    assert execution_session.id is not None

    now = timezone.now_for_db()
    deadline_at = now - timedelta(seconds=1)
    ack_received_at = now - timedelta(seconds=30)
    command = SimpleNamespace(
        id=991,
        command_code="CMD-RUNTIME-TIMER-001",
        device_id=7,
        correlation_id=None,
        status=CommandStatus.ACK_RECEIVED,
        ack_received_at=ack_received_at,
    )
    runtime_session = SimpleNamespace(
        id=execution_session.id,
        workline_id=45,
        trace_id="trace-runtime-timer-001",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=now - timedelta(seconds=400),
        deadline_at=deadline_at,
        awaiting_device_command_code=command.command_code,
        reconciliation_state=None,
        context_json={},
        ended_at=None,
    )
    workline = SimpleNamespace(id=45)

    service = RuntimeInboxService()
    accepted = await service.accept_timer_timeout(
        db_session,
        execution_session_id=execution_session.id,
        workline_id=45,
        deadline_at=deadline_at,
        trace_id="trace-runtime-timer-001",
        wait_token=command.command_code,
        wait_type="COMMAND_RESULT",
        awaiting_device_command_code=command.command_code,
        command_code=command.command_code,
        device_id=7,
        device_code="ARM_07",
        command_id=991,
        command_status=CommandStatus.ACK_RECEIVED.value,
        ack_received_at=ack_received_at,
    )
    await db_session.commit()
    claims = await service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="runtime-timer-worker-001",
        stale_after_seconds=30,
    )
    assert [claim["id"] for claim in claims] == [accepted.record.id]

    monkeypatch.setattr(
        bridge_module,
        "_load_related_entities",
        AsyncMock(return_value=(runtime_session, workline, None, command, {}, SimpleNamespace(), True)),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.session_repository,
        "get_for_update",
        AsyncMock(return_value=runtime_session),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.workline_repository,
        "get_for_update",
        AsyncMock(return_value=workline),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.workline_status_projection_service,
        "project_reconciling",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.device_service,
        "mark_callback_deadline_expired",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.system_outbox_repository,
        "cancel_active_by_session",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.rack_task_repository,
        "cancel_active_by_material_session",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.runtime_hold_creation_service,
        "create_for_callback_deadline_expired",
        AsyncMock(return_value=SimpleNamespace(id=9905)),
    )

    class _CommandRepo:
        async def get_by_command_code(self, _db, _command_code):
            return command

    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", _CommandRepo)
    monkeypatch.setattr(reconciliation_module, "add_timeline_with_sequence", AsyncMock())
    monkeypatch.setattr(reconciliation_module.workline_diagnostic_service, "record_event", AsyncMock())
    legacy_terminal_writer = AsyncMock()
    monkeypatch.setattr(legacy_inbox_service_module.inbox_service, "mark_as_processed", legacy_terminal_writer)

    result = await RuntimeInboxProcessorBridge(inbox_service=service).process_claimed(
        db_session,
        claim=claims[0],
    )

    stored = (await db_session.execute(select(RuntimeInbox).where(RuntimeInbox.id == accepted.record.id))).scalar_one()
    await db_session.refresh(stored)
    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert stored.status == "PROCESSED"
    assert stored.processor_token == "runtime-timer-worker-001"
    assert stored.processed_at is not None
    legacy_terminal_writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_timer_terminal_rejects_lost_processor_token(monkeypatch) -> None:
    """Runtime TIMER helper 在 lease token 丢失时必须拒绝报告终态成功。"""

    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
        _handle_timer_timeout,
    )

    monkeypatch.setattr(
        workline_runtime_reconciliation_service.session_repository,
        "get_for_update",
        AsyncMock(return_value=None),
    )
    runtime_terminal_writer = SimpleNamespace(mark_processed=AsyncMock(return_value=False))
    db = SimpleNamespace()

    with pytest.raises(RuntimeError, match="lease lost"):
        await _handle_timer_timeout(
            db,
            inbox=SimpleNamespace(
                execution_session_id=553,
                correlation_id=None,
                trace_id="trace-lost-token",
            ),
            inbox_pk=901,
            payload={"event_type": "TIMER_TIMEOUT", "data": {}},
            processor_token="stale-worker-token",
            inbox_service=runtime_terminal_writer,  # type: ignore[arg-type]
        )

    runtime_terminal_writer.mark_processed.assert_awaited_once_with(
        db,
        inbox_id=901,
        lease_token="stale-worker-token",
    )
