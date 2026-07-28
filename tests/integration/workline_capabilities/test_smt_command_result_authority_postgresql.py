"""SMT COMMAND_RESULT 持久命令权威链 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_context_loader import load_related_entities
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.smt_sorting_inbound_postgresql import (
    NoopQueueGateway,
    process_smt_source_pick_claim,
    seed_smt_source_pick_claim,
    snapshot_smt_write_set,
)


def _effect_state(snapshot: object) -> tuple[object, ...]:
    sessions = tuple(
        tuple((field, value) for field, value in row if field not in {"version", "updated_at", "plugin_state_version"})
        for row in snapshot.sessions
    )
    return (
        snapshot.worklines,
        snapshot.devices,
        snapshot.commands,
        snapshot.outboxes,
        sessions,
        snapshot.execution_sessions,
        snapshot.execution_work_items,
        snapshot.execution_correlations,
        snapshot.runtime_intent_logs,
        snapshot.runtime_holds,
        snapshot.idempotency_keys,
        snapshot.runtime_status_projections,
        snapshot.device_runtime_projections,
        snapshot.source_items,
        snapshot.demands,
        snapshot.attempt_evidence,
    )


async def _assert_invalid_callback(
    db: AsyncSession,
    *,
    suffix: str,
    mode: str,
    expected_diagnostic: str,
) -> None:
    seeded = await seed_smt_source_pick_claim(db, suffix=suffix)
    processed = await process_smt_source_pick_claim(db, seeded)
    command_id: int | None
    expected_loaded_command: DeviceCommand | None
    if mode == "missing":
        command_id = None
        expected_loaded_command = None
    elif mode == "not-found":
        command_id = 2_147_483_647
        expected_loaded_command = None
    else:
        foreign = DeviceCommand(
            command_code=f"IT-SMT-FOREIGN-{suffix}",
            device_id=processed.command.device_id,
            task_type=processed.command.task_type,
            correlation_id=processed.command.correlation_id,
            workline_id=processed.command.workline_id,
            plugin_key=processed.command.plugin_key,
            contract_version=processed.command.contract_version,
        )
        db.add(foreign)
        await db.flush()
        command_id = int(foreign.id)
        expected_loaded_command = foreign

    accepted = await RuntimeInboxService().accept_command_result(
        db,
        command_code=processed.command.command_code,
        command_id=command_id,
        source_event_id=f"IT-SMT-INVALID-CALLBACK-{suffix}",
        source_provider_code="ECS",
        source_event_type="DEVICE_RESULT",
        device_code=seeded.device_code,
        device_id=seeded.device_id,
        workline_id=seeded.workline_id,
        correlation_id=processed.command.correlation_id,
        trace_id=f"trace-invalid-{suffix}",
        payload_json={
            "command_code": processed.command.command_code,
            "device_code": seeded.device_code,
            "result": "SUCCESS",
        },
    )
    inbox = accepted.record
    related = await load_related_entities(db, inbox)
    assert related["command"] is expected_loaded_command
    assert inbox.command_id == command_id
    assert related["session"] is not None
    await db.commit()

    before = await snapshot_smt_write_set(db)
    [claim] = await RuntimeInboxService().claim_for_processing(
        db,
        limit=1,
        processor_token=f"lease-invalid-{suffix}",
        stale_after_seconds=60,
    )
    await db.commit()
    result = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(db, claim=claim)
    assert result["success"] == 1
    await db.commit()

    after = await snapshot_smt_write_set(db)
    assert _effect_state(after) == _effect_state(before)
    decision = await db.scalar(
        select(WorklineTimeline)
        .where(
            WorklineTimeline.related_inbox_id == inbox.id,
            WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
        )
        .order_by(WorklineTimeline.id.desc())
    )
    if decision is None:
        timelines = list((await db.scalars(select(WorklineTimeline).order_by(WorklineTimeline.id))).all())
        raise AssertionError(
            f"缺少 PLUGIN_DECISION: inbox={inbox.id}, "
            f"timelines={[(row.related_inbox_id, row.payload_json) for row in timelines]}"
        )
    assert decision.payload_json["decision"]["outcome_code"] == expected_diagnostic


def test_command_result_missing_wrong_or_mismatched_command_id_has_zero_effect() -> None:
    async def scenario() -> None:
        cases = (
            ("missing", "missing", "COMMAND_ID_MISSING"),
            ("not-found", "not-found", "COMMAND_NOT_FOUND"),
            ("mismatch", "mismatch", "COMMAND_RESULT_CORRELATION_MISMATCH"),
        )
        for suffix, mode, diagnostic in cases:
            async with temporary_database() as (_database, database_url):
                run_alembic("upgrade", "head", database_url=database_url)
                engine = create_async_engine(database_url, pool_pre_ping=True)
                session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                try:
                    async with session_factory() as db:
                        await _assert_invalid_callback(
                            db,
                            suffix=suffix,
                            mode=mode,
                            expected_diagnostic=diagnostic,
                        )
                finally:
                    await engine.dispose()

    asyncio.run(scenario())
