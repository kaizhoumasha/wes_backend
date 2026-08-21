"""RuntimeInbox PostgreSQL 通用处理与崩溃恢复的共享证据夹具。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import patch

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.sys.models import SystemOutbox
from src.app.workline.models.workline import LineType, WorkLine
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class SeededInboxFlow:
    inbox_id: int
    session_id: int
    workline_id: int
    trace_id: str


@dataclass(slots=True)
class RecordingTaskQueueGateway:
    """记录出队唤醒请求，确保 heavy test 不接触真实 Celery broker。"""

    outbox_enqueues: list[tuple[object, int]] = field(default_factory=list)

    def enqueue_outbox(self, *, targets: object, limit: int = 50) -> None:
        self.outbox_enqueues.append((targets, limit))


def processor(service: RuntimeInboxService) -> RuntimeInboxProcessorBridge:
    """构造无嵌入式插件的 RuntimeInbox 生产处理桥接。"""

    return RuntimeInboxProcessorBridge(inbox_service=service)


async def seed_scan_flow(db: AsyncSession) -> SeededInboxFlow:
    """写入无插件的会话、执行锚点和待处理内部事件。"""

    trace_id = "it-runtime-inbox-trace"
    workline = WorkLine(
        line_code="IT-RUNTIME-INBOX-GENERIC",
        line_name="RuntimeInbox Generic Flow",
        line_type=LineType.AUTO,
        is_active=True,
    )
    db.add(workline)
    await db.flush()
    await workline_runtime_status_projection_service.project_ready_after_start(db, workline_id=workline.id)

    session = WorklineSession(
        session_code="IT-RUNTIME-INBOX-SESSION",
        workline_id=workline.id,
        business_key="IT-GENERIC-OBJECT-001",
        status=SessionStatus.RUNNING,
        trace_id=trace_id,
    )
    execution_session = ExecutionSession(workline_id=workline.id, state="RUNNING")
    db.add_all([session, execution_session])
    await db.flush()
    assert session.id is not None and execution_session.id is not None

    correlation = ExecutionCorrelation(
        correlation_id=f"workline-session:{session.session_code}",
        execution_session_id=execution_session.id,
        trace_id=trace_id,
        source_event_id="it-runtime-inbox-event",
        business_owner_key=session.business_key,
    )
    db.add(correlation)
    await db.flush()
    db.add(
        ExecutionWorkItem(
            execution_session_id=execution_session.id,
            correlation_id=correlation.correlation_id,
            object_type="material",
            object_key="IT-GENERIC-OBJECT-001",
            current_step="INGRESS",
        )
    )
    await db.flush()

    accepted = await RuntimeInboxService().accept_internal_event(
        db,
        event_type="GENERIC_INPUT_RECEIVED",
        payload_json={
            "event_type": "GENERIC_INPUT_RECEIVED",
            "data": {"session_id": session.id, "barcode": "IT-GENERIC-OBJECT-001"},
        },
        trace_id=trace_id,
        event_id="it-runtime-inbox-event",
        workline_id=workline.id,
    )
    accepted.record.workline_session_id = session.id
    accepted.record.execution_session_id = execution_session.id
    accepted.record.correlation_id = correlation.correlation_id
    await db.commit()
    assert accepted.record.id is not None and workline.id is not None
    return SeededInboxFlow(
        inbox_id=int(accepted.record.id),
        session_id=int(session.id),
        workline_id=int(workline.id),
        trace_id=trace_id,
    )


async def claim(db: AsyncSession, service: RuntimeInboxService, *, token: str) -> dict[str, object]:
    claims = await service.claim_for_processing(db, limit=1, processor_token=token, stale_after_seconds=60)
    assert len(claims) == 1
    await db.commit()
    return claims[0]


async def expire_and_recover(db: AsyncSession, service: RuntimeInboxService, *, inbox_id: int) -> None:
    await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == inbox_id).values(lease_until=0))
    await db.commit()
    assert await service.recover_stale_leases(db, stale_after_seconds=60, limit=1) == 1
    await db.commit()


async def assert_effects(db: AsyncSession, seeded: SeededInboxFlow, *, expected_count: int) -> None:
    """通用入口不得暗中产生插件决策、设备指令或出站效果。"""

    assert expected_count == 0
    inbox = await db.get(RuntimeInbox, seeded.inbox_id)
    assert inbox is not None and inbox.execution_session_id is not None
    outbox_count = await db.scalar(
        select(func.count()).select_from(SystemOutbox).where(SystemOutbox.session_id == seeded.session_id)
    )
    intent_count = await db.scalar(
        select(func.count())
        .select_from(RuntimeIntentLog)
        .where(RuntimeIntentLog.execution_session_id == inbox.execution_session_id)
    )
    assert outbox_count == 0
    assert intent_count == 0


async def assert_processed_terminal(db: AsyncSession, *, inbox_id: int) -> None:
    db.expire_all()
    inbox = await db.get(RuntimeInbox, inbox_id)
    assert inbox is not None
    assert inbox.status == "PROCESSED"
    assert inbox.processor_token is None
    assert inbox.lease_until is None


async def assert_dead_letter_terminal(db: AsyncSession, *, inbox_id: int, error_code: str) -> None:
    db.expire_all()
    inbox = await db.get(RuntimeInbox, inbox_id)
    assert inbox is not None
    assert inbox.status == "DEAD_LETTER"
    assert inbox.last_error_code == error_code
    assert inbox.processor_token is None
    assert inbox.lease_until is None


async def with_temporary_runtime_database(
    scenario: Callable[[async_sessionmaker[AsyncSession], RecordingTaskQueueGateway], Awaitable[None]],
) -> None:
    template_database = os.environ.get("RUNTIME_INBOX_DATABASE_TEMPLATE") or None
    async with temporary_database(template_database=template_database) as (_database, database_url):
        if template_database is None:
            run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=10,
        )
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        queue_gateway = RecordingTaskQueueGateway()
        try:
            with patch("src.core.task_queue_gateway.task_queue_gateway", queue_gateway):
                await scenario(session_factory, queue_gateway)
        finally:
            await engine.dispose()


__all__ = [
    "RecordingTaskQueueGateway",
    "SeededInboxFlow",
    "assert_effects",
    "assert_processed_terminal",
    "claim",
    "expire_and_recover",
    "processor",
    "seed_scan_flow",
    "with_temporary_runtime_database",
]
