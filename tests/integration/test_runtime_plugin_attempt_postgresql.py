"""平台插件 Stage3 在真实 PostgreSQL 上的 stale snapshot 并发合同。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, update
from sqlmodel import select

from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import RuntimeInboxService
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


def test_stale_plugin_state_version_cannot_write_evidence_state_intents_or_terminal() -> None:
    """另一个事务推进 plugin state 后，旧 attempt 必须锁后 SAFE_RETRY 且零写。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as snapshot_db:
            seeded = await seed_scan_flow(snapshot_db)
            claimed = await claim(snapshot_db, service, token="plugin-attempt-owner")
            session = await snapshot_db.get(WorklineSession, seeded.session_id)
            assert session is not None
            snapshot = AttemptSnapshot(
                processor_token=str(claimed["processor_token"]),
                session_version=session.version,
                plugin_state_version=session.plugin_state_version,
            )

        async with session_factory() as concurrent_db:
            await concurrent_db.execute(
                update(WorklineSession)
                .where(WorklineSession.id == seeded.session_id)
                .values(plugin_state_version=snapshot.plugin_state_version + 1)
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
                write_set=AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=()),
            )
            assert disposition is WriteDisposition.SAFE_RETRY
            inbox = await writeback_db.get(RuntimeInbox, seeded.inbox_id)
            assert inbox is not None and inbox.status == "PROCESSING"
            timeline_count = await writeback_db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(WorklineTimeline.related_inbox_id == seeded.inbox_id)
            )
            assert timeline_count == 0

    asyncio.run(with_temporary_runtime_database(scenario))
