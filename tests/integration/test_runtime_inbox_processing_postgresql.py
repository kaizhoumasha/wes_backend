"""RuntimeInbox 生产三阶段链路的 PostgreSQL heavy integration 测试。

本文件必须显式运行。数据库由既有 migration 测试 harness 创建为
``wes_tmp_runtime_inbox_`` 前缀的临时库，并在测试结束后强制删除。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorService
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    RuntimeInboxOrchestratorDelegate,
)
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.sys.models import SystemOutbox
from src.app.workline.models.workline import LineType, WorkLine
from tests.integration.test_runtime_inbox_migration_postgresql import _run_alembic, _temporary_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@asynccontextmanager
async def _noop_session_lock(_lock_key: str):
    """仅替换外部锁基础设施；持久化、编排和 write-back 均使用生产实现。"""

    yield


def _production_orchestrator_factory(**_kwargs: object) -> OrchestratorService:
    return OrchestratorService(lock_provider=_noop_session_lock)


@dataclass(frozen=True, slots=True)
class _SeededScanFlow:
    inbox_id: int
    session_id: int
    arm_id: int


class _SimulatedWorkerCrash(BaseException):
    """模拟 write-back effects 已执行、终态更新前进程被强制终止。"""


class _CrashBeforeTerminalService(RuntimeInboxService):
    async def mark_processed(self, db: AsyncSession, *, inbox_id: int, lease_token: str) -> bool:
        _ = db, inbox_id, lease_token
        raise _SimulatedWorkerCrash


def _processor(service: RuntimeInboxService) -> RuntimeInboxProcessorBridge:
    return RuntimeInboxProcessorBridge(
        processor_service=RuntimeInboxOrchestratorDelegate(
            orchestrator_factory=_production_orchestrator_factory,
        ),
        inbox_service=service,
    )


async def _seed_scan_flow(db: AsyncSession) -> _SeededScanFlow:
    workline = WorkLine(
        line_code="IT-RUNTIME-INBOX-SCAN",
        line_name="RuntimeInbox Production Flow",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config={"pipeline_input_location": "PIPELINE-IN-IT"},
        is_active=True,
    )
    db.add(workline)
    await db.flush()
    await workline_runtime_status_projection_service.project_ready_after_start(db, workline_id=workline.id)
    scanner = Device(
        device_code="IT-SCANNER-01",
        device_name="Integration Scanner",
        work_line_id=workline.id,
        device_role="ROUGH_SORTER_SCANNER",
        device_status=DeviceStatus.IDLE,
    )
    arm = Device(
        device_code="IT-ARM-01",
        device_name="Integration Input Arm",
        work_line_id=workline.id,
        device_role="ROUGH_SORTER_INPUT_ARM",
        device_status=DeviceStatus.IDLE,
    )
    db.add_all([scanner, arm])
    await db.flush()
    session = WorklineSession(
        session_code="IT-RUNTIME-INBOX-SESSION",
        workline_id=workline.id,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        status=SessionStatus.RUNNING,
        trace_id="it-runtime-inbox-trace",
    )
    db.add_all(
        [
            session,
            ExecutionCorrelation(
                correlation_id="it-runtime-inbox-correlation",
                trace_id="it-runtime-inbox-trace",
                source_event_id="it-runtime-inbox-event",
                business_owner_key="it-runtime-inbox-scan",
            ),
        ]
    )
    await db.flush()
    accepted = await RuntimeInboxService().accept_device_event(
        db,
        device_code=scanner.device_code,
        event_type="SCAN_COMPLETED",
        payload_json={
            "event_type": "SCAN_COMPLETED",
            "canonical_event_type": "SCAN_COMPLETED",
            "device_code": scanner.device_code,
            "data": {
                "session_id": session.id,
                "HHPN": "MAT-IT-001",
                "MfrPN": "VENDOR-IT-001",
                "Qty": "10",
                "DateCode": "20260711",
                "LotCode": "LOT-IT-001",
                "PkgID": "PKG-IT-001",
            },
        },
        trace_id=session.trace_id,
        event_id="it-runtime-inbox-event",
        workline_id=workline.id,
        device_id=scanner.id,
    )
    await db.commit()
    assert accepted.record.id is not None and session.id is not None and arm.id is not None
    return _SeededScanFlow(inbox_id=accepted.record.id, session_id=session.id, arm_id=arm.id)


async def _claim(
    db: AsyncSession,
    service: RuntimeInboxService,
    *,
    token: str,
) -> dict[str, object]:
    claims = await service.claim_for_processing(
        db,
        limit=1,
        processor_token=token,
        stale_after_seconds=60,
    )
    assert len(claims) == 1
    await db.commit()
    return claims[0]


async def _expire_and_recover(db: AsyncSession, service: RuntimeInboxService, *, inbox_id: int) -> None:
    # 用确定性 DB 时间推进替代 sleep，模拟 worker lease 已经过期。
    await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == inbox_id).values(lease_until=0))
    await db.commit()
    assert await service.recover_stale_leases(db, stale_after_seconds=60, limit=1) == 1
    await db.commit()


async def _assert_effects(
    db: AsyncSession,
    seeded: _SeededScanFlow,
    *,
    expected_count: int,
) -> None:
    session = await db.get(WorklineSession, seeded.session_id)
    assert session is not None
    if expected_count:
        assert session.status == SessionStatus.WAITING_DEVICE_RESULT
        assert session.awaiting_device_command_code is not None
        command = await db.scalar(
            select(DeviceCommand).where(DeviceCommand.command_code == session.awaiting_device_command_code)
        )
        assert command is not None and command.device_id == seeded.arm_id
    else:
        assert session.status == SessionStatus.RUNNING
        assert session.awaiting_device_command_code is None
    assert (
        await db.scalar(
            select(func.count()).select_from(DeviceCommand).where(DeviceCommand.workline_id == session.workline_id)
        )
        == expected_count
    )
    assert (
        await db.scalar(
            select(func.count()).select_from(SystemOutbox).where(SystemOutbox.session_id == seeded.session_id)
        )
        == expected_count
    )
    timeline_count = await db.scalar(
        select(func.count()).select_from(WorklineTimeline).where(WorklineTimeline.session_id == seeded.session_id)
    )
    assert (timeline_count or 0) >= expected_count


async def _with_temporary_runtime_database(
    scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]],
) -> None:
    async with _temporary_database() as (_database, database_url):
        _run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await scenario(session_factory)
        finally:
            await engine.dispose()


def test_device_event_persists_claims_and_applies_production_effects_once() -> None:
    """producer→RuntimeInbox→claim→三阶段→effects→fenced terminal 必须真实闭环。"""

    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await _seed_scan_flow(db)
            claim = await _claim(db, service, token="it-runtime-inbox-owner")
            assert claim["id"] == seeded.inbox_id
            result = await _processor(service).process_claimed(db, claim=claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            db.expire_all()
            persisted_inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            assert persisted_inbox is not None and persisted_inbox.status == "PROCESSED"
            await _assert_effects(db, seeded, expected_count=1)

    asyncio.run(_with_temporary_runtime_database(scenario))


def test_claim_crash_recovers_with_new_owner_and_rejects_old_fence() -> None:
    """claim 提交后崩溃：新 owner 收敛，旧 token 不得写终态或重复 effect。"""

    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await _seed_scan_flow(db)
            old_claim = await _claim(db, service, token="it-crash-a-old-owner")

        async with session_factory() as db:
            await _expire_and_recover(db, service, inbox_id=seeded.inbox_id)
            assert not await service.mark_processed(
                db,
                inbox_id=seeded.inbox_id,
                lease_token=str(old_claim["processor_token"]),
            )
            await db.rollback()
            new_claim = await _claim(db, service, token="it-crash-a-new-owner")
            result = await _processor(service).process_claimed(db, claim=new_claim)
            assert result["success"] == 1
            db.expire_all()
            await _assert_effects(db, seeded, expected_count=1)

    asyncio.run(_with_temporary_runtime_database(scenario))


def test_writeback_crash_rolls_back_effects_before_reprocessing_once() -> None:
    """effects 后、终态前崩溃必须整事务回滚；恢复重跑后只落一次副作用。"""

    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        real_service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await _seed_scan_flow(db)
            claim = await _claim(db, real_service, token="it-crash-b-old-owner")

        with pytest.raises(_SimulatedWorkerCrash):
            async with session_factory() as crashed_db:
                await _processor(_CrashBeforeTerminalService()).process_claimed(crashed_db, claim=claim)

        async with session_factory() as db:
            await _assert_effects(db, seeded, expected_count=0)
            persisted_inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            assert persisted_inbox is not None and persisted_inbox.status == "PROCESSING"
            await _expire_and_recover(db, real_service, inbox_id=seeded.inbox_id)
            new_claim = await _claim(db, real_service, token="it-crash-b-new-owner")
            result = await _processor(real_service).process_claimed(db, claim=new_claim)
            assert result["success"] == 1
            db.expire_all()
            await _assert_effects(db, seeded, expected_count=1)

    asyncio.run(_with_temporary_runtime_database(scenario))
