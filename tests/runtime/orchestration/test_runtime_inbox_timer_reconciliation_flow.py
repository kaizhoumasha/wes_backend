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
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.utils.timezone import timezone
from tests.support.runtime_binding import binding_pin_fields


@pytest.mark.asyncio
async def test_timer_timeout_producer_claim_bridge_uses_runtime_fenced_terminal(db_session, monkeypatch) -> None:
    """真实 producer/claim/bridge 必须解析 canonical timeout 并写 RuntimeInbox 终态。"""

    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )
    from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
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

    runtime_session = WorklineSession(
        id=1001,
        session_code="session-runtime-timer-001",
        workline_id=45,
        plugin_key="test_workline_plugin",
        contract_version="v1",
        **binding_pin_fields(),
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    wrong_id_sentinel = WorklineSession(
        id=9001,
        session_code="session-runtime-timer-wrong-id-sentinel",
        workline_id=45,
        plugin_key="test_workline_plugin",
        contract_version="v1",
        **binding_pin_fields(),
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    execution_session = ExecutionSession(
        id=9001,
        workline_id=45,
        plugin_key="test-plugin",
        manifest_version="test-manifest-v1",
        **binding_pin_fields(),
        state="RUNNING",
    )
    db_session.add_all([runtime_session, wrong_id_sentinel, execution_session])
    await db_session.flush()
    assert runtime_session.id is not None

    now = timezone.now_for_db()
    deadline_at = now - timedelta(seconds=1)
    ack_received_at = now - timedelta(seconds=30)
    command = SimpleNamespace(
        id=None,
        command_code="CMD-RUNTIME-TIMER-001",
        device_id=None,
        correlation_id=None,
        status=CommandStatus.ACK_RECEIVED,
        ack_received_at=ack_received_at,
    )
    runtime_session.trace_id = "trace-runtime-timer-001"
    runtime_session.current_wait_type = "COMMAND_RESULT"
    runtime_session.current_wait_timeout_seconds = 300
    runtime_session.waiting_since = now - timedelta(seconds=400)
    runtime_session.deadline_at = deadline_at
    runtime_session.awaiting_device_command_code = command.command_code
    runtime_session.context_json = {}
    wrong_id_sentinel.trace_id = "trace-wrong-id-sentinel"
    wrong_id_sentinel.current_wait_type = "COMMAND_RESULT"
    wrong_id_sentinel.current_wait_timeout_seconds = 300
    wrong_id_sentinel.waiting_since = runtime_session.waiting_since
    wrong_id_sentinel.deadline_at = deadline_at
    wrong_id_sentinel.awaiting_device_command_code = command.command_code
    wrong_id_sentinel.context_json = {}
    workline = SimpleNamespace(id=45)

    service = RuntimeInboxService()
    accepted = await service.accept_timer_timeout(
        db_session,
        session_id=runtime_session.id,
        execution_session_id=execution_session.id,
        workline_id=45,
        deadline_at=deadline_at,
        trace_id="trace-runtime-timer-001",
        wait_token=command.command_code,
        wait_type="COMMAND_RESULT",
        awaiting_device_command_code=command.command_code,
        command_code=command.command_code,
        device_code="ARM_07",
        command_status=CommandStatus.ACK_RECEIVED.value,
        ack_received_at=ack_received_at,
    )
    assert accepted.record.execution_session_id == execution_session.id
    assert accepted.record.payload_json["data"]["session_id"] == runtime_session.id
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
    cancel_outbox = AsyncMock(return_value=0)
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.system_outbox_cancellation_service,
        "cancel_active_by_session",
        cancel_outbox,
    )
    cancel_rack_task = AsyncMock(return_value=0)
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.rack_task_repository,
        "cancel_active_by_material_session",
        cancel_rack_task,
    )

    class _CommandRepo:
        async def get_by_command_code(self, _db, _command_code):
            return command

    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", _CommandRepo)
    monkeypatch.setattr(reconciliation_module, "add_timeline_with_sequence", AsyncMock())
    monkeypatch.setattr(reconciliation_module.workline_diagnostic_service, "record_event", AsyncMock())

    result = await RuntimeInboxProcessorBridge(inbox_service=service).process_claimed(
        db_session,
        claim=claims[0],
    )

    stored = (await db_session.execute(select(RuntimeInbox).where(RuntimeInbox.id == accepted.record.id))).scalar_one()
    await db_session.refresh(stored)
    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert stored.status == "PROCESSED"
    assert stored.processor_token is None
    assert stored.lease_until is None
    assert stored.processed_at is not None
    hold = (await db_session.execute(select(RuntimeHold))).scalar_one()
    assert hold.source_inbox_id == stored.id
    assert hold.source_idempotency_key == f"callback-timeout:runtime-inbox:{runtime_session.id}:{stored.id}"
    assert hold.evidence_snapshot_json["inbox_id"] == stored.id
    assert hold.evidence_snapshot_json["inbox_store"] == "runtime_inbox"
    await db_session.refresh(runtime_session)
    await db_session.refresh(wrong_id_sentinel)
    assert runtime_session.status == SessionStatus.MANUAL_HOLD
    assert wrong_id_sentinel.status == SessionStatus.WAITING_DEVICE_RESULT
    cancel_outbox.assert_awaited_once_with(
        db_session,
        session_id=runtime_session.id,
        reason="CALLBACK_DEADLINE_EXPIRED",
    )
    cancel_rack_task.assert_awaited_once_with(
        db_session,
        material_session_id=runtime_session.id,
        reason="CALLBACK_DEADLINE_EXPIRED",
    )


@pytest.mark.asyncio
async def test_runtime_timer_terminal_rejects_lost_processor_token(monkeypatch) -> None:
    """Runtime TIMER helper 在 lease token 丢失时必须拒绝报告终态成功。"""

    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
        _handle_timer_timeout,
    )

    missing_session_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        workline_runtime_reconciliation_service.session_repository,
        "get_for_update",
        missing_session_lookup,
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
    missing_session_lookup.assert_not_awaited()
