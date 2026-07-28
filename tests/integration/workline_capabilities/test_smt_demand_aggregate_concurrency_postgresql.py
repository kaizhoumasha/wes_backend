"""同一 SMT demand 的不同 source item 并发聚合 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.command import CommandResult, CommandStatus
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
    SmtInboundHandoffService,
    smt_inbound_handoff_service,
)
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

if TYPE_CHECKING:
    import pytest


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


def test_stale_public_claim_and_real_correlation_share_item_then_demand_lock_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with session_factory() as seed_db:
                workline, _device, demand, source_item = await _seed_source_pick(seed_db)

            route_entered = asyncio.Event()
            release_route = asyncio.Event()
            slow_item_lock_requested = asyncio.Event()
            correlation_item_locked = asyncio.Event()
            release_correlation_item_lock = asyncio.Event()

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

            slow_service = SmtInboundHandoffService(route_service=_SlowRouteService())
            original_slow_item_lock = slow_service.repository.lock_source_item_by_id

            async def signal_slow_item_lock(db: AsyncSession, *, source_item_id: int) -> object:
                slow_item_lock_requested.set()
                return await original_slow_item_lock(db, source_item_id=source_item_id)

            monkeypatch.setattr(slow_service.repository, "lock_source_item_by_id", signal_slow_item_lock)

            async def slow_claim() -> str:
                async with session_factory() as db:
                    await db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                    result = await slow_service.claim_next_source_item(
                        db,
                        demand_id=demand.id,
                        trace_id=demand.trace_id,
                    )
                    await db.commit()
                    return result.kind

            slow_task = asyncio.create_task(slow_claim())
            await route_entered.wait()
            async with session_factory() as fast_claim_db:
                fast_service = SmtInboundHandoffService(
                    route_service=_SelectedRouteService(
                        workline_id=workline.id,
                        workline_code=workline.line_code,
                    )
                )
                fast_result = await fast_service.claim_next_source_item(
                    fast_claim_db,
                    demand_id=demand.id,
                    trace_id=demand.trace_id,
                )
                assert fast_result.kind == "CLAIMED"
                await fast_claim_db.commit()

            original_correlation_item_lock = smt_inbound_handoff_service.repository.get_source_item_for_update

            async def hold_correlation_after_item_lock(db: AsyncSession, source_item_id: int) -> object:
                item = await original_correlation_item_lock(db, source_item_id)
                if source_item_id == source_item.id:
                    correlation_item_locked.set()
                    await release_correlation_item_lock.wait()
                return item

            monkeypatch.setattr(
                smt_inbound_handoff_service.repository,
                "get_source_item_for_update",
                hold_correlation_after_item_lock,
            )

            async def correlate_from_public_runtime_inbox_entry() -> dict[str, int]:
                async with session_factory() as db:
                    await db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                    [claim] = await RuntimeInboxService().claim_for_processing(
                        db,
                        limit=1,
                        processor_token="pg-stale-claim-correlation",
                        stale_after_seconds=60,
                    )
                    await db.commit()
                    await db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                    processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
                        db,
                        claim=claim,
                    )
                    await db.commit()
                    return processed

            correlation_task = asyncio.create_task(correlate_from_public_runtime_inbox_entry())
            await asyncio.wait_for(correlation_item_locked.wait(), timeout=5)
            release_route.set()
            # 两种锁序都会请求 item；旧 demand→item 在该点已经持有 demand，
            # 当前 item→demand 则只会等待 correlation 释放 item。
            await asyncio.wait_for(slow_item_lock_requested.wait(), timeout=5)
            release_correlation_item_lock.set()
            processed, slow_kind = await asyncio.wait_for(
                asyncio.gather(correlation_task, slow_task),
                timeout=5,
            )
            assert processed["success"] == 1
            assert slow_kind == "RETRY"

            async with session_factory() as reopened_db:
                reopened_item = await reopened_db.get(SmtInboundHandoffSourceItem, source_item.id)
                reopened_demand = await reopened_db.get(SmtInboundHandoffDemand, demand.id)
                assert reopened_item is not None and reopened_demand is not None
                assert reopened_item.status == SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING
                assert reopened_item.source_pick_command_id is not None
                assert reopened_item.source_pick_inbox_id == fast_result.inbox.id
                assert reopened_item.handoff_demand_id == reopened_demand.id
                assert reopened_demand.status == SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING
            await engine.dispose()

    asyncio.run(scenario())


def test_duplicate_public_manual_retry_fences_attempt_and_preserves_parent_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            demand_id, retry_item_id, held_item_id = await _seed_demand(session_factory)
            async with session_factory() as seed_db:
                demand = await seed_db.get(SmtInboundHandoffDemand, demand_id)
                retry_item = await seed_db.get(SmtInboundHandoffSourceItem, retry_item_id)
                held_item = await seed_db.get(SmtInboundHandoffSourceItem, held_item_id)
                assert demand is not None and retry_item is not None and held_item is not None
                retry_item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
                retry_item.claim_attempt_no = 4
                retry_item.failure_code = "SOURCE_PICK_COMMAND_NOT_CREATED"
                retry_item.failure_message = "允许人工重试"
                held_item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
                held_item.failure_code = "SIBLING_SOURCE_HOLD"
                held_item.failure_message = "同 demand 另一 source item 仍需人工处理"
                demand.status = SmtInboundHandoffDemandStatus.MANUAL_HOLD
                demand.failure_code = held_item.failure_code
                demand.failure_message = held_item.failure_message
                seed_db.add_all([demand, retry_item, held_item])
                await seed_db.commit()

            first_item_locked = asyncio.Event()
            release_first_retry = asyncio.Event()
            first_service = SmtInboundHandoffService()
            original_first_item_lock = first_service.repository.get_source_item_for_update

            async def hold_first_retry_item_lock(db: AsyncSession, source_item_id: int) -> object:
                item = await original_first_item_lock(db, source_item_id)
                if source_item_id == retry_item_id:
                    first_item_locked.set()
                    await release_first_retry.wait()
                return item

            monkeypatch.setattr(
                first_service.repository,
                "get_source_item_for_update",
                hold_first_retry_item_lock,
            )

            async def retry(service: SmtInboundHandoffService) -> tuple[str, object]:
                async with session_factory() as db:
                    await db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                    try:
                        result = await service.retry_source_pick_action(db, source_item_id=retry_item_id)
                    except ValueError as exc:
                        await db.rollback()
                        return "rejected", str(exc)
                    await db.commit()
                    return "success", result

            first_task = asyncio.create_task(retry(first_service))
            await asyncio.wait_for(first_item_locked.wait(), timeout=5)
            second_task = asyncio.create_task(retry(SmtInboundHandoffService()))
            await asyncio.sleep(0.1)
            assert not second_task.done()
            release_first_retry.set()
            outcomes = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)
            assert [kind for kind, _result in outcomes].count("success") == 1
            assert [kind for kind, _result in outcomes].count("rejected") == 1
            rejected_message = next(str(result) for kind, result in outcomes if kind == "rejected")
            assert "当前状态不可重试" in rejected_message

            async with session_factory() as reopened_db:
                demand = await reopened_db.get(SmtInboundHandoffDemand, demand_id)
                retry_item = await reopened_db.get(SmtInboundHandoffSourceItem, retry_item_id)
                held_item = await reopened_db.get(SmtInboundHandoffSourceItem, held_item_id)
                assert demand is not None and retry_item is not None and held_item is not None
                assert retry_item.status == SmtInboundHandoffSourceItemStatus.READY
                assert retry_item.claim_attempt_no == 5
                assert held_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
                assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
                assert demand.failure_code == held_item.failure_code
                assert demand.failure_message == held_item.failure_message
            await engine.dispose()

    asyncio.run(scenario())


def test_unassociated_completed_failed_recovery_commits_correlation_and_manual_hold() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with session_factory() as seed_db:
                _service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(seed_db)
                source_item_id = source_item.id
                demand_id = source_item.handoff_demand_id
                command_id = command.id
                command_code = command.command_code
                inbox_id = source_inbox.id
                command.status = CommandStatus.COMPLETED
                command.result = CommandResult.FAILED
                source_item.source_pick_command_id = None
                source_item.source_pick_command_code = None
                source_item.source_pick_dispatch_key = None
                seed_db.add_all([command, source_item])
                await seed_db.commit()
                await seed_db.execute(
                    update(SmtInboundHandoffSourceItem)
                    .where(SmtInboundHandoffSourceItem.id == source_item_id)
                    .values(updated_at=timezone.now_for_db() - timedelta(minutes=10))
                )
                await seed_db.commit()

            async with session_factory() as recovery_db:
                summary = await SmtInboundHandoffService().scan_smt_inbound_handoff_demands_batch(
                    recovery_db,
                    scan_limit=0,
                    recovery_limit=10,
                    claim_limit=0,
                    stale_after_seconds=0,
                )
                await recovery_db.commit()
                assert summary["manual_hold"] == 1
                assert summary["recovery_errors"] == 0

            async with session_factory() as reopened_db:
                item = await reopened_db.get(SmtInboundHandoffSourceItem, source_item_id)
                demand = await reopened_db.get(SmtInboundHandoffDemand, demand_id)
                assert item is not None and demand is not None
                assert item.source_pick_inbox_id == inbox_id
                assert item.source_pick_command_id == command_id
                assert item.source_pick_command_code == command_code
                assert item.source_pick_dispatch_key == f"device-command:{command_code}"
                assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
                assert item.failure_code == "SOURCE_PICK_COMMAND_NOT_CREATED"
                assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
                assert demand.failure_code == item.failure_code
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
