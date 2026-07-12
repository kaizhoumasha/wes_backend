"""RuntimeInbox 生产三阶段链路的 PostgreSQL happy-path heavy integration。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxConflict, RuntimeInboxService
from tests.support.runtime_inbox_processing_postgresql import (
    RecordingTaskQueueGateway,
    assert_effects,
    assert_processed_terminal,
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def test_device_event_persists_claims_and_applies_production_effects_once() -> None:
    """producer→RuntimeInbox→claim→三阶段→effects→fenced terminal 必须真实闭环。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            claimed = await claim(db, service, token="it-runtime-inbox-owner")
            assert claimed["id"] == seeded.inbox_id
            assert queue_gateway.outbox_enqueues == []
            result = await processor(service).process_claimed(db, claim=claimed)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)
            await assert_effects(db, seeded, expected_count=1)
            assert queue_gateway.outbox_enqueues == [(None, 50)]

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_same_request_concurrently_creates_one_runtime_inbox() -> None:
    """真实 PostgreSQL 唯一约束与 source 行锁共同收敛并发 replay identity。"""

    class _AuditService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create_audit_log(self, *_args: Any, **_kwargs: Any) -> object:
            self.calls.append(_kwargs)
            return SimpleNamespace(id=1)

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-source",
                payload_hash="it-replay-hash",
                payload_json={"event_type": "SESSION_RESUME", "data": {}},
                payload_schema_version=1,
                status="DEAD_LETTER",
                claim_bucket_key="source:it-replay-source",
                received_at=1_700_000_000_000,
                failed_at=1_700_000_000_001,
            )
            db.add(source)
            await db.commit()
            source_id = source.id
        assert source_id is not None

        audit_service = _AuditService()

        async def replay_once() -> int:
            async with session_factory() as db:
                result = await RuntimeInboxService(audit_service=audit_service).replay_from_dead_letter(
                    db,
                    source_inbox_id=source_id,
                    request_id="concurrent-request",
                    actor="integration",
                    reason="concurrent replay",
                )
                await db.commit()
                assert result.replay_record.id is not None
                return result.replay_record.id

        replay_ids = await asyncio.gather(replay_once(), replay_once())
        assert replay_ids[0] == replay_ids[1]
        assert len(audit_service.calls) == 1

        async with session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(RuntimeInbox)
                .where(RuntimeInbox.source_event_id == f"replay:{source_id}:concurrent-request")
            )
            assert count == 1
            persisted_source = await db.get(RuntimeInbox, source_id)
            assert persisted_source is not None and persisted_source.status == "DEAD_LETTER"

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_same_identity_different_hash_has_one_success_and_one_conflict() -> None:
    """并发同 identity 异 canonical hash 必须保留一行，并各写一次成功/冲突审计。"""

    class _AuditService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create_audit_log(self, *_args: Any, **kwargs: Any) -> object:
            self.calls.append(kwargs)
            return SimpleNamespace(id=len(self.calls))

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-conflict-source",
                payload_hash="it-replay-conflict-hash",
                payload_json={"event_type": "SESSION_RESUME", "data": {}},
                payload_schema_version=1,
                status="DEAD_LETTER",
                claim_bucket_key="source:it-replay-conflict-source",
                received_at=1_700_000_000_000,
                failed_at=1_700_000_000_001,
            )
            db.add(source)
            await db.commit()
            source_id = source.id
        assert source_id is not None

        audit_service = _AuditService()

        async def replay_once(reason: str) -> tuple[str, int | None]:
            async with session_factory() as db:
                try:
                    result = await RuntimeInboxService(audit_service=audit_service).replay_from_dead_letter(
                        db,
                        source_inbox_id=source_id,
                        request_id="same-identity",
                        actor="integration",
                        reason=reason,
                    )
                except RuntimeInboxConflict:
                    await db.commit()
                    return ("conflict", None)
                await db.commit()
                return ("success", result.replay_record.id)

        outcomes = await asyncio.gather(replay_once("content-a"), replay_once("content-b"))
        assert sorted(outcome for outcome, _ in outcomes) == ["conflict", "success"]

        async with session_factory() as db:
            replay_rows = (
                (
                    await db.execute(
                        select(RuntimeInbox).where(RuntimeInbox.source_event_id == f"replay:{source_id}:same-identity")
                    )
                )
                .scalars()
                .all()
            )
            assert len(replay_rows) == 1
            persisted_source = await db.get(RuntimeInbox, source_id)
            assert persisted_source is not None and persisted_source.status == "DEAD_LETTER"

        event_types = [call["args"]["event_type"] for call in audit_service.calls]
        assert event_types.count("RUNTIME_INBOX_MANUAL_REPLAY") == 1
        assert event_types.count("RUNTIME_INBOX_MANUAL_REPLAY_CONFLICT") == 1

    asyncio.run(with_temporary_runtime_database(scenario))
