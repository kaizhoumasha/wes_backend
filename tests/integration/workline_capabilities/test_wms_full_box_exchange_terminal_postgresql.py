"""E11 reject/success 投影、漂移回滚与 statement budget 的真实 PostgreSQL RED。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select

from src.app.resource.models import (
    BinCellOccupancy,
    BinMaterialMount,
    RackBinMount,
    RackBinMountStatus,
    ResourceSourceSystem,
)
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import effect_reducer
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from tests.support.wms_full_box_exchange_postgresql import (
    E11,
    NOW,
    completed_event,
    domain_types,
    prepare_exchange,
    prepare_reservation,
    reject_event,
    reserve_exchange,
    seed_destination_rack,
    seed_exchange_graph,
    seed_selected_empty_bin,
    success_result,
    with_database,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tests.support.wms_full_box_exchange_postgresql import ExchangeGraph


async def _project(
    db: AsyncSession,
    *,
    projector: Any,
    reservation: Any,
    event: Any,
) -> Any:
    reduction = await effect_reducer.reduce(db, event)
    assert reduction is not None and reduction.state_changed is True
    await projector.project_event(
        db,
        operation=WMS_OPERATION_BY_IDENTITY[E11],
        request_payload=reservation.request.model_dump(mode="json"),
        event=event,
        reduction=reduction,
    )
    return reduction


async def _resource_snapshot(db: AsyncSession, *, demand_id: int) -> dict[str, Any]:
    rack_mounts = (
        await db.execute(
            select(
                RackBinMount.rack_code,
                RackBinMount.rack_slot_code,
                RackBinMount.bin_code,
                RackBinMount.mount_status,
                RackBinMount.ended_at,
            ).order_by(RackBinMount.id)
        )
    ).all()
    occupancies = (
        await db.execute(
            select(
                BinCellOccupancy.id,
                BinCellOccupancy.bin_code,
                BinCellOccupancy.occupancy_status,
                BinCellOccupancy.ended_at,
            ).order_by(BinCellOccupancy.id)
        )
    ).all()
    material_mounts = (
        await db.execute(
            select(
                BinMaterialMount.id,
                BinMaterialMount.bin_code,
                BinMaterialMount.pkg_code,
                BinMaterialMount.mount_status,
                BinMaterialMount.wms_confirmation_status,
                BinMaterialMount.wms_inventory_version,
                BinMaterialMount.ended_at,
            ).order_by(BinMaterialMount.id)
        )
    ).all()
    source_items = (
        await db.execute(
            select(
                SmtInboundHandoffSourceItem.id,
                SmtInboundHandoffSourceItem.pkg_code,
                SmtInboundHandoffSourceItem.status,
            )
            .where(SmtInboundHandoffSourceItem.handoff_demand_id == demand_id)
            .order_by(SmtInboundHandoffSourceItem.id)
        )
    ).all()
    return {
        "rack_mounts": [tuple(row) for row in rack_mounts],
        "occupancies": [tuple(row) for row in occupancies],
        "material_mounts": [tuple(row) for row in material_mounts],
        "source_items": [tuple(row) for row in source_items],
    }


async def _prepare_with_empty(
    db: AsyncSession,
    *,
    service: Any,
    projector: Any,
    graph: ExchangeGraph,
    full_box_id: str = "FULL-1",
    empty_box_id: str = "EMPTY-1",
    intent_id: int = 5101,
    prefer_full_box_exchange: bool | None = None,
) -> tuple[Any, RackBinMount]:
    reservation = await prepare_exchange(
        service=service,
        projector=projector,
        db=db,
        graph=graph,
        full_box_id=full_box_id,
        intent_id=intent_id,
        prefer_full_box_exchange=prefer_full_box_exchange,
    )
    empty_mount = await seed_selected_empty_bin(
        db,
        empty_bin_id=empty_box_id,
        source_slot_id="A-01",
    )
    return reservation, empty_mount


@pytest.mark.integration
@pytest.mark.parametrize(
    "event_type",
    [EffectReducerEventType.ASYNC_SUBMIT_REJECTED, EffectReducerEventType.STATUS_REJECTED],
)
def test_e11_real_reducer_projector_reject_preserves_graph_and_owners(
    event_type: EffectReducerEventType,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as setup_db:
            graph = await seed_exchange_graph(setup_db)
            reservation = await prepare_exchange(
                service=service_type(),
                projector=projector,
                db=setup_db,
                graph=graph,
                full_box_id="FULL-1",
                intent_id=5101,
            )
            demand_id = graph.demand.id
            assert demand_id is not None
            before = await _resource_snapshot(setup_db, demand_id=demand_id)
            await setup_db.commit()

        async with session_factory() as terminal_db:
            await _project(
                terminal_db,
                projector=projector,
                reservation=reservation,
                event=reject_event(reservation.request.dispatch_key, event_type=event_type),
            )
            await terminal_db.commit()

        async with session_factory() as verify_db:
            demand = await verify_db.get(SmtInboundHandoffDemand, demand_id)
            assert demand is not None
            assert demand.status is SmtInboundHandoffDemandStatus.RECONCILING
            assert demand.active_full_box_exchange_intent_id == 5101
            owners = (await verify_db.scalars(select(MaterialFlowOwner))).all()
            assert len(owners) == 4
            assert all(owner.lifecycle_state == "ACTIVE" for owner in owners)
            assert await _resource_snapshot(verify_db, demand_id=demand_id) == before
            intent = await verify_db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == reservation.request.dispatch_key)
            )
            assert intent is not None and intent.effect_status is RuntimeIntentStatus.REJECTED

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_success_projects_exact_relations_inventory_and_parent_readiness() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as setup_db:
            graph = await seed_exchange_graph(
                setup_db,
                include_partial_bin=True,
                materials_per_full_bin=2,
            )
            reservation, empty_mount = await _prepare_with_empty(
                setup_db,
                service=service_type(),
                projector=projector,
                graph=graph,
            )
            demand_id = graph.demand.id
            assert demand_id is not None
            full_occupancy_id = graph.bins["FULL-1"].occupancy.id
            source_item_ids = [item.id for item in graph.bins["FULL-1"].source_items]
            material_ids = [mount.id for mount in graph.bins["FULL-1"].material_mounts]
            assert full_occupancy_id is not None
            assert all(item_id is not None for item_id in source_item_ids)
            assert all(material_id is not None for material_id in material_ids)
            result = success_result(
                reservation,
                empty_bin_id="EMPTY-1",
                five_rack_code=empty_mount.rack_code,
            )
            await setup_db.commit()

        async with session_factory() as terminal_db:
            await _project(
                terminal_db,
                projector=projector,
                reservation=reservation,
                event=completed_event(result),
            )
            await terminal_db.commit()

        async with session_factory() as verify_db:
            active_relations = (
                await verify_db.execute(
                    select(
                        RackBinMount.rack_code,
                        RackBinMount.rack_slot_code,
                        RackBinMount.bin_code,
                    )
                    .where(RackBinMount.ended_at.is_(None))
                    .order_by(RackBinMount.bin_code)
                )
            ).all()
            assert {(row.rack_code, row.rack_slot_code, row.bin_code) for row in active_relations} == {
                ("SINGLE-1", "A-01", "EMPTY-1"),
                ("SINGLE-1", "A-02", "PARTIAL-1"),
                (empty_mount.rack_code, "FULL-DEST", "FULL-1"),
            }
            assert (
                await verify_db.scalar(
                    select(func.count()).select_from(RackBinMount).where(RackBinMount.ended_at.is_not(None))
                )
                == 2
            )
            occupancy = await verify_db.get(BinCellOccupancy, full_occupancy_id)
            assert occupancy is not None
            assert occupancy.ended_at is None
            assert occupancy.occupancy_status.value == "FULL"
            material_mounts = (
                await verify_db.scalars(select(BinMaterialMount).where(BinMaterialMount.id.in_(material_ids)))
            ).all()
            assert len(material_mounts) == 2
            assert all(mount.ended_at is None and mount.mount_status.value == "OCCUPIED" for mount in material_mounts)
            assert all(mount.wms_confirmation_status.value == "CONFIRMED" for mount in material_mounts)
            assert all(mount.wms_inventory_version == "7" for mount in material_mounts)
            source_items = (
                await verify_db.scalars(
                    select(SmtInboundHandoffSourceItem).where(SmtInboundHandoffSourceItem.id.in_(source_item_ids))
                )
            ).all()
            assert all(item.status is SmtInboundHandoffSourceItemStatus.EXCHANGED for item in source_items)
            owners = (await verify_db.scalars(select(MaterialFlowOwner))).all()
            assert len(owners) == 4
            assert all(owner.lifecycle_state == "RELEASED" and owner.released_at_ms == 3000 for owner in owners)
            demand = await verify_db.get(SmtInboundHandoffDemand, demand_id)
            assert demand is not None
            assert demand.active_full_box_exchange_intent_id is None
            assert demand.status is SmtInboundHandoffDemandStatus.READY_FOR_SORTING

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    "drift",
    [
        "selected_empty_occupied",
        "target_slot_conflict",
        "frozen_occupancy_missing",
        "material_missing",
        "material_code_changed",
        "quantity_changed",
        "source_item_missing",
        "source_item_duplicate",
        "source_identity_changed",
        "target_slot_missing",
    ],
)
def test_e11_success_detects_locked_set_drift_and_rolls_back_whole_terminal_transaction(
    drift: str,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as setup_db:
            graph = await seed_exchange_graph(setup_db, materials_per_full_bin=2)
            reservation, empty_mount = await _prepare_with_empty(
                setup_db,
                service=service_type(),
                projector=projector,
                graph=graph,
            )
            demand_id = graph.demand.id
            assert demand_id is not None
            if drift == "selected_empty_occupied":
                setup_db.add(
                    BinCellOccupancy(
                        bin_code="EMPTY-1",
                        bin_cell_code="EMPTY-1-CELL-1",
                        bin_cell_index="1",
                        material_identity_key="unexpected-empty-material",
                        material_code="UNEXPECTED",
                        reel_count=1,
                        used_depth_mm=1,
                        capacity_depth_mm=100,
                        remaining_depth_mm=99,
                        occupancy_status="OCCUPIED",
                        source_system=ResourceSourceSystem.WES_RUNTIME,
                        source_event_id="unexpected-empty-occupancy",
                        started_at=NOW,
                    )
                )
            elif drift == "target_slot_conflict":
                setup_db.add(
                    RackBinMount(
                        rack_code=empty_mount.rack_code,
                        rack_slot_code="FULL-DEST",
                        bin_code="UNRELATED-BIN",
                        mount_status=RackBinMountStatus.MOUNTED,
                        source_system=ResourceSourceSystem.WMS,
                        source_event_id="unexpected-target-slot",
                        started_at=NOW,
                    )
                )
            elif drift == "frozen_occupancy_missing":
                for mount in graph.bins["FULL-1"].material_mounts:
                    await setup_db.delete(mount)
                await setup_db.flush()
                await setup_db.delete(graph.bins["FULL-1"].occupancy)
            elif drift == "material_missing":
                await setup_db.delete(graph.bins["FULL-1"].material_mounts[-1])
            elif drift == "material_code_changed":
                graph.bins["FULL-1"].material_mounts[-1].material_code = "DRIFTED-MATERIAL"
            elif drift == "quantity_changed":
                graph.bins["FULL-1"].material_mounts[-1].qty_snapshot = 11
            elif drift == "source_item_missing":
                await setup_db.delete(graph.bins["FULL-1"].source_items[-1])
            elif drift == "source_item_duplicate":
                source = graph.bins["FULL-1"].source_items[-1]
                setup_db.add(
                    type(source)(
                        handoff_demand_id=source.handoff_demand_id,
                        item_key=f"{source.item_key}:duplicate",
                        bin_code=source.bin_code,
                        bin_cell_index=source.bin_cell_index,
                        bin_cell_code=source.bin_cell_code,
                        material_identity_key=source.material_identity_key,
                        pkg_code=source.pkg_code,
                        status=source.status,
                    )
                )
            elif drift == "source_identity_changed":
                graph.bins["FULL-1"].source_items[-1].material_identity_key = "DRIFTED-IDENTITY"
            await setup_db.flush()
            drifted = await _resource_snapshot(setup_db, demand_id=demand_id)
            result = success_result(
                reservation,
                empty_bin_id="EMPTY-1",
                five_rack_code=empty_mount.rack_code,
                full_destination_slot_id="MISSING-SLOT" if drift == "target_slot_missing" else "FULL-DEST",
            )
            await setup_db.commit()

        async with session_factory() as terminal_db:
            with pytest.raises(
                (ValueError, RuntimeError),
                match=r"frozen|set|empty|slot|occupancy|material|source|quantity",
            ):
                await _project(
                    terminal_db,
                    projector=projector,
                    reservation=reservation,
                    event=completed_event(result),
                )
            await terminal_db.rollback()

        async with session_factory() as verify_db:
            intent = await verify_db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == reservation.request.dispatch_key)
            )
            assert intent is not None and intent.effect_status is RuntimeIntentStatus.PROPOSED
            demand = await verify_db.get(SmtInboundHandoffDemand, demand_id)
            assert demand is not None
            assert demand.active_full_box_exchange_intent_id == 5101
            assert await _resource_snapshot(verify_db, demand_id=demand_id) == drifted
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(RackBinMount)
                    .where(
                        RackBinMount.rack_code == empty_mount.rack_code,
                        RackBinMount.rack_slot_code == "FULL-DEST",
                        RackBinMount.bin_code == "FULL-1",
                        RackBinMount.ended_at.is_(None),
                    )
                )
                == 0
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_success_allows_empty_source_and_full_destination_on_different_racks() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, include_partial_bin=True)
            reservation, empty_mount = await _prepare_with_empty(
                db,
                service=service_type(),
                projector=projector,
                graph=graph,
            )
            destination_rack = await seed_destination_rack(db, rack_code="INDEPENDENT-DEST")
            result = success_result(
                reservation,
                empty_bin_id="EMPTY-1",
                five_rack_code=destination_rack.rack_code,
            )
            await db.commit()

            await _project(
                db,
                projector=projector,
                reservation=reservation,
                event=completed_event(result),
            )
            await db.flush()

            full_destination = await db.scalar(
                select(RackBinMount).where(
                    RackBinMount.rack_code == destination_rack.rack_code,
                    RackBinMount.rack_slot_code == "FULL-DEST",
                    RackBinMount.bin_code == "FULL-1",
                    RackBinMount.ended_at.is_(None),
                )
            )
            assert full_destination is not None
            assert empty_mount.rack_code != destination_rack.rack_code
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_success_locks_destination_master_before_active_mounts() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, include_partial_bin=True)
            reservation, empty_mount = await _prepare_with_empty(
                db,
                service=service_type(),
                projector=projector,
                graph=graph,
            )
            result = success_result(
                reservation,
                empty_bin_id="EMPTY-1",
                five_rack_code=empty_mount.rack_code,
            )
            await db.commit()
            reduction = await effect_reducer.reduce(db, completed_event(result))
            assert reduction is not None and reduction.state_changed is True
            statements: list[str] = []

            def record_statement(
                _connection: Any,
                _cursor: Any,
                statement: str,
                *_args: Any,
            ) -> None:
                if "resource_racks" in statement or "resource_rack_bin_mounts" in statement:
                    statements.append(statement)

            sqlalchemy_event.listen(db.bind.sync_engine, "before_cursor_execute", record_statement)
            try:
                await projector.project_event(
                    db,
                    operation=WMS_OPERATION_BY_IDENTITY[E11],
                    request_payload=reservation.request.model_dump(mode="json"),
                    event=completed_event(result),
                    reduction=reduction,
                )
            finally:
                sqlalchemy_event.remove(db.bind.sync_engine, "before_cursor_execute", record_statement)

            master_index = next(index for index, statement in enumerate(statements) if "resource_racks" in statement)
            mount_index = next(
                index for index, statement in enumerate(statements) if "resource_rack_bin_mounts" in statement
            )
            assert master_index < mount_index
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_competing_transactions_reread_mounts_after_shared_destination_master_lock() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        async with session_factory() as setup_db:
            first_graph = await seed_exchange_graph(setup_db, full_bins=("FULL-1",))
            second_graph = await seed_exchange_graph(
                setup_db,
                full_bins=("FULL-2",),
                graph_index=2,
            )
            first = await prepare_exchange(
                service=service_type(),
                projector=projector_type(),
                db=setup_db,
                graph=first_graph,
                full_box_id="FULL-1",
                intent_id=5251,
            )
            second = await prepare_exchange(
                service=service_type(),
                projector=projector_type(),
                db=setup_db,
                graph=second_graph,
                full_box_id="FULL-2",
                intent_id=5252,
            )
            first_empty = await seed_selected_empty_bin(
                setup_db,
                empty_bin_id="EMPTY-1",
                source_slot_id="A-01",
            )
            second_empty = await seed_selected_empty_bin(
                setup_db,
                empty_bin_id="EMPTY-2",
                source_slot_id="A-01",
            )
            await seed_destination_rack(
                setup_db,
                rack_code="A-SHARED-DEST",
                slot_id="FULL-DEST",
            )
            first_result = success_result(
                first,
                empty_bin_id="EMPTY-1",
                five_rack_code="A-SHARED-DEST",
            )
            second_result = success_result(
                second,
                empty_bin_id="EMPTY-2",
                five_rack_code="A-SHARED-DEST",
                source_version="8",
            )
            assert first_empty.rack_code != second_empty.rack_code
            await setup_db.commit()

        async with session_factory() as first_db, session_factory() as second_db:
            await _project(
                first_db,
                projector=projector_type(),
                reservation=first,
                event=completed_event(first_result),
            )
            competing = asyncio.create_task(
                _project(
                    second_db,
                    projector=projector_type(),
                    reservation=second,
                    event=completed_event(second_result, occurred_at_ms=4000),
                )
            )
            done, _pending = await asyncio.wait({competing}, timeout=0.2)
            assert not done, "第二事务必须等待共享 destination master 行锁"
            await first_db.commit()
            with pytest.raises(ValueError, match=r"destination.*conflict|slot.*conflict"):
                await asyncio.wait_for(competing, timeout=2)
            await second_db.rollback()

        async with session_factory() as verify_db:
            active_shared = (
                await verify_db.execute(
                    select(RackBinMount)
                    .where(
                        RackBinMount.rack_code == "A-SHARED-DEST",
                        RackBinMount.rack_slot_code == "FULL-DEST",
                        RackBinMount.ended_at.is_(None),
                    )
                    .order_by(RackBinMount.id)
                )
            ).scalars()
            assert [mount.bin_code for mount in active_shared] == ["FULL-1"]

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_two_full_bins_serialize_and_only_last_success_releases_parent_to_sorting() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        service = service_type()
        projector = projector_type()
        async with session_factory() as db:
            graph = await seed_exchange_graph(
                db,
                full_bins=("FULL-1", "FULL-2"),
                include_partial_bin=True,
            )
            graph.bins["FULL-2"].occupancy.occupancy_status = "OCCUPIED"
            first, empty_one = await _prepare_with_empty(
                db,
                service=service,
                projector=projector,
                graph=graph,
                full_box_id="FULL-1",
                empty_box_id="EMPTY-1",
                intent_id=5301,
            )
            await db.commit()
            await _project(
                db,
                projector=projector,
                reservation=first,
                event=completed_event(
                    success_result(
                        first,
                        empty_bin_id="EMPTY-1",
                        five_rack_code=empty_one.rack_code,
                    )
                ),
            )
            await db.flush()
            assert graph.demand.status == SmtInboundHandoffDemandStatus.EVALUATING
            assert graph.demand.active_full_box_exchange_intent_id is None
            # 每个 E11 root 是独立 Intent/Outbox 事务；首个终态提交后第二个满箱才可竞争。
            await db.commit()

            second = await reserve_exchange(service, db, graph, full_box_id="FULL-2")
            assert second.created is True
            await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=second,
                intent_id=5302,
            )
            empty_two = await seed_selected_empty_bin(
                db,
                empty_bin_id="EMPTY-2",
                source_slot_id="A-01",
            )
            await db.commit()
            await _project(
                db,
                projector=projector,
                reservation=second,
                event=completed_event(
                    success_result(
                        second,
                        empty_bin_id="EMPTY-2",
                        five_rack_code=empty_two.rack_code,
                        source_version="8",
                    ),
                    occurred_at_ms=4000,
                ),
            )
            await db.flush()
            assert graph.demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
            assert graph.demand.active_full_box_exchange_intent_id is None
            full_items = [
                *graph.bins["FULL-1"].source_items,
                *graph.bins["FULL-2"].source_items,
            ]
            assert all(item.status == SmtInboundHandoffSourceItemStatus.EXCHANGED for item in full_items)
            assert all(
                item.status == SmtInboundHandoffSourceItemStatus.READY for item in graph.bins["PARTIAL-1"].source_items
            )
            await db.commit()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_preferred_threshold_keeps_parent_evaluating_while_preferred_bin_remains() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as db:
            graph = await seed_exchange_graph(
                db,
                full_bins=("FULL-1", "FULL-2"),
                include_partial_bin=True,
            )
            for bin_code in ("FULL-1", "FULL-2"):
                occupancy = graph.bins[bin_code].occupancy
                occupancy.used_depth_mm = Decimal("60")
                occupancy.remaining_depth_mm = Decimal("40")
                occupancy.occupancy_status = "OCCUPIED"
            first, empty_one = await _prepare_with_empty(
                db,
                service=service_type(),
                projector=projector,
                graph=graph,
                full_box_id="FULL-1",
                prefer_full_box_exchange=True,
            )
            await db.commit()

            await _project(
                db,
                projector=projector,
                reservation=first,
                event=completed_event(
                    success_result(
                        first,
                        empty_bin_id="EMPTY-1",
                        five_rack_code=empty_one.rack_code,
                    )
                ),
            )
            await db.flush()

            assert graph.demand.decision_status == "PREFERRED_FULL_BOX_EXCHANGE_REQUESTED"
            assert graph.demand.status == SmtInboundHandoffDemandStatus.EVALUATING
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_required_threshold_ignores_one_occupied_slot_across_six_slot_capacity() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as db:
            graph = await seed_exchange_graph(
                db,
                include_partial_bin=True,
                bin_slot_counts={"PARTIAL-1": 6},
            )
            remaining = graph.bins["PARTIAL-1"].occupancy
            remaining.used_depth_mm = Decimal("90")
            remaining.remaining_depth_mm = Decimal("10")
            reservation, empty_mount = await _prepare_with_empty(
                db,
                service=service_type(),
                projector=projector,
                graph=graph,
            )
            await db.commit()

            await _project(
                db,
                projector=projector,
                reservation=reservation,
                event=completed_event(
                    success_result(
                        reservation,
                        empty_bin_id="EMPTY-1",
                        five_rack_code=empty_mount.rack_code,
                    )
                ),
            )

            assert graph.demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("bin_cell_index", "bin_cell_code", "occupancy_status"),
    [
        ("99", None, "OCCUPIED"),
        ("UNKNOWN", "PARTIAL-1:UNKNOWN", "UNKNOWN"),
    ],
)
def test_e11_terminal_rejects_remaining_occupancy_without_template_index_mapping(
    bin_cell_index: str,
    bin_cell_code: str | None,
    occupancy_status: str,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        projector = projector_type()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, include_partial_bin=True)
            remaining = graph.bins["PARTIAL-1"].occupancy
            remaining.bin_cell_index = bin_cell_index
            remaining.bin_cell_code = bin_cell_code
            remaining.occupancy_status = occupancy_status
            reservation, empty_mount = await _prepare_with_empty(
                db,
                service=service_type(),
                projector=projector,
                graph=graph,
            )
            await db.commit()

            with pytest.raises(ValueError, match=r"usage|mapping|occupancy|template"):
                await _project(
                    db,
                    projector=projector,
                    reservation=reservation,
                    event=completed_event(
                        success_result(
                            reservation,
                            empty_bin_id="EMPTY-1",
                            five_rack_code=empty_mount.rack_code,
                        )
                    ),
                )
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_success_statement_budget_is_constant_for_one_occupancy_with_many_materials() -> None:
    counts: list[int] = []

    async def measure(
        session_factory: async_sessionmaker[AsyncSession],
        *,
        material_count: int,
        intent_id: int,
    ) -> None:
        service_type, projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(
                db,
                include_partial_bin=True,
                materials_per_full_bin=material_count,
            )
            projector = projector_type()
            reservation, empty_mount = await _prepare_with_empty(
                db,
                service=service_type(),
                projector=projector,
                graph=graph,
                intent_id=intent_id,
            )
            result = success_result(
                reservation,
                empty_bin_id="EMPTY-1",
                five_rack_code=empty_mount.rack_code,
            )
            await db.commit()
            reduction = await effect_reducer.reduce(db, completed_event(result))
            assert reduction is not None and reduction.state_changed is True
            statements = 0

            def count_statement(*_args: Any, **_kwargs: Any) -> None:
                nonlocal statements
                statements += 1

            sqlalchemy_event.listen(db.bind.sync_engine, "before_cursor_execute", count_statement)
            try:
                await projector.project_event(
                    db,
                    operation=WMS_OPERATION_BY_IDENTITY[E11],
                    request_payload=reservation.request.model_dump(mode="json"),
                    event=completed_event(result),
                    reduction=reduction,
                )
            finally:
                sqlalchemy_event.remove(db.bind.sync_engine, "before_cursor_execute", count_statement)
            assert all(
                item.status == SmtInboundHandoffSourceItemStatus.EXCHANGED for item in graph.bins["FULL-1"].source_items
            )
            assert all(
                getattr(mount.wms_confirmation_status, "value", mount.wms_confirmation_status) == "CONFIRMED"
                for mount in graph.bins["FULL-1"].material_mounts
            )
            counts.append(statements)

    for index, material_count in enumerate((1, 10, 100), start=1):
        asyncio.run(
            with_database(
                lambda session_factory, material_count=material_count, intent_id=5400 + index: measure(
                    session_factory,
                    material_count=material_count,
                    intent_id=intent_id,
                )
            )
        )
    assert counts[1:] == [counts[0], counts[0]]
