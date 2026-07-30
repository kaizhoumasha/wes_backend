"""E12 reserve/preparation 的真实 PostgreSQL 并发与原子性证据。"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.app.resource.models import Bin, RackSlotTemplate
from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.sys.models.outbox import SystemOutbox
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.wms_conveyor_batch_postgresql import (
    REVISION,
    claim_reservation,
    domain_types,
    mark_bin_unavailable,
    prepare_reservation,
    reserve_batch,
    seed_batch_graph,
    seed_reserved_positions,
    with_database,
)


@pytest.mark.integration
def test_entry_membership_shape_and_active_position_unique_are_migrated() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            constraint_def = await db.scalar(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE connamespace = 'wes_runtime'::regnamespace
                      AND conname = 'ck_conveyor_queue_memberships_entry_shape'
                    """
                )
            )
            index_def = await db.scalar(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'wes_runtime'
                      AND tablename = 'conveyor_queue_memberships'
                      AND indexname = 'ux_wes_runtime_conveyor_queue_memberships_active_entry_position'
                    """
                )
            )

            assert constraint_def is not None
            assert "queue_role" in constraint_def and "ENTRY" in constraint_def
            assert "route_instance_id IS NOT NULL" in constraint_def
            assert "queue_position IS NOT NULL" in constraint_def
            assert "bin_code IS NOT NULL" in constraint_def
            assert index_def is not None and "UNIQUE INDEX" in index_def
            assert "(workline_id, queue_code, queue_position)" in index_def
            assert "membership_status" in index_def and "RECONCILING" in index_def
            assert "queue_role" in index_def and "ENTRY" in index_def

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_entry_membership_migration_round_trip() -> None:
    async def count_constraints(database_url: str) -> tuple[int, int]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                check_count = await connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_constraint
                        WHERE connamespace = 'wes_runtime'::regnamespace
                          AND conname = 'ck_conveyor_queue_memberships_entry_shape'
                        """
                    )
                )
                index_count = await connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_indexes
                        WHERE schemaname = 'wes_runtime'
                          AND indexname = 'ux_wes_runtime_conveyor_queue_memberships_active_entry_position'
                        """
                    )
                )
                return int(check_count or 0), int(index_count or 0)
        finally:
            await engine.dispose()

    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", REVISION, database_url=database_url)
            assert await count_constraints(database_url) == (1, 1)
            run_alembic("downgrade", "f9ffbef8992a", database_url=database_url)
            assert await count_constraints(database_url) == (0, 0)
            run_alembic("upgrade", REVISION, database_url=database_url)
            assert await count_constraints(database_url) == (1, 1)

    asyncio.run(scenario())


@pytest.mark.integration
def test_entry_membership_and_active_route_constraints_reject_conflicts() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 1234)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as setup_db:
            graph = await seed_batch_graph(
                setup_db,
                graph_index=63,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
            )
            reservation = await reserve_batch(service, setup_db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=setup_db,
                graph=graph,
                reservation=reservation,
            )
            assert prepared.intent_log is not None
            await setup_db.commit()

        routes = tuple(item.route_instance_id for item in reservation.request.items)
        bins = tuple(item.bin_id for item in reservation.request.items)
        queue_code = reservation.request.destination_station_code
        async with session_factory() as db:
            db.add(
                BinRouteInstance(
                    route_instance_id=f"{reservation.request.batch_id}:duplicate-route",
                    bin_code=bins[0],
                    workline_id=graph.workline_id,
                    created_by_e12_intent_id=prepared.intent_log.id,
                    current_node="FIVE_RACK",
                    current_rack_code=reservation.request.items[0].source_rack_id,
                    current_slot_code=reservation.request.items[0].source_slot_id,
                    route_version=1,
                    lifecycle_state="ACTIVE",
                    last_transition_source="TEST",
                    last_transition_source_event_id="duplicate-active-route",
                )
            )
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

        async with session_factory() as db:
            db.add(
                ConveyorQueueMembership(
                    bin_code=bins[0],
                    workline_id=graph.workline_id,
                    conveyor_code=queue_code,
                    queue_code=queue_code,
                    queue_role="ENTRY",
                    membership_status="ACTIVE",
                    entered_at=1,
                    route_instance_id=routes[0],
                    queue_position=None,
                )
            )
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

        async with session_factory() as db:
            db.add(
                ConveyorQueueMembership(
                    bin_code=bins[0],
                    workline_id=graph.workline_id,
                    conveyor_code=queue_code,
                    queue_code=queue_code,
                    queue_role="ENTRY",
                    membership_status="ACTIVE",
                    entered_at=1,
                    route_instance_id=routes[0],
                    queue_position=1,
                )
            )
            await db.commit()

        async with session_factory() as db:
            db.add(
                ConveyorQueueMembership(
                    bin_code=bins[1],
                    workline_id=graph.workline_id,
                    conveyor_code=queue_code,
                    queue_code=queue_code,
                    queue_role="ENTRY",
                    membership_status="RECONCILING",
                    entered_at=2,
                    route_instance_id=routes[1],
                    queue_position=1,
                )
            )
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_reserve_uses_pinned_binding_not_mutable_workline_config() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_batch_graph(db, entry_capacity=2, ctu_capacity=4, bin_count=3)
            reservation = await reserve_batch(service_type(), db, graph)

            assert reservation.created is True
            assert reservation.request.source_station_code == "TARGET_STATION"
            assert reservation.request.destination_station_code == graph.config["conveyor_entry_queue"]["code"]
            assert len(reservation.request.items) == 2
            assert tuple(item.reserved_queue_position for item in reservation.request.items) == (1, 2)
            assert reservation.request.capacity_snapshot_version == service_type.capacity_snapshot_version(
                binding_id=graph.binding_id,
                binding_version=1,
                plugin_config_hash=reservation.claim.plugin_config_hash,
                entry_capacity=2,
            )
            assert reservation.request.destination_station_code != "MUTABLE_WRONG"
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_claim_member_route_and_outbox_are_one_transaction() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 1234)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(db, graph_index=60, entry_capacity=2, ctu_capacity=2, bin_count=3)
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )

            assert prepared.intent_log is not None
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            assert await db.scalar(select(func.count()).select_from(BinRouteInstance)) == 2
            assert await db.scalar(select(func.count()).select_from(WmsConveyorBatchMember)) == 2
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            members = (
                await db.execute(select(WmsConveyorBatchMember).order_by(WmsConveyorBatchMember.sequence_no))
            ).scalars()
            assert tuple(member.member_state for member in members) == ("CANDIDATE", "CANDIDATE")
            await db.commit()

        async with session_factory() as verify_db:
            routes = (
                await verify_db.execute(select(BinRouteInstance).order_by(BinRouteInstance.route_instance_id))
            ).scalars()
            assert tuple(
                (route.current_node, route.current_rack_code, route.current_slot_code) for route in routes
            ) == tuple(
                ("FIVE_RACK", item.source_rack_id, item.source_slot_id)
                for item in sorted(reservation.request.items, key=lambda item: item.route_instance_id)
            )
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 1

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_member_insert_failure_rolls_back_intent_route_and_outbox() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 1234)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(db, graph_index=61, entry_capacity=2, ctu_capacity=2, bin_count=2)
            await db.commit()
            reservation = await reserve_batch(service, db, graph)

            def fail_member_flush(session, _flush_context, _instances) -> None:  # type: ignore[no-untyped-def]
                if any(isinstance(value, WmsConveyorBatchMember) for value in session.new):
                    raise RuntimeError("injected E12 member failure")

            event.listen(db.sync_session, "before_flush", fail_member_flush)
            try:
                with pytest.raises(RuntimeError, match="injected E12 member failure"):
                    await prepare_reservation(
                        projector=projector,
                        db=db,
                        graph=graph,
                        reservation=reservation,
                    )
            finally:
                event.remove(db.sync_session, "before_flush", fail_member_flush)
            await db.rollback()

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(BinRouteInstance)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(WmsConveyorBatchMember)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 0

        async with session_factory() as db:
            reservation = await reserve_batch(service, db, graph)
            _ctx, prepared, _execution = await claim_reservation(
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert prepared.intent_log is not None
            await db.rollback()

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 0

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_two_workers_wait_reread_and_never_overlap() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 1234)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as setup_db:
            graph = await seed_batch_graph(
                setup_db,
                graph_index=62,
                entry_capacity=3,
                ctu_capacity=2,
                bin_count=3,
            )
            await setup_db.commit()

        async with session_factory() as winner_db, session_factory() as loser_db:
            winner = await reserve_batch(service, winner_db, graph)
            assert winner.created is True and len(winner.request.items) == 2
            loser_task = asyncio.create_task(reserve_batch(service, loser_db, graph))
            done, _pending = await asyncio.wait({loser_task}, timeout=0.1)
            assert not done, "loser 必须等待 WorkLine + entry queue transaction lock"

            await prepare_reservation(
                projector=projector,
                db=winner_db,
                graph=graph,
                reservation=winner,
            )
            await winner_db.commit()

            loser = await asyncio.wait_for(loser_task, timeout=2)
            assert loser.created is True and len(loser.request.items) == 1
            winner_bins = {item.bin_id for item in winner.request.items}
            loser_bins = {item.bin_id for item in loser.request.items}
            assert winner_bins.isdisjoint(loser_bins)
            assert {
                *(item.reserved_queue_position for item in winner.request.items),
                *(item.reserved_queue_position for item in loser.request.items),
            } == {1, 2, 3}
            await prepare_reservation(
                projector=projector,
                db=loser_db,
                graph=graph,
                reservation=loser,
            )
            await loser_db.commit()

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 2
            assert await verify_db.scalar(select(func.count()).select_from(BinRouteInstance)) == 3
            assert await verify_db.scalar(select(func.count()).select_from(WmsConveyorBatchMember)) == 3
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 2

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_existing_pin_position_and_slot_authorities_fail_closed() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            pin_drift = await seed_batch_graph(db, graph_index=50, bin_count=1)
            workline = await db.get(WorkLine, pin_drift.workline_id)
            assert workline is not None
            workline.active_plugin_config_hash = "f" * 64
            with pytest.raises(PermissionError, match=r"pin|binding"):
                await reserve_batch(service_type(), db, pin_drift)

            inactive = await seed_batch_graph(db, graph_index=51, bin_count=1)
            inactive_workline = await db.get(WorkLine, inactive.workline_id)
            assert inactive_workline is not None
            inactive_workline.is_active = False
            with pytest.raises(PermissionError, match=r"active|deleted"):
                await reserve_batch(service_type(), db, inactive)

            disabled_position = await seed_batch_graph(db, graph_index=52, bin_count=1)
            position = await db.scalar(
                select(WorklineRackPosition).where(WorklineRackPosition.workline_id == disabled_position.workline_id)
            )
            assert position is not None
            position.enabled = False
            assert (await reserve_batch(service_type(), db, disabled_position)).created is False

            wrong_slot_kind = await seed_batch_graph(db, graph_index=53, bin_count=1)
            rack_slot = await db.scalar(
                select(RackSlotTemplate).where(RackSlotTemplate.rack_type_code == "FIVE-E12-53")
            )
            assert rack_slot is not None
            rack_slot.slot_kind = "MATERIAL_SLOT"
            assert (await reserve_batch(service_type(), db, wrong_slot_kind)).created is False

            disallowed_bin_type = await seed_batch_graph(db, graph_index=54, bin_count=1)
            rack_slot = await db.scalar(
                select(RackSlotTemplate).where(RackSlotTemplate.rack_type_code == "FIVE-E12-54")
            )
            bin_master = await db.scalar(select(Bin).where(Bin.bin_code == disallowed_bin_type.bin_codes[0]))
            assert rack_slot is not None and bin_master is not None
            rack_slot.allowed_bin_types = ["SOME-OTHER-BIN-TYPE"]
            assert bin_master.bin_type_code not in rack_slot.allowed_bin_types
            assert (await reserve_batch(service_type(), db, disallowed_bin_type)).created is False
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_unavailable_bin_predicates_fail_closed_without_intent_or_outbox() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, _projector_type = domain_types()
        reasons = (
            "disabled_bin",
            "disabled_type",
            "no_active_slot",
            "full",
            "unknown",
            "occupied_unknown",
            "reservation",
            "route",
            "membership",
            "owner",
        )
        async with session_factory() as db:
            for graph_index, reason in enumerate(reasons, start=10):
                graph = await seed_batch_graph(
                    db,
                    graph_index=graph_index,
                    entry_capacity=1,
                    ctu_capacity=1,
                    bin_count=1,
                )
                await mark_bin_unavailable(db, graph, bin_code=graph.bin_codes[0], reason=reason)
                reservation = await reserve_batch(service_type(), db, graph)
                assert reservation.created is False, reason
                assert reservation.claim is None and reservation.operation is None and reservation.request is None

            capacity_full = await seed_batch_graph(
                db,
                graph_index=30,
                entry_capacity=1,
                ctu_capacity=1,
                bin_count=1,
            )
            await seed_reserved_positions(db, capacity_full, member_positions=(1,))
            no_capacity = await reserve_batch(service_type(), db, capacity_full)
            assert no_capacity.created is False
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 0
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_accepts_empty_or_positive_remaining_cell_without_content_snapshot() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=40,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
                occupied_remaining_by_bin={"E12-40-BIN-02": Decimal("25")},
            )
            reservation = await reserve_batch(service_type(), db, graph)

            assert reservation.created is True
            assert tuple(item.bin_id for item in reservation.request.items) == graph.bin_codes
            assert len(reservation.request.items) == 2
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_batch_size_is_min_of_position_union_ctu_and_available_bins() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            position_limited = await seed_batch_graph(
                db,
                graph_index=1,
                entry_capacity=4,
                ctu_capacity=4,
                bin_count=4,
            )
            await seed_reserved_positions(
                db,
                position_limited,
                member_positions=(1,),
                membership_positions=((2, "ACTIVE"), (3, "RECONCILING")),
            )
            ctu_limited = await seed_batch_graph(
                db,
                graph_index=2,
                entry_capacity=4,
                ctu_capacity=2,
                bin_count=4,
            )
            bin_limited = await seed_batch_graph(
                db,
                graph_index=3,
                entry_capacity=4,
                ctu_capacity=4,
                bin_count=1,
            )

            by_position = await reserve_batch(service_type(), db, position_limited)
            by_ctu = await reserve_batch(service_type(), db, ctu_limited)
            by_bin = await reserve_batch(service_type(), db, bin_limited)

            assert tuple(item.reserved_queue_position for item in by_position.request.items) == (4,)
            assert len(by_ctu.request.items) == 2
            assert len(by_bin.request.items) == 1
            await db.rollback()

    asyncio.run(with_database(scenario))
