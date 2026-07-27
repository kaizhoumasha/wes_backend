"""SMT recovery Celery 事务边界回归。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.device.models.command import CommandResult, CommandStatus, DeviceCommand
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.celery_app.tasks.workline import _scan_smt_inbound_handoff_demands_in_transaction
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_recovery_task_commit_persists_correlation_and_picked_after_session_exit(db_engine: object) -> None:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    correlation_id = "workline-session:SMT-TASK-21"
    command_code = "SC-SOURCE-PICK-TASK-31"
    request_event_id = "source-pick-task-event-31"

    async with session_factory() as seed_db:
        demand = SmtInboundHandoffDemand(
            demand_key="task-recovery-demand",
            rack_release_id="task-recovery-release",
            single_layer_rack_code="RACK-TASK",
            source_workline_id=1,
            source_workline_code="SOURCE",
            target_workline_id=7,
            target_workline_code="SORTING",
            status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        )
        session = WorklineSession(
            session_code="SMT-TASK-21",
            workline_id=7,
            plugin_key="smt_sorting",
            run_mode=RunMode.AUTO,
            status=SessionStatus.WAITING_DEVICE_RESULT,
            context_json={},
            contract_version="1.0",
            plugin_binding_id=1,
            plugin_binding_version=1,
            plugin_config_hash="a" * 64,
            plugin_index_digest="b" * 64,
            plugin_state_json={},
            current_wait_type="DEVICE_CALLBACK",
            awaiting_device_command_code=command_code,
        )
        seed_db.add_all([demand, session])
        await seed_db.flush()

        inbox = RuntimeInbox(
            workline_session_id=session.id,
            execution_session_id=61,
            correlation_id=correlation_id,
            kind="INTERNAL_EVENT",
            workline_id=7,
            event_id=request_event_id,
            provider_code="WES_INTERNAL",
            event_type="SORTING_SOURCE_PICK_REQUESTED",
            source_event_id=request_event_id,
            payload_hash="hash-task-31",
            payload_json={"event_id": request_event_id},
            payload_schema_version=1,
            status="PROCESSED",
            claim_bucket_key=correlation_id,
            received_at=1,
            processed_at=2,
        )
        seed_db.add(inbox)
        await seed_db.flush()

        source_item = SmtInboundHandoffSourceItem(
            handoff_demand_id=demand.id,
            item_key="task-source-item",
            status=SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
            target_workline_id=7,
            target_workline_code="SORTING",
            sorting_session_id=session.id,
            claim_attempt_no=3,
            source_pick_inbox_id=inbox.id,
            updated_at=timezone.now_for_db() - timedelta(minutes=10),
        )
        seed_db.add(source_item)
        await seed_db.flush()
        session.context_json = {
            "sorting": {
                "context_schema_version": 1,
                "source_pick_request": {
                    "handoff_demand_id": demand.id,
                    "handoff_source_item_id": source_item.id,
                    "claim_attempt_no": 3,
                    "event_id": request_event_id,
                },
            }
        }
        command = DeviceCommand(
            device_id=1,
            task_type="SORTING_SOURCE_PICK",
            command_code=command_code,
            correlation_id=correlation_id,
            workline_id=7,
            plugin_key=session.plugin_key,
            contract_version=session.contract_version,
            params={
                "handoff_demand_id": demand.id,
                "handoff_source_item_id": source_item.id,
                "claim_attempt_no": 3,
                "source_pick_inbox_id": inbox.id,
                "source_pick_request_event_id": request_event_id,
            },
            status=CommandStatus.COMPLETED,
            result=CommandResult.SUCCESS,
        )
        seed_db.add_all([session, command])
        await seed_db.commit()
        source_item_id = int(source_item.id)
        command_id = int(command.id)

    async with session_factory() as scan_db:
        summary = await _scan_smt_inbound_handoff_demands_in_transaction(
            scan_db,
            service=SmtInboundHandoffService(),
            scan_limit=0,
            recovery_limit=10,
            claim_limit=0,
            stale_after_seconds=1,
            legacy_limit=None,
        )

    assert summary["advanced"] == 1
    async with session_factory() as verify_db:
        persisted = await verify_db.get(SmtInboundHandoffSourceItem, source_item_id)

    assert persisted is not None
    assert persisted.source_pick_command_id == command_id
    assert persisted.source_pick_command_code == command_code
    assert persisted.status == SmtInboundHandoffSourceItemStatus.PICKED
