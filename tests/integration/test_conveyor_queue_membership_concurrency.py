"""PostgreSQL concurrency checks for ConveyorQueueMembership writer."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, func, select

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
    ConveyorQueueMembershipWriterService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _delete_memberships(session_factory: async_sessionmaker[AsyncSession], *, workline_id: int) -> None:
    async with session_factory() as db:
        await db.execute(delete(ConveyorQueueMembership).where(ConveyorQueueMembership.workline_id == workline_id))
        await db.commit()


async def _membership_count(db: AsyncSession, *, workline_id: int, bin_code: str) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(ConveyorQueueMembership)
        .where(
            ConveyorQueueMembership.workline_id == workline_id,
            ConveyorQueueMembership.bin_code == bin_code,
            ConveyorQueueMembership.membership_status == "ACTIVE",
        )
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_conveyor_queue_writer_rereads_existing_after_real_postgres_unique_race(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """真实 PostgreSQL 并发插入撞 ACTIVE partial unique index 后必须重读 existing。"""

    workline_id = 70_000_000 + uuid.uuid4().int % 1_000_000
    bin_code = f"BIN-PG-RACE-{uuid.uuid4().hex[:8]}"
    service = ConveyorQueueMembershipWriterService()

    await _delete_memberships(integration_session_factory, workline_id=workline_id)
    try:
        async with integration_session_factory() as holder:
            existing = ConveyorQueueMembership(
                workline_id=workline_id,
                conveyor_code="CV-PG",
                queue_code="Q-IN",
                queue_role="ENTRY_SCAN",
                bin_code=bin_code,
                membership_status="ACTIVE",
                entered_at=1_700_000_000_000,
                evidence_json={"source_event_id": "evt-holder"},
            )
            holder.add(existing)
            await holder.flush()

            async def contender_write():
                async with integration_session_factory() as contender:
                    return await service.write_active(
                        contender,
                        workline_id=workline_id,
                        conveyor_code="CV-PG",
                        queue_code="Q-IN",
                        queue_role="ENTRY_SCAN",
                        bin_code=bin_code,
                        declared_queue_codes={"Q-IN"},
                        evidence_json={"source_event_id": "evt-contender"},
                        auto_commit=True,
                    )

            contender_task = asyncio.create_task(contender_write())
            await asyncio.sleep(0.2)
            await holder.commit()

            result = await asyncio.wait_for(contender_task, timeout=5)

        async with integration_session_factory() as verify:
            assert await _membership_count(verify, workline_id=workline_id, bin_code=bin_code) == 1
        assert result.created is False
        assert result.diagnostics.reused_existing_after_integrity_conflict is True
        assert result.membership.bin_code == bin_code
    finally:
        await _delete_memberships(integration_session_factory, workline_id=workline_id)
