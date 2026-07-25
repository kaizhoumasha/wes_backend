"""粗分机插件 attempt 的 PostgreSQL 并发与 fencing 证据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, update
from sqlmodel import select

from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
)
from src.app.runtime.workline_plugins.attempt_coordinator import (
    AttemptSnapshot,
    AttemptWriteSet,
    WriteDisposition,
)
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.sys.models import SystemOutbox
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_duplicate_scan_inbox,
    seed_scan_flow,
    with_temporary_runtime_database,
)


def _snapshot(claimed: dict[str, object], session: WorklineSession) -> AttemptSnapshot:
    return AttemptSnapshot(
        processor_token=str(claimed["processor_token"]),
        session_version=session.version,
        plugin_state_version=session.plugin_state_version,
        session_status=session.status.value,
        definition_identity=DEFINITION.identity,
        binding_id=session.plugin_binding_id,
        binding_version=session.plugin_binding_version,
        plugin_config_hash=session.plugin_config_hash,
        index_digest=session.plugin_index_digest,
    )


def test_query_snapshot_change_discards_decision_without_partial_write() -> None:
    """QUERY 后 session/plugin token 任一变化，旧 decision 必须 SAFE_RETRY 且零写。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            claimed = await claim(db, service, token="query-stale-owner")
            session = await db.get(WorklineSession, seeded.session_id)
            assert session is not None
            snapshot = _snapshot(claimed, session)

        async with session_factory() as concurrent_db:
            await concurrent_db.execute(
                update(WorklineSession)
                .where(WorklineSession.id == seeded.session_id)
                .values(plugin_state_version=snapshot.plugin_state_version + 1, version=snapshot.session_version + 1)
            )
            await concurrent_db.commit()

        async with session_factory() as writeback_db:
            disposition = await RuntimeInboxWriteBackService(inbox_service=service).commit_plugin_attempt(
                writeback_db,
                expected_snapshot=snapshot,
                inbox_id=seeded.inbox_id,
                session_id=seeded.session_id,
                workline_id=seeded.workline_id,
                trace_id=seeded.trace_id,
                write_set=AttemptWriteSet(
                    evidence=(),
                    next_state={"phase": "PICK_TO_PIPELINE"},
                    intents=(),
                    outcome_code="PICK_AND_PUT_PERSISTED",
                ),
            )
            assert disposition is WriteDisposition.SAFE_RETRY
            assert (
                await writeback_db.scalar(
                    select(func.count())
                    .select_from(WorklineTimeline)
                    .where(WorklineTimeline.related_inbox_id == seeded.inbox_id)
                )
                == 0
            )

    asyncio.run(with_temporary_runtime_database(scenario))


def test_two_workers_process_distinct_inboxes_for_same_business_key_exactly_once() -> None:
    """两个 worker 并发处理同业务键的不同 Inbox，第二条按 duplicate 合同归档。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            duplicate_inbox_id = await seed_duplicate_scan_inbox(db, seeded)

        ready = asyncio.Barrier(2)

        async def worker(token: str) -> dict[str, int]:
            async with session_factory() as db:
                await ready.wait()
                return await processor(service).claim_and_process_batch(
                    db,
                    limit=1,
                    processor_token_prefix=token,
                )

        first, second = await asyncio.gather(worker("worker-a"), worker("worker-b"))
        assert sum(result["processed"] for result in (first, second)) == 1, (first, second)

        # 第一条若因资源快照重试，推进其 next_retry_at 后仍由真实 processor 收敛。
        # 恢复与 claim 分离 Session，避免测试代码预加载 ORM 行污染 claim 返回快照。
        for _ in range(3):
            async with session_factory() as db:
                processing = (
                    (await db.execute(select(RuntimeInbox).where(RuntimeInbox.status == "PROCESSING"))).scalars().all()
                )
                if processing:
                    for row in processing:
                        row.lease_until = 0
                    await db.commit()
                    _ = await service.recover_stale_leases(db, stale_after_seconds=60, limit=2)
                    await db.commit()
                pending = (
                    (await db.execute(select(RuntimeInbox).where(RuntimeInbox.status.in_(("RECEIVED", "FAILED")))))
                    .scalars()
                    .all()
                )
                if not pending:
                    break
                for row in pending:
                    row.next_retry_at = 0
                await db.commit()
            async with session_factory() as db:
                _ = await processor(service).claim_and_process_batch(
                    db,
                    limit=1,
                    processor_token_prefix="worker-converge",
                )

        async with session_factory() as db:
            inboxes = (
                (
                    await db.execute(
                        select(RuntimeInbox).where(RuntimeInbox.id.in_((seeded.inbox_id, duplicate_inbox_id)))
                    )
                )
                .scalars()
                .all()
            )
            assert {row.status for row in inboxes} == {"PROCESSED"}, (
                first,
                second,
                [(row.id, row.status) for row in inboxes],
            )
            session = await db.get(WorklineSession, seeded.session_id)
            assert session is not None
            assert session.plugin_state_version == 1
            assert session.plugin_state_json["phase"] == "PICK_TO_PIPELINE"
            plugin_decisions = await db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION")
            )
            duplicate_archives = await db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(
                    WorklineTimeline.related_inbox_id == duplicate_inbox_id,
                    WorklineTimeline.message == "DUPLICATE_ENTRY_ARCHIVED",
                )
            )
            assert plugin_decisions == 1
            assert duplicate_archives == 1
            intents = list((await db.execute(select(RuntimeIntentLog).order_by(RuntimeIntentLog.id))).scalars())
            assert tuple(intent.capability_key for intent in intents) == (
                "material_flow.material_unit_write",
                "device.device_command_write",
            )
            outboxes = list((await db.execute(select(SystemOutbox).order_by(SystemOutbox.id))).scalars())
            device_intents = [intent for intent in intents if intent.capability_key == "device.device_command_write"]
            assert tuple(outbox.dispatch_key for outbox in outboxes) == tuple(
                intent.dispatch_key for intent in device_intents
            )

    asyncio.run(with_temporary_runtime_database(scenario))


def test_lost_lease_owner_cannot_commit_plugin_attempt() -> None:
    """lease token 被新 owner 替换后，旧 owner 的 state/evidence/intent 不得提交。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            old_claim = await claim(db, service, token="lease-old")
            session = await db.get(WorklineSession, seeded.session_id)
            assert session is not None
            snapshot = _snapshot(old_claim, session)
            await db.execute(
                update(RuntimeInbox).where(RuntimeInbox.id == seeded.inbox_id).values(processor_token="lease-new")
            )
            await db.commit()

        async with session_factory() as db:
            disposition = await RuntimeInboxWriteBackService(inbox_service=service).commit_plugin_attempt(
                db,
                expected_snapshot=snapshot,
                inbox_id=seeded.inbox_id,
                session_id=seeded.session_id,
                workline_id=seeded.workline_id,
                trace_id=seeded.trace_id,
                write_set=AttemptWriteSet(
                    evidence=(),
                    next_state={"phase": "PICK_TO_PIPELINE"},
                    intents=(),
                    outcome_code="PICK_AND_PUT_PERSISTED",
                ),
            )
            assert disposition is WriteDisposition.SAFE_RETRY
            persisted = await db.get(RuntimeInbox, seeded.inbox_id)
            assert persisted is not None and persisted.processor_token == "lease-new"

    asyncio.run(with_temporary_runtime_database(scenario))
