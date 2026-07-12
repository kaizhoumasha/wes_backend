"""RuntimeInbox 生产三阶段链路的 PostgreSQL happy-path heavy integration。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.app.runtime.orchestration.models.session import RuntimeReconciliationState, SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.intent.operation_service import WorklineOperationService
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxConflict,
    RuntimeInboxReplayNotAllowed,
    RuntimeInboxService,
)
from src.app.sys.models.audit_log import AuditLog, OperaStatus
from src.app.sys.services import AuditLogService
from src.app.workline.models.workline import LineType, WorkLine
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

        async def replay_once() -> int:
            async with session_factory() as db:
                result = await RuntimeInboxService(audit_service=AuditLogService()).replay_from_dead_letter(
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

        async with session_factory() as db:
            replay_rows = (
                (
                    await db.execute(
                        select(RuntimeInbox).where(
                            RuntimeInbox.source_event_id == f"replay:{source_id}:concurrent-request"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(replay_rows) == 1
            persisted_source = await db.get(RuntimeInbox, source_id)
            assert persisted_source is not None and persisted_source.status == "DEAD_LETTER"
            audits = (await db.execute(select(AuditLog).where(AuditLog.object_id == str(source_id)))).scalars().all()
            assert [audit.action for audit in audits].count("manual_replay") == 1
            assert [audit.action for audit in audits].count("manual_replay_conflict") == 0
            audit = audits[0]
            assert audit.status == OperaStatus.SUCCESS and audit.code == "200"
            assert audit.args is not None
            assert audit.args["replay_source_event_id"] == f"replay:{source_id}:concurrent-request"
            assert audit.args["replay_payload_hash"] == replay_rows[0].payload_hash
            assert audit.args["actor"] == "integration"
            assert "original_payload" not in audit.args and "reason" not in audit.args

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_same_identity_different_hash_has_one_success_and_one_conflict() -> None:
    """并发同 identity 异 canonical hash 必须保留一行，并各写一次成功/冲突审计。"""

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

        async def replay_once(reason: str) -> tuple[str, int | None]:
            async with session_factory() as db:
                try:
                    result = await RuntimeInboxService(audit_service=AuditLogService()).replay_from_dead_letter(
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
            audits = (await db.execute(select(AuditLog).where(AuditLog.object_id == str(source_id)))).scalars().all()
            by_action = {audit.action: audit for audit in audits}
            assert [audit.action for audit in audits].count("manual_replay") == 1
            assert [audit.action for audit in audits].count("manual_replay_conflict") == 1

            success = by_action["manual_replay"]
            assert success.status == OperaStatus.SUCCESS and success.code == "200"
            assert success.args is not None
            assert success.args["replay_source_event_id"] == f"replay:{source_id}:same-identity"
            assert success.args["replay_payload_hash"] == replay_rows[0].payload_hash
            assert success.args["actor"] == "integration"
            assert "original_payload" not in success.args and "reason" not in success.args

            conflict = by_action["manual_replay_conflict"]
            assert conflict.status == OperaStatus.FAIL and conflict.code == "409"
            assert conflict.args is not None
            assert conflict.args["source_event_id"] == f"replay:{source_id}:same-identity"
            assert conflict.args["existing_payload_hash"] == replay_rows[0].payload_hash
            assert conflict.args["incoming_payload_hash"] != replay_rows[0].payload_hash
            assert conflict.args["actor"] == "integration"
            assert "original_payload" not in conflict.args and "reason" not in conflict.args

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_waits_for_session_lock_and_rejects_new_pending_reconciliation() -> None:
    """双会话竞态必须服从 Session→WorkLine 锁序，锁后新 PENDING 不得放行 replay。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            workline = WorkLine(
                line_code="IT-REPLAY-LOCK-WORKLINE",
                line_name="Replay Lock Workline",
                line_type=LineType.AUTO,
                is_active=True,
            )
            db.add(workline)
            await db.flush()
            session = WorklineSession(
                session_code="IT-REPLAY-LOCK-SESSION",
                workline_id=workline.id,
                plugin_key="test",
                status=SessionStatus.RUNNING,
            )
            db.add(session)
            await db.flush()
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-lock-source",
                payload_hash="it-replay-lock-hash",
                payload_json={"event_type": "SESSION_RESUME", "data": {"session_id": session.id}},
                payload_schema_version=1,
                workline_id=workline.id,
                workline_session_id=session.id,
                status="DEAD_LETTER",
                claim_bucket_key=f"session:{session.id}",
                received_at=1_700_000_000_000,
                failed_at=1_700_000_000_001,
            )
            db.add(source)
            await db.commit()
            source_id = source.id
            session_id = session.id
        assert source_id is not None and session_id is not None

        updater_holds_lock = asyncio.Event()
        replay_attempted_lock = asyncio.Event()
        allow_updater_commit = asyncio.Event()

        class _SignalingSessionRepository(WorklineSessionRepository):
            async def get_for_update(self, db: AsyncSession, locked_session_id: int) -> WorklineSession | None:
                replay_attempted_lock.set()
                return await super().get_for_update(db, locked_session_id)

        async def mark_reconciliation_pending() -> None:
            async with session_factory() as db:
                locked = await WorklineSessionRepository().get_for_update(db, session_id)
                assert locked is not None
                locked.reconciliation_state = RuntimeReconciliationState.PENDING
                await db.flush()
                updater_holds_lock.set()
                await allow_updater_commit.wait()
                await db.commit()

        updater = asyncio.create_task(mark_reconciliation_pending())
        await updater_holds_lock.wait()
        async with session_factory() as db:
            service = WorklineOperationService(session_repo=_SignalingSessionRepository())
            replay_task = asyncio.create_task(
                service.replay_inbox(
                    db,
                    inbox_id=source_id,
                    request_id="race-pending",
                    actor="integration",
                    reason="must observe pending",
                    auto_commit=False,
                )
            )
            await asyncio.wait_for(replay_attempted_lock.wait(), timeout=1)
            await asyncio.sleep(0.05)
            assert not replay_task.done()
            allow_updater_commit.set()
            try:
                await replay_task
            except RuntimeInboxReplayNotAllowed as exc:
                assert exc.reason_code == "SOURCE_RECONCILIATION_PENDING"
            else:
                raise AssertionError("pending reconciliation must reject replay")
            await db.rollback()
        await updater

    asyncio.run(with_temporary_runtime_database(scenario))
