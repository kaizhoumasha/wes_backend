"""RuntimeInbox worker 崩溃后的 PostgreSQL 租约围栏与事务恢复证据。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import update

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from tests.support.runtime_inbox_processing_postgresql import (
    RecordingTaskQueueGateway,
    assert_effects,
    assert_processed_terminal,
    claim,
    expire_and_recover,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class _SimulatedWorkerCrash(BaseException):
    """模拟 write-back effects 已执行、终态更新前进程被强制终止。"""


class _CrashBeforeTerminalService(RuntimeInboxService):
    async def mark_processed(self, db: AsyncSession, *, inbox_id: int, lease_token: str) -> bool:
        _ = db, inbox_id, lease_token
        raise _SimulatedWorkerCrash


class _AuditService:
    async def create_audit_log(self, *_args: Any, **_kwargs: Any) -> object:
        return SimpleNamespace(id=1)


def test_claim_crash_recovers_with_new_owner_and_rejects_old_fence() -> None:
    """claim 提交后崩溃：新 owner 收敛，旧 token 不得写终态或重复 effect。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            old_claim = await claim(db, service, token="it-crash-a-old-owner")

        async with session_factory() as db:
            await expire_and_recover(db, service, inbox_id=seeded.inbox_id)
            old_token = str(old_claim["processor_token"])
            assert not await service.mark_processed(db, inbox_id=seeded.inbox_id, lease_token=old_token)
            await db.rollback()
            new_claim = await claim(db, service, token="it-crash-a-new-owner")
            assert new_claim["processor_token"] != old_token
            assert queue_gateway.outbox_enqueues == []
            result = await processor(service).process_claimed(db, claim=new_claim)
            if result["resource_wait"]:
                await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == seeded.inbox_id).values(next_retry_at=0))
                await db.commit()
                refreshed_claim = await claim(db, service, token="it-crash-a-refreshed-owner")
                result = await processor(service).process_claimed(db, claim=refreshed_claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)
            await assert_effects(db, seeded, expected_count=1)
            # OUTBOX_ASYNC 只在本事务持久化 durable acceptance；远端派发由独立 dispatcher 消费。
            assert queue_gateway.outbox_enqueues == []

    asyncio.run(with_temporary_runtime_database(scenario))


def test_writeback_crash_rolls_back_effects_before_reprocessing_once() -> None:
    """effects 后、终态前崩溃必须整事务回滚；恢复重跑后只落一次副作用。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        real_service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            old_claim = await claim(db, real_service, token="it-crash-b-old-owner")

        with pytest.raises(_SimulatedWorkerCrash):
            async with session_factory() as crashed_db:
                await processor(_CrashBeforeTerminalService()).process_claimed(crashed_db, claim=old_claim)

        assert queue_gateway.outbox_enqueues == []

        async with session_factory() as db:
            await assert_effects(db, seeded, expected_count=0)
            persisted_inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            assert persisted_inbox is not None and persisted_inbox.status == "PROCESSING"
            await expire_and_recover(db, real_service, inbox_id=seeded.inbox_id)
            old_token = str(old_claim["processor_token"])
            new_claim = await claim(db, real_service, token="it-crash-b-new-owner")
            assert new_claim["processor_token"] != old_token
            assert not await real_service.mark_processed(db, inbox_id=seeded.inbox_id, lease_token=old_token)
            await db.rollback()
            result = await processor(real_service).process_claimed(db, claim=new_claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)
            await assert_effects(db, seeded, expected_count=1)
            assert queue_gateway.outbox_enqueues == []

    asyncio.run(with_temporary_runtime_database(scenario))


def test_replay_request_recovery_rejects_old_fence_and_applies_effect_once() -> None:
    """REPLAY_REQUEST 解包后仍复用原 claim fencing 与 effect-once 事务边界。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService(audit_service=_AuditService())
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            source_claim = await claim(db, service, token="it-replay-source-owner")
            source_result = await processor(service).process_claimed(db, claim=source_claim)
            assert source_result == {
                "processed": 1,
                "success": 1,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }
            await assert_effects(db, seeded, expected_count=1)
            await db.execute(
                update(RuntimeInbox)
                .where(RuntimeInbox.id == seeded.inbox_id)
                .values(status="DEAD_LETTER", failed_at=1_700_000_000_001)
            )
            await db.commit()
            replay = await service.replay_from_dead_letter(
                db,
                source_inbox_id=seeded.inbox_id,
                request_id="it-replay-fencing",
                actor="integration",
                reason="verify replay fencing",
            )
            await db.commit()
            assert replay.replay_record.kind == "REPLAY_REQUEST"
            assert replay.replay_record.id is not None
            replay_seeded = replace(seeded, inbox_id=replay.replay_record.id)
            old_claim = await claim(db, service, token="it-replay-old-owner")
            assert old_claim["id"] == replay.replay_record.id

        async with session_factory() as db:
            await expire_and_recover(db, service, inbox_id=replay_seeded.inbox_id)
            old_token = str(old_claim["processor_token"])
            assert not await service.mark_processed(db, inbox_id=replay_seeded.inbox_id, lease_token=old_token)
            await db.rollback()
            new_claim = await claim(db, service, token="it-replay-new-owner")
            assert new_claim["processor_token"] != old_token
            result = await processor(service).process_claimed(db, claim=new_claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=replay_seeded.inbox_id)
            await assert_effects(db, replay_seeded, expected_count=1)
            source = await db.get(RuntimeInbox, seeded.inbox_id)
            assert source is not None and source.status == "DEAD_LETTER"
            assert queue_gateway.outbox_enqueues == []

    asyncio.run(with_temporary_runtime_database(scenario))
