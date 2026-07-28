"""同一 SMT demand 的不同 source item 并发聚合 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database


async def _seed_demand(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    async with session_factory() as db:
        demand = SmtInboundHandoffDemand(
            demand_key="concurrent-demand",
            rack_release_id="concurrent-release",
            single_layer_rack_code="RACK-CONCURRENT",
            status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        )
        db.add(demand)
        await db.flush()
        items = [
            SmtInboundHandoffSourceItem(
                handoff_demand_id=demand.id,
                item_key=f"concurrent-item-{index}",
                bin_code=f"BIN-{index}",
                bin_cell_index=index,
                bin_cell_code=f"CELL-{index}",
                material_identity_key=f"MAT-{index}",
                status=SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
            )
            for index in (1, 2)
        ]
        db.add_all(items)
        await db.commit()
        return demand.id, items[0].id, items[1].id


def test_different_source_items_serialize_on_parent_demand_lock() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            demand_id, first_item_id, second_item_id = await _seed_demand(session_factory)
            first_has_parent = asyncio.Event()
            release_first = asyncio.Event()
            second_has_parent = asyncio.Event()

            async def first_writer() -> None:
                async with session_factory() as db:
                    await SmtInboundHandoffService().repository.lock_source_item_by_id(db, source_item_id=first_item_id)
                    await SmtInboundHandoffService().repository.lock_demand_by_id(db, demand_id=demand_id)
                    first_has_parent.set()
                    await release_first.wait()
                    await db.commit()

            async def second_writer() -> None:
                async with session_factory() as db:
                    await first_has_parent.wait()
                    await SmtInboundHandoffService().repository.lock_source_item_by_id(
                        db, source_item_id=second_item_id
                    )
                    await SmtInboundHandoffService().repository.lock_demand_by_id(db, demand_id=demand_id)
                    second_has_parent.set()
                    await db.commit()

            first_task = asyncio.create_task(first_writer())
            second_task = asyncio.create_task(second_writer())
            await first_has_parent.wait()
            await asyncio.sleep(0.1)
            assert not second_has_parent.is_set()
            release_first.set()
            await asyncio.gather(first_task, second_task)
            assert second_has_parent.is_set()
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_child_outcomes_preserve_parent_manual_hold_failure() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            demand_id, first_item_id, second_item_id = await _seed_demand(session_factory)
            first_has_parent = asyncio.Event()
            release_first = asyncio.Event()

            async def hold_writer() -> None:
                service = SmtInboundHandoffService()
                async with session_factory() as db:
                    item = await service.repository.lock_source_item_by_id(db, source_item_id=first_item_id)
                    demand = await service.repository.lock_demand_by_id(db, demand_id=demand_id)
                    assert item is not None and demand is not None
                    item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
                    item.failure_code = "SOURCE_PICK_COMMAND_NOT_CREATED"
                    item.failure_message = "并发恢复失败"
                    db.add(item)
                    first_has_parent.set()
                    await release_first.wait()
                    await service.recalculate_demand_status(db, demand, reason="concurrent_hold")
                    await db.commit()

            async def success_writer() -> None:
                service = SmtInboundHandoffService()
                async with session_factory() as db:
                    await first_has_parent.wait()
                    item = await service.repository.lock_source_item_by_id(db, source_item_id=second_item_id)
                    demand = await service.repository.lock_demand_by_id(db, demand_id=demand_id)
                    assert item is not None and demand is not None
                    item.status = SmtInboundHandoffSourceItemStatus.PICKED
                    demand.failure_code = None
                    demand.failure_message = None
                    db.add(item)
                    await service.recalculate_demand_status(db, demand, reason="concurrent_success")
                    await db.commit()

            first_task = asyncio.create_task(hold_writer())
            second_task = asyncio.create_task(success_writer())
            await first_has_parent.wait()
            release_first.set()
            await asyncio.gather(first_task, second_task)

            async with session_factory() as db:
                demand = await db.get(SmtInboundHandoffDemand, demand_id)
                assert demand is not None
                assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
                assert demand.failure_code == "SOURCE_PICK_COMMAND_NOT_CREATED"
                assert demand.failure_message == "并发恢复失败"
            await engine.dispose()

    asyncio.run(scenario())
