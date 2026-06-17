"""PostgreSQL gated tests for SMT inbound handoff source item claim."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed_ready_items(db: AsyncSession, *, test_prefix: str) -> list[int]:
    demand = SmtInboundHandoffDemand(
        demand_key=f"{test_prefix}:smt-inbound-handoff",
        rack_release_id=f"{test_prefix}:release",
        single_layer_rack_code=f"{test_prefix}:rack",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        trace_id=f"{test_prefix}:claim",
        bin_snapshots_json={"bins": []},
    )
    db.add(demand)
    await db.flush()
    items = [
        SmtInboundHandoffSourceItem(
            handoff_demand_id=demand.id,
            item_key=f"{test_prefix}:item:{index}",
            bin_code=f"{test_prefix}:BIN-{index}",
            bin_cell_code=f"A0{index}",
            material_identity_key=f"{test_prefix}:MAT-{index}",
            pkg_code=f"{test_prefix}:PKG-{index}",
            status=SmtInboundHandoffSourceItemStatus.READY,
        )
        for index in (1, 2)
    ]
    db.add_all(items)
    await db.commit()
    return [int(item.id) for item in items if isinstance(item.id, int)]


async def _seed_waiting_and_ready_items(db: AsyncSession, *, test_prefix: str) -> tuple[int, int]:
    waiting_demand = SmtInboundHandoffDemand(
        demand_key=f"{test_prefix}:waiting:smt-inbound-handoff",
        rack_release_id=f"{test_prefix}:waiting:release",
        single_layer_rack_code=f"{test_prefix}:waiting:rack",
        status=SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE,
        trace_id=f"{test_prefix}:waiting:claim",
        bin_snapshots_json={"bins": []},
    )
    ready_demand = SmtInboundHandoffDemand(
        demand_key=f"{test_prefix}:ready:smt-inbound-handoff",
        rack_release_id=f"{test_prefix}:ready:release",
        single_layer_rack_code=f"{test_prefix}:ready:rack",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        trace_id=f"{test_prefix}:ready:claim",
        bin_snapshots_json={"bins": []},
    )
    db.add_all([waiting_demand, ready_demand])
    await db.flush()
    waiting_item = SmtInboundHandoffSourceItem(
        handoff_demand_id=waiting_demand.id,
        item_key=f"{test_prefix}:waiting:item",
        bin_code=f"{test_prefix}:WAITING-BIN",
        bin_cell_code="A01",
        material_identity_key=f"{test_prefix}:WAITING-MAT",
        pkg_code=f"{test_prefix}:WAITING-PKG",
        status=SmtInboundHandoffSourceItemStatus.READY,
    )
    ready_item = SmtInboundHandoffSourceItem(
        handoff_demand_id=ready_demand.id,
        item_key=f"{test_prefix}:ready:item",
        bin_code=f"{test_prefix}:READY-BIN",
        bin_cell_code="A02",
        material_identity_key=f"{test_prefix}:READY-MAT",
        pkg_code=f"{test_prefix}:READY-PKG",
        status=SmtInboundHandoffSourceItemStatus.READY,
    )
    db.add_all([waiting_item, ready_item])
    await db.commit()
    return int(waiting_item.id), int(ready_item.id)


async def _cleanup_handoff_rows(db: AsyncSession, *, test_prefix: str) -> None:
    demand_ids = select(SmtInboundHandoffDemand.id).where(
        SmtInboundHandoffDemand.rack_release_id.like(f"{test_prefix}%")
    )
    await db.execute(
        delete(SmtInboundHandoffSourceItem).where(SmtInboundHandoffSourceItem.handoff_demand_id.in_(demand_ids))
    )
    await db.execute(
        delete(SmtInboundHandoffDemand).where(SmtInboundHandoffDemand.rack_release_id.like(f"{test_prefix}%"))
    )
    await db.commit()


async def _claim_and_hold(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    locked: asyncio.Event,
    release: asyncio.Event,
) -> int | None:
    repository = SmtInboundHandoffRepository()
    async with session_factory() as db:
        transaction = await db.begin()
        try:
            item = await repository.claim_next_ready_source_item(db, now=timezone.now_for_db())
            locked.set()
            await release.wait()
            return None if item is None else int(item.id)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_concurrent_claim_does_not_duplicate_source_item(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        await _seed_ready_items(db, test_prefix=test_prefix)

    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    first_task = asyncio.create_task(
        _claim_and_hold(integration_session_factory, locked=first_locked, release=release_first)
    )
    await first_locked.wait()

    second_locked = asyncio.Event()
    release_second = asyncio.Event()
    second_task = asyncio.create_task(
        _claim_and_hold(integration_session_factory, locked=second_locked, release=release_second)
    )
    await second_locked.wait()

    release_second.set()
    release_first.set()
    first_id, second_id = await asyncio.gather(first_task, second_task)

    async with integration_session_factory() as cleanup_db:
        await _cleanup_handoff_rows(cleanup_db, test_prefix=test_prefix)

    assert first_id is not None
    assert second_id is not None
    assert first_id != second_id


@pytest.mark.asyncio
async def test_claim_next_ready_source_item_skips_waiting_full_box_exchange_demand(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    repository = SmtInboundHandoffRepository()
    async with integration_session_factory() as db:
        waiting_item_id, ready_item_id = await _seed_waiting_and_ready_items(db, test_prefix=test_prefix)

    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            item = await repository.claim_next_ready_source_item(db, now=timezone.now_for_db())
            claimed_id = None if item is None else int(item.id)
        finally:
            await transaction.rollback()

    async with integration_session_factory() as cleanup_db:
        await _cleanup_handoff_rows(cleanup_db, test_prefix=test_prefix)

    assert claimed_id == ready_item_id
    assert claimed_id != waiting_item_id
