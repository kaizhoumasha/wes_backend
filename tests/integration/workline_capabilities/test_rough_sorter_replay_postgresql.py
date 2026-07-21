"""粗分机 deterministic replay 与 zero-new-effect PostgreSQL 证据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, update
from sqlmodel import select

from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.sys.models import SystemOutbox
from src.app.sys.services import AuditLogService
from src.app.wms_integration.adapters import InventoryQueryOperationAdapter
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import QuerySuccess
from tests.integration.workline_capabilities.test_rough_sorter_outbox_result_flow import _process_seeded_scan
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)


def test_recorded_replay_of_successful_query_never_calls_provider_or_creates_effect(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """首次 callback 走真实 QUERY；manual recorded replay 复用决策且零 provider/新 effect。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        provider_calls = 0

        async def query_inventory(_adapter, request):  # type: ignore[no-untyped-def]
            nonlocal provider_calls
            provider_calls += 1
            return QuerySuccess(
                InventoryQueryOperationResult(
                    items=(
                        InventoryAuthorityItem(
                            material_code=request.material_code,
                            warehouse_code=request.warehouse_code or "WH-IT",
                            storage_location_code="A-01",
                            available_quantity=10,
                            lot_no="LOT-IT-001",
                        ),
                    ),
                    source_version="WMS-IT-1",
                )
            )

        monkeypatch.setattr(InventoryQueryOperationAdapter, "execute", query_inventory)
        service = RuntimeInboxService(audit_service=AuditLogService())

        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            await _process_seeded_scan(db, service, seeded)
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id))
            assert command is not None
            accepted = await service.accept_command_result(
                db,
                command_code=command.command_code,
                source_event_id="it-replay-success-callback",
                device_code="IT-ARM-01",
                workline_id=seeded.workline_id,
                device_id=seeded.arm_id,
                command_id=command.id,
                trace_id=seeded.trace_id,
                payload_json={
                    "logical_route": "PICK_AND_PUT_RESULT",
                    "command_code": command.command_code,
                    "command_type": "PICK_AND_PUT",
                    "result": "SUCCESS",
                    "data": {"reel_diameter": "100", "reel_thickness": "10"},
                },
            )
            await db.commit()
            callback_id = int(accepted.record.id)
            callback_claim = await claim(db, service, token="replay-live-callback-owner")
            live_result = await processor(service).process_claimed(db, claim=callback_claim)
            if live_result["resource_wait"]:
                await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == callback_id).values(next_retry_at=0))
                await db.commit()
                callback_claim = await claim(db, service, token="replay-live-callback-retry")
                live_result = await processor(service).process_claimed(db, claim=callback_claim)
            assert live_result["success"] == 1 and provider_calls == 1
            baseline_intents = await db.scalar(select(func.count()).select_from(RuntimeIntentLog))
            baseline_outbox = await db.scalar(select(func.count()).select_from(SystemOutbox))
            await db.execute(
                update(RuntimeInbox)
                .where(RuntimeInbox.id == callback_id)
                .values(status="DEAD_LETTER", last_error_code="IT_REPLAY", processor_token=None, lease_until=None)
            )
            await db.commit()

            replay = await service.replay_from_dead_letter(
                db,
                source_inbox_id=callback_id,
                request_id="it-recorded-success-replay",
                actor="integration",
                reason="verify recorded decision replay",
            )
            replay_id = int(replay.replay_record.id)
            await db.commit()
            replay_claim = await claim(db, service, token="recorded-success-replay-owner")
            replay_result = await processor(service).process_claimed(db, claim=replay_claim)
            assert replay_result["success"] == 1
            assert provider_calls == 1
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == baseline_intents
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == baseline_outbox
            db.expire_all()
            replay_row = await db.get(RuntimeInbox, replay_id)
            assert replay_row is not None and replay_row.status == "PROCESSED"

    asyncio.run(with_temporary_runtime_database(scenario))
