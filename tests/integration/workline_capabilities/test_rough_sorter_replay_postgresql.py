"""粗分机 deterministic replay 与 zero-new-effect PostgreSQL 证据。"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import func, update
from sqlmodel import select

from src.app.callback.services.callback_ingress_service import CallbackIngressService
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.ports.inventory_operations import (
    InventoryRecord,
    InventorySnapshotQueryResult,
)
from src.app.wms_integration.ports.query_outcome import QuerySuccess
from tests.integration.workline_capabilities.test_rough_sorter_outbox_result_flow import (
    _callback_request,
    _process_seeded_scan,
)
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)
from tests.support.wms_query_runtime import bind_stub_wms_query_runtime


def test_recorded_replay_of_persisted_q19_never_calls_provider_or_creates_effect(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """首次 callback 与 recorded replay 都消费 SCAN 已持久化的 Q19，零 provider/新 effect。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        provider_calls = 0

        async def forbidden_query(*_args):  # type: ignore[no-untyped-def]
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("Q19 持久化后不得回查 provider")

        bind_stub_wms_query_runtime(monkeypatch, forbidden_query)

        async def invalidate_cache(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(DeviceCommandService, "_invalidate_command_cache", invalidate_cache)
        service = RuntimeInboxService()

        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            await _process_seeded_scan(db, service, seeded)
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id))
            assert command is not None
            response = await CallbackIngressService().handle_result(
                _callback_request(
                    {
                        "command_code": command.command_code,
                        "device_code": "IT-ARM-01",
                        "result": "SUCCESS",
                        "finish_time": int(time.time() * 1000),
                        "source_event_id": "it-replay-success-callback",
                        "trace_id": seeded.trace_id,
                        "data": {"reel_diameter": "100", "reel_thickness": "10"},
                    }
                ),
                db,
                request_id="it-replay-success-request",
                start_time=time.time(),
                enqueue_processing=lambda: None,
            )
            assert response["code"] == "1000"
            callback = await db.scalar(
                select(RuntimeInbox).where(
                    RuntimeInbox.provider_code == "ECS",
                    RuntimeInbox.source_event_id == "it-replay-success-callback",
                )
            )
            assert callback is not None
            callback_id = int(callback.id)
            await db.refresh(command)
            assert command.status == CommandStatus.COMPLETED
            callback_claim = await claim(db, service, token="replay-live-callback-owner")
            live_result = await processor(service).process_claimed(db, claim=callback_claim)
            if live_result["resource_wait"]:
                await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == callback_id).values(next_retry_at=0))
                await db.commit()
                callback_claim = await claim(db, service, token="replay-live-callback-retry")
                live_result = await processor(service).process_claimed(db, claim=callback_claim)
            assert live_result["success"] == 1 and provider_calls == 0
            baseline_intents = await db.scalar(select(func.count()).select_from(RuntimeIntentLog))
            baseline_outbox = await db.scalar(select(func.count()).select_from(SystemOutbox))
            recorded_decision_count = await db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(
                    WorklineTimeline.related_inbox_id == callback_id,
                    WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
                )
            )
            assert recorded_decision_count == 1
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
            assert provider_calls == 0
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == baseline_intents
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == baseline_outbox
            db.expire_all()
            replay_row = await db.get(RuntimeInbox, replay_id)
            assert replay_row is not None and replay_row.status == "PROCESSED"

    asyncio.run(with_temporary_runtime_database(scenario))
