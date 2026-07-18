"""粗分机插件 attempt 的 PostgreSQL 并发与 fencing 证据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, update
from sqlmodel import select

from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
)
from src.app.runtime.workline_plugins.attempt_coordinator import (
    AttemptSnapshot,
    AttemptWriteSet,
    WriteDisposition,
)
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    seed_scan_flow,
    with_temporary_runtime_database,
)


def _snapshot(claimed: dict[str, object], session: WorklineSession) -> AttemptSnapshot:
    return AttemptSnapshot(
        processor_token=str(claimed["processor_token"]),
        session_version=session.version,
        plugin_state_version=session.plugin_state_version,
        session_status=session.status.value,
        definition_identity=f"{session.plugin_key}@{session.contract_version}",
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


def test_two_workers_claim_same_business_key_only_once() -> None:
    """两个 PostgreSQL worker 同时 claim 同一 key，只允许一个 owner 获得 decision 权。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)

        ready = asyncio.Event()

        async def worker(token: str) -> list[dict[str, object]]:
            async with session_factory() as db:
                ready.set()
                await ready.wait()
                rows = await service.claim_for_processing(
                    db,
                    limit=1,
                    processor_token=token,
                    stale_after_seconds=60,
                )
                await db.commit()
                return rows

        first, second = await asyncio.gather(worker("worker-a"), worker("worker-b"))
        claims = [row for batch in (first, second) for row in batch]
        assert [row["id"] for row in claims] == [seeded.inbox_id]
        assert len({row["processor_token"] for row in claims}) == 1

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
