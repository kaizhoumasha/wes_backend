"""粗分机 DeviceCommand/Outbox 与 logical result 回流证据。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import func, update
from sqlmodel import select

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.workline_runtime_status_projection import WorklineRuntimeStatusProjection
from src.app.sys.models import SystemOutbox
from src.app.sys.services import AuditLogService
from src.app.wms_integration.adapters.inventory_query_port_adapter import WmsInventoryQueryPortAdapter
from src.app.wms_integration.ports.inventory_query import WmsInventoryItem
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)


async def _process_seeded_scan(db, service: RuntimeInboxService, seeded) -> None:  # type: ignore[no-untyped-def]
    claimed = await claim(db, service, token="outbox-scan-owner")
    result = await processor(service).process_claimed(db, claim=claimed)
    if result["resource_wait"]:
        await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == seeded.inbox_id).values(next_retry_at=0))
        await db.commit()
        claimed = await claim(db, service, token="outbox-scan-retry-owner")
        result = await processor(service).process_claimed(db, claim=claimed)
    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}


def test_outbox_acceptance_is_not_remote_completion_and_callback_is_runtime_inbox(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """accepted/queued/dispatched 均非完成；callback processor 才推进 typed state。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        provider_calls = 0

        async def query_inventory(_adapter, material_code: str, *, warehouse_code: str | None = None):  # type: ignore[no-untyped-def]
            nonlocal provider_calls
            provider_calls += 1
            return [
                WmsInventoryItem(
                    material_code=material_code,
                    warehouse_code=warehouse_code or "WH-IT",
                    storage_location_code="A-01",
                    quantity=10,
                    batch_no="LOT-IT-001",
                )
            ]

        monkeypatch.setattr(WmsInventoryQueryPortAdapter, "query_inventory", query_inventory)
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            await _process_seeded_scan(db, service, seeded)
            session = await db.get(WorklineSession, seeded.session_id)
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id))
            assert session is not None and command is not None
            assert session.status == "WAITING_DEVICE_RESULT"
            assert session.awaiting_device_command_code == command.command_code
            command_code = command.command_code

        async with session_factory() as db:
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
            assert command is not None and command.status == CommandStatus.PENDING
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            accepted = await RuntimeInboxService().accept_command_result(
                db,
                command_code=command_code,
                source_event_id="it-device-result-1",
                device_code="IT-ARM-01",
                workline_id=seeded.workline_id,
                device_id=seeded.arm_id,
                command_id=command.id,
                trace_id=seeded.trace_id,
                payload_json={
                    "logical_route": "PICK_AND_PUT_RESULT",
                    "command_code": command_code,
                    "command_type": "PICK_AND_PUT",
                    "result": "SUCCESS",
                    "data": {"reel_diameter": "100", "reel_thickness": "10"},
                },
            )
            await db.commit()
            callback = await db.get(RuntimeInbox, accepted.record.id)
            assert callback is not None and callback.kind == "COMMAND_RESULT"
            callback_id = int(callback.id)
            assert callback.status == "RECEIVED"
            assert command.status == CommandStatus.PENDING
            callback_claim = await claim(db, service, token="outbox-callback-owner")
            callback_result = await processor(service).process_claimed(db, claim=callback_claim)
            if callback_result["resource_wait"]:
                await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == callback_id).values(next_retry_at=0))
                await db.commit()
                callback_claim = await claim(db, service, token="outbox-callback-retry-owner")
                callback_result = await processor(service).process_claimed(db, claim=callback_claim)
            persisted_callback = await db.get(RuntimeInbox, callback_id, populate_existing=True)
            assert callback_result == {
                "processed": 1,
                "success": 1,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }, (
                persisted_callback.status,
                persisted_callback.last_error_code,
                persisted_callback.last_error_message,
            )
            await db.refresh(callback)
            await db.refresh(command)
            await db.refresh(session := await db.get(WorklineSession, seeded.session_id))
            assert callback.status == "PROCESSED"
            assert command.status == CommandStatus.PENDING
            assert session.plugin_state_json["phase"] == "MOVING_FORWARD"
            assert session.plugin_state_version == 2
            assert provider_calls == 1
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(WorklineTimeline)
                    .where(WorklineTimeline.related_inbox_id == callback.id)
                )
                == 2
            )

    asyncio.run(with_temporary_runtime_database(scenario))


def test_missing_callback_becomes_visible_timeout_without_fake_success() -> None:
    """callback 丢失经真实 TIMER reconciliation 进入唯一 Hold，且不生成插件 RuntimeIntent。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService(audit_service=AuditLogService())
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            await _process_seeded_scan(db, service, seeded)
            session = await db.get(WorklineSession, seeded.session_id)
            source_inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id))
            assert command is not None and session is not None
            assert source_inbox is not None and session.status == "WAITING_DEVICE_RESULT"
            baseline_intents = await db.scalar(select(func.count()).select_from(RuntimeIntentLog))
            assert baseline_intents == 4

            now = timezone.now_for_db()
            command.status = CommandStatus.ACK_RECEIVED
            command.ack_received_at = now - timedelta(seconds=60)
            session.deadline_at = now - timedelta(seconds=1)
            await db.commit()

            timeout = await service.accept_timer_timeout(
                db,
                session_id=seeded.session_id,
                execution_session_id=source_inbox.execution_session_id,
                workline_id=seeded.workline_id,
                deadline_at=session.deadline_at,
                trace_id=seeded.trace_id,
                wait_token=command.command_code,
                wait_type="COMMAND_RESULT",
                awaiting_device_command_code=command.command_code,
                command_code=command.command_code,
                device_id=seeded.arm_id,
                command_id=command.id,
                command_status=CommandStatus.ACK_RECEIVED.value,
                ack_received_at=command.ack_received_at,
            )
            await db.commit()
            duplicate = await service.accept_timer_timeout(
                db,
                session_id=seeded.session_id,
                execution_session_id=source_inbox.execution_session_id,
                workline_id=seeded.workline_id,
                deadline_at=session.deadline_at,
                trace_id=seeded.trace_id,
                wait_token=command.command_code,
                wait_type="COMMAND_RESULT",
                awaiting_device_command_code=command.command_code,
                command_code=command.command_code,
                device_id=seeded.arm_id,
                command_id=command.id,
                command_status=CommandStatus.ACK_RECEIVED.value,
                ack_received_at=command.ack_received_at,
            )
            assert duplicate.created is False and duplicate.record.id == timeout.record.id

            timeout_claim = await claim(db, service, token="outbox-timeout-owner")
            result = await processor(service).process_claimed(db, claim=timeout_claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}

        async with session_factory() as verify_db:
            timer = await verify_db.get(RuntimeInbox, timeout.record.id)
            session = await verify_db.get(WorklineSession, seeded.session_id)
            command = await verify_db.get(DeviceCommand, command.id)
            hold = await verify_db.scalar(select(RuntimeHold).where(RuntimeHold.session_id == seeded.session_id))
            assert timer is not None and timer.kind == "TIMER_TIMEOUT" and timer.status == "PROCESSED"
            assert session is not None and session.status == "MANUAL_HOLD"
            assert session.reconciliation_reason == "CALLBACK_DEADLINE_EXPIRED"
            assert hold is not None and hold.source_reason == "ROUGH_SORTER_PICK_RESULT_TIMEOUT"
            assert command is not None and command.status != CommandStatus.COMPLETED
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeHold)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == baseline_intents

            await verify_db.execute(
                update(RuntimeInbox)
                .where(RuntimeInbox.id == timer.id)
                .values(status="DEAD_LETTER", last_error_code="IT_TIMEOUT_REPLAY", processor_token=None)
            )
            await verify_db.execute(
                update(WorklineRuntimeStatusProjection)
                .where(WorklineRuntimeStatusProjection.workline_id == seeded.workline_id)
                .values(runtime_status="READY", stopped_reason=None)
            )
            await verify_db.commit()
            replay = await service.replay_from_dead_letter(
                verify_db,
                source_inbox_id=int(timer.id),
                request_id="it-timeout-recorded-replay",
                actor="integration",
                reason="verify timeout recorded replay has zero new hold",
            )
            replay_id = int(replay.replay_record.id)
            await verify_db.commit()
            replay_claim = await claim(verify_db, service, token="timeout-recorded-replay-owner")
            replay_result = await processor(service).process_claimed(verify_db, claim=replay_claim)
            assert replay_result["processed"] == 1
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeHold)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == baseline_intents
            replay_row = await verify_db.get(RuntimeInbox, replay_id, populate_existing=True)
            assert replay_row is not None and replay_row.status == "DEAD_LETTER"
            assert replay_row.last_error_code == "RECORDED_REPLAY_RECORD_MISSING"

    asyncio.run(with_temporary_runtime_database(scenario))
