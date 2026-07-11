"""RuntimeInbox worker 崩溃后的 PostgreSQL 租约围栏与事务恢复证据。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from tests.support.runtime_inbox_processing_postgresql import (
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


def test_claim_crash_recovers_with_new_owner_and_rejects_old_fence() -> None:
    """claim 提交后崩溃：新 owner 收敛，旧 token 不得写终态或重复 effect。"""

    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
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
            result = await processor(service).process_claimed(db, claim=new_claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)
            await assert_effects(db, seeded, expected_count=1)

    asyncio.run(with_temporary_runtime_database(scenario))


def test_writeback_crash_rolls_back_effects_before_reprocessing_once() -> None:
    """effects 后、终态前崩溃必须整事务回滚；恢复重跑后只落一次副作用。"""

    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        real_service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            old_claim = await claim(db, real_service, token="it-crash-b-old-owner")

        with pytest.raises(_SimulatedWorkerCrash):
            async with session_factory() as crashed_db:
                await processor(_CrashBeforeTerminalService()).process_claimed(crashed_db, claim=old_claim)

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

    asyncio.run(with_temporary_runtime_database(scenario))
