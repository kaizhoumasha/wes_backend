"""同一 SMT demand 的不同 source item 并发聚合 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.command import CommandResult, CommandStatus
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.utils.timezone import timezone
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.workline_runtime.test_smt_generated_source_pick_lifecycle import (
    _claim_and_process_source_pick,
    _NoopQueueGateway,
    _seed_source_pick,
    _SelectedRouteService,
)


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


def test_stale_public_claim_and_real_correlation_share_item_then_demand_lock_order() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with session_factory() as seed_db:
                workline, _device, demand, _item = await _seed_source_pick(seed_db)

            route_entered = asyncio.Event()
            release_route = asyncio.Event()

            class _SlowRouteService:
                async def resolve_route(self, _db: object, **_kwargs: object) -> SimpleNamespace:
                    route_entered.set()
                    await release_route.wait()
                    return SimpleNamespace(
                        kind="SELECTED",
                        selected_workline_id=workline.id,
                        selected_workline_code=workline.line_code,
                        route_evidence={
                            "source_rack_position_code": "SOURCE_STATION_A",
                            "target_rack_position_code": "TARGET_STATION",
                            "manifest_contract_version": workline.contract_version,
                        },
                    )

            async def slow_claim() -> str:
                async with session_factory() as db:
                    result = await SmtInboundHandoffService(route_service=_SlowRouteService()).claim_next_source_item(
                        db,
                        demand_id=demand.id,
                        trace_id=demand.trace_id,
                    )
                    await db.commit()
                    return result.kind

            slow_task = asyncio.create_task(slow_claim())
            await route_entered.wait()
            async with session_factory() as db:
                fast_service = SmtInboundHandoffService(
                    route_service=_SelectedRouteService(
                        workline_id=workline.id,
                        workline_code=workline.line_code,
                    )
                )
                fast_result = await fast_service.claim_next_source_item(
                    db,
                    demand_id=demand.id,
                    trace_id=demand.trace_id,
                )
                assert fast_result.kind == "CLAIMED"
                await db.commit()
                [claim] = await RuntimeInboxService().claim_for_processing(
                    db,
                    limit=1,
                    processor_token="pg-stale-claim-correlation",
                    stale_after_seconds=60,
                )
                await db.commit()
                processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
                    db,
                    claim=claim,
                )
                assert processed["success"] == 1
                await db.commit()

            release_route.set()
            assert await asyncio.wait_for(slow_task, timeout=5) == "RETRY"
            await engine.dispose()

    asyncio.run(scenario())


def test_real_recovery_and_public_manual_retry_serialize_and_preserve_aggregate() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with session_factory() as seed_db:
                _service, source_item, _inbox, command, _outbox = await _claim_and_process_source_pick(seed_db)
                command.status = CommandStatus.COMPLETED
                command.result = CommandResult.FAILED
                retry_item = SmtInboundHandoffSourceItem(
                    handoff_demand_id=source_item.handoff_demand_id,
                    item_key="concurrent-manual-retry",
                    bin_code="BIN-RETRY",
                    bin_cell_index=2,
                    bin_cell_code="CELL-RETRY",
                    material_identity_key="MAT-RETRY",
                    status=SmtInboundHandoffSourceItemStatus.MANUAL_HOLD,
                    claim_attempt_no=4,
                    failure_code="SOURCE_PICK_COMMAND_NOT_CREATED",
                    failure_message="等待人工重试",
                )
                seed_db.add_all([command, retry_item])
                await seed_db.commit()
                await seed_db.execute(
                    update(SmtInboundHandoffSourceItem)
                    .where(SmtInboundHandoffSourceItem.id == source_item.id)
                    .values(updated_at=timezone.now_for_db() - timedelta(minutes=10))
                )
                await seed_db.commit()
                demand_id = source_item.handoff_demand_id
                source_item_id = source_item.id
                retry_item_id = retry_item.id

            recovery_holds_parent = asyncio.Event()
            release_recovery = asyncio.Event()

            class _BlockingRecoveryService(SmtInboundHandoffService):
                async def _manual_hold_source_pick_recovery(self, db: AsyncSession, **kwargs: object) -> None:
                    await super()._manual_hold_source_pick_recovery(db, **kwargs)
                    recovery_holds_parent.set()
                    await release_recovery.wait()

            async def recover() -> dict[str, int]:
                async with session_factory() as db:
                    summary = await _BlockingRecoveryService().scan_smt_inbound_handoff_demands_batch(
                        db,
                        scan_limit=0,
                        recovery_limit=10,
                        claim_limit=0,
                        stale_after_seconds=0,
                    )
                    await db.commit()
                    return summary

            async def retry() -> dict[str, object]:
                async with session_factory() as db:
                    result = await SmtInboundHandoffService().retry_source_pick_action(
                        db,
                        source_item_id=retry_item_id,
                    )
                    await db.commit()
                    return result

            recovery_task = asyncio.create_task(recover())
            await recovery_holds_parent.wait()
            retry_task = asyncio.create_task(retry())
            await asyncio.sleep(0.1)
            assert not retry_task.done()
            release_recovery.set()
            recovery_summary, retry_result = await asyncio.gather(recovery_task, retry_task)
            assert recovery_summary["manual_hold"] == 1
            assert retry_result["status"] == SmtInboundHandoffSourceItemStatus.READY.value

            async with session_factory() as db:
                demand = await db.get(SmtInboundHandoffDemand, demand_id)
                recovered_item = await db.get(SmtInboundHandoffSourceItem, source_item_id)
                retried_item = await db.get(SmtInboundHandoffSourceItem, retry_item_id)
                assert demand is not None and recovered_item is not None and retried_item is not None
                assert recovered_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
                assert retried_item.status == SmtInboundHandoffSourceItemStatus.READY
                assert retried_item.claim_attempt_no == 5
                assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
                assert demand.failure_code == recovered_item.failure_code
            await engine.dispose()

    asyncio.run(scenario())
