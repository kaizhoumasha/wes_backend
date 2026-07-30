"""E11 根串行化、阶段门与 preparation owner 的真实 PostgreSQL RED。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, func, select, text

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffDemandStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.sys.models.outbox import SystemOutbox
from src.app.wms_integration.effect_preparation_runtime import (
    bind_wms_effect_preparation_runtime,
    build_wms_effect_preparation_runtime,
    unbind_wms_effect_preparation_runtime,
)
from src.celery_app.tasks.workline import _scan_smt_inbound_handoff_demands_in_transaction
from src.core.task_queue_gateway import OutboxDispatchTarget
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.support.wms_full_box_exchange_postgresql import (
    REVISION,
    domain_types,
    prepare_exchange,
    prepare_reservation,
    reserve_exchange,
    seed_exchange_graph,
    with_database,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _insert_reservation(db: AsyncSession, *, status: str) -> None:
    await db.execute(text("SET session_replication_role = replica"))
    try:
        await db.execute(
            text(
                """
                INSERT INTO wes_biz.workline_bin_cell_reservations (
                    reservation_key, workline_id, workline_code, session_id,
                    correlation_id, trace_id, pkg_code, bin_code, bin_cell_code,
                    bin_cell_index, reservation_status, source_event_id, reserved_at, created_at
                )
                VALUES (
                    'reservation-e11', 1, 'ROUGH-10', 101,
                    'release-e11', 'trace-e11', 'FULL-1-PKG-1', 'FULL-1', 'FULL-1-CELL-1',
                    '1', :status, 'reservation-e11', '2026-07-30 08:01:00', '2026-07-30 08:01:00'
                )
                """
            ),
            {"status": status},
        )
    finally:
        await db.execute(text("SET session_replication_role = origin"))


async def _insert_command(db: AsyncSession, *, status: str, trace_id: str | None) -> None:
    await db.execute(text("SET session_replication_role = replica"))
    try:
        await db.execute(
            text(
                """
                INSERT INTO wes_biz.device_commands (
                    device_id, task_type, priority, timeout_ms, params,
                    command_code, trace_id, workline_id, status, retry_count, created_at
                )
                VALUES (
                    999, 'ROUGH_RELEASE', 5, 30000, '{}'::json,
                    :command_code, :trace_id, 1, :status, 0, '2026-07-30 08:01:00'
                )
                """
            ),
            {
                "command_code": f"cmd-{status.lower()}-{trace_id or 'missing'}",
                "trace_id": trace_id,
                "status": status,
            },
        )
    finally:
        await db.execute(text("SET session_replication_role = origin"))


async def _insert_conflicting_owner(db: AsyncSession, *, owner_type: str) -> None:
    db.add(
        MaterialFlowOwner(
            workline_id=1,
            object_type="RACK",
            object_key="SINGLE-1",
            owner_type=owner_type,
            owner_key=f"conflict:{owner_type}",
            lifecycle_state="ACTIVE",
            source_event_id=f"owner-conflict:{owner_type}",
            acquired_at_ms=1,
        )
    )
    await db.flush()


@pytest.mark.integration
def test_e11_fixed_revision_adds_nullable_stage_gate_and_active_intent_fk() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT column_name, is_nullable, character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'smt_inbound_handoff_demands'
                          AND column_name IN (
                              'full_box_exchange_station_code',
                              'full_box_exchange_rack_face',
                              'active_full_box_exchange_intent_id'
                          )
                        """
                    )
                )
            ).all()
            assert {(row.column_name, row.is_nullable) for row in rows} == {
                ("full_box_exchange_station_code", "YES"),
                ("full_box_exchange_rack_face", "YES"),
                ("active_full_box_exchange_intent_id", "YES"),
            }
            lengths = {row.column_name: row.character_maximum_length for row in rows}
            assert lengths["full_box_exchange_station_code"] == 120
            assert lengths["full_box_exchange_rack_face"] == 1
            fk_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.constraint_schema = tc.constraint_schema
                    WHERE tc.table_schema = 'wes_biz'
                      AND tc.table_name = 'smt_inbound_handoff_demands'
                      AND tc.constraint_type = 'FOREIGN KEY'
                      AND ccu.table_schema = 'wes_runtime'
                      AND ccu.table_name = 'runtime_intent_logs'
                    """
                )
            )
            assert fk_count == 1
            index_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz'
                      AND tablename = 'smt_inbound_handoff_demands'
                      AND indexname = 'ix_smt_handoff_demands_exchange_intent'
                    """
                )
            )
            assert index_count == 1
            bin_slot_index = (
                await db.execute(
                    text(
                        """
                        SELECT is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'resource_bin_slot_templates'
                          AND column_name = 'bin_slot_index'
                        """
                    )
                )
            ).scalar_one()
            assert bin_slot_index == "NO"
            template_index_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz'
                      AND tablename = 'resource_bin_slot_templates'
                      AND indexname = 'ux_resource_bin_slot_templates_type_index'
                    """
                )
            )
            assert template_index_count == 1
            positive_index_check = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.table_constraints
                    WHERE constraint_schema = 'wes_biz'
                      AND table_name = 'resource_bin_slot_templates'
                      AND constraint_name = 'ck_resource_bin_slot_templates_positive_index'
                      AND constraint_type = 'CHECK'
                    """
                )
            )
            assert positive_index_check == 1

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_demand_row_lock_allows_one_active_root_then_suppresses_loser() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        service = service_type()
        projector = projector_type()
        async with session_factory() as setup_db:
            graph = await seed_exchange_graph(setup_db, full_bins=("FULL-1", "FULL-2"))
            await setup_db.commit()

        async with session_factory() as winner_db, session_factory() as loser_db:
            winner = await reserve_exchange(service, winner_db, graph, full_box_id="FULL-1")
            assert winner.created is True
            await prepare_reservation(
                projector=projector,
                db=winner_db,
                graph=graph,
                reservation=winner,
                intent_id=4101,
            )
            loser_task = asyncio.create_task(reserve_exchange(service, loser_db, graph, full_box_id="FULL-2"))
            done, _pending = await asyncio.wait({loser_task}, timeout=0.1)
            assert not done, "同一 handoff demand 的 loser 必须等待 winner 行锁"
            await winner_db.commit()
            loser = await asyncio.wait_for(loser_task, timeout=2)
            assert loser.created is False
            assert loser.request is None
            await loser_db.commit()

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(MaterialFlowOwner)) == 4

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_existing_handoff_demand_produces_one_domain_e11_intent_and_outbox() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            graph.demand.bin_snapshots_json = {"bins": [{"bin_code": "FULL-1", "usage": 0.9}]}
            assert graph.demand.id is not None
            db.add(
                ExecutionCorrelation(
                    correlation_id=f"smt-inbound-handoff:{graph.demand.id}",
                    execution_session_id=None,
                    trace_id=graph.demand.trace_id,
                    source_event_id=graph.demand.rack_release_id,
                    business_owner_key=graph.demand.demand_key,
                )
            )
            await db.commit()

        runtime = build_wms_effect_preparation_runtime(catalog=build_provider_catalog())
        bind_wms_effect_preparation_runtime(runtime)
        try:

            class _Gateway:
                def __init__(self) -> None:
                    self.targets: list[frozenset[OutboxDispatchTarget]] = []

                def enqueue_outbox(self, *, targets: object, limit: int = 50) -> None:
                    assert targets == frozenset({OutboxDispatchTarget.WMS_FULFILLMENT})
                    self.targets.append(frozenset(targets))

            gateway = _Gateway()
            async with session_factory() as db:
                summary = await _scan_smt_inbound_handoff_demands_in_transaction(
                    db,
                    service=SmtInboundHandoffService(),
                    scan_limit=1,
                    recovery_limit=0,
                    claim_limit=0,
                    stale_after_seconds=1,
                    legacy_limit=None,
                    queue_gateway=gateway,
                )
                assert summary["advanced"] == 1
                assert gateway.targets == [frozenset({OutboxDispatchTarget.WMS_FULFILLMENT})]
        finally:
            unbind_wms_effect_preparation_runtime(runtime)

        async with session_factory() as verify_db:
            demand = await verify_db.get(type(graph.demand), graph.demand.id)
            assert demand is not None
            assert demand.status == SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE
            assert demand.active_full_box_exchange_intent_id is not None
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 1

    asyncio.run(with_database(scenario, revision="head"))


@pytest.mark.integration
def test_e11_scanner_missing_persisted_correlation_rolls_back_without_intent_or_outbox() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            graph.demand.bin_snapshots_json = {"bins": [{"bin_code": "FULL-1", "usage": 0.9}]}
            await db.commit()

        async with session_factory() as db:
            summary = await _scan_smt_inbound_handoff_demands_in_transaction(
                db,
                service=SmtInboundHandoffService(),
                scan_limit=1,
                recovery_limit=0,
                claim_limit=0,
                stale_after_seconds=1,
                legacy_limit=None,
            )
            assert summary["recovery_errors"] == 1

        async with session_factory() as verify_db:
            demand = await verify_db.get(type(graph.demand), graph.demand.id)
            assert demand is not None
            assert demand.active_full_box_exchange_intent_id is None
            assert demand.status == SmtInboundHandoffDemandStatus.EVALUATING
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 0

    asyncio.run(with_database(scenario, revision="head"))


@pytest.mark.integration
def test_e11_demand_row_lock_promotes_loser_after_winner_rollback() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        service = service_type()
        projector = projector_type()
        async with session_factory() as setup_db:
            graph = await seed_exchange_graph(setup_db, full_bins=("FULL-1", "FULL-2"))
            await setup_db.commit()

        async with session_factory() as winner_db, session_factory() as loser_db:
            winner = await reserve_exchange(service, winner_db, graph, full_box_id="FULL-1")
            assert winner.created is True
            loser_task = asyncio.create_task(reserve_exchange(service, loser_db, graph, full_box_id="FULL-2"))
            done, _pending = await asyncio.wait({loser_task}, timeout=0.1)
            assert not done
            await winner_db.rollback()
            promoted = await asyncio.wait_for(loser_task, timeout=2)
            assert promoted.created is True
            assert promoted.request.full_box_id == "FULL-2"
            await prepare_reservation(
                projector=projector,
                db=loser_db,
                graph=graph,
                reservation=promoted,
                intent_id=4202,
            )
            await loser_db.commit()

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(MaterialFlowOwner)) == 4

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_station", r"station"),
        ("wrong_workline", r"workline"),
        ("not_arrived", r"ARRIVED|placement"),
        ("wrong_face", r"face|side"),
        ("reconciling_reservation", r"reservation"),
        ("related_pending_command", r"command|unfinished"),
        ("missing_demand_trace", r"trace"),
        ("unmapped_occupancy", r"mapping|occupancy.*template"),
    ],
)
def test_e11_stage_gate_rejects_invalid_locked_facts(mutation: str, message: str) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            if mutation == "wrong_station":
                graph.placement.position_code = "OTHER-STATION"
            elif mutation == "wrong_workline":
                graph.placement.workline_code = "OTHER-LINE"
            elif mutation == "not_arrived":
                graph.placement.placement_status = "DEPARTED"
            elif mutation == "wrong_face":
                graph.demand.full_box_exchange_rack_face = "B"
            elif mutation == "reconciling_reservation":
                await _insert_reservation(db, status="RECONCILING")
            elif mutation == "related_pending_command":
                await _insert_command(db, status="PENDING", trace_id="trace-e11")
            elif mutation == "missing_demand_trace":
                graph.demand.trace_id = None
            elif mutation == "unmapped_occupancy":
                occupancy = graph.bins["FULL-1"].occupancy
                occupancy.bin_cell_index = "99"
                occupancy.bin_cell_code = None
                occupancy.occupancy_status = "UNKNOWN"
            await db.flush()
            with pytest.raises((ValueError, RuntimeError), match=message):
                await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("status", "trace_id"),
    [("COMPLETED", "trace-e11"), ("PENDING", "unrelated-trace"), ("PENDING", None)],
)
def test_e11_stage_gate_allows_completed_related_or_unrelated_unfinished_command(
    status: str,
    trace_id: str,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            await _insert_command(db, status=status, trace_id=trace_id)
            reservation = await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            assert reservation.created is True
            assert reservation.request.full_box_id == "FULL-1"
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_unrelated_unfinished_command_is_not_locked_by_trace_exact_stage_gate() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as setup_db:
            graph = await seed_exchange_graph(setup_db)
            await _insert_command(setup_db, status="PENDING", trace_id="unrelated-trace")
            await setup_db.commit()

        async with session_factory() as locker_db, session_factory() as candidate_db:
            await locker_db.execute(
                text(
                    """
                    SELECT id
                    FROM wes_biz.device_commands
                    WHERE workline_id = 1 AND trace_id = 'unrelated-trace'
                    FOR UPDATE
                    """
                )
            )
            reservation_task = asyncio.create_task(
                reserve_exchange(service_type(), candidate_db, graph, full_box_id="FULL-1")
            )
            try:
                reservation = await asyncio.wait_for(asyncio.shield(reservation_task), timeout=0.2)
            except TimeoutError:
                await locker_db.rollback()
                await reservation_task
                pytest.fail("不同 trace 的 unfinished command 不得被 E11 stage gate 锁定")
            else:
                assert reservation.created is True
                await locker_db.rollback()
            await candidate_db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("used_depth", "occupancy_status", "prefer_full_box_exchange", "allowed"),
    [
        (Decimal("20"), "FULL", False, False),
        (Decimal("90"), "OCCUPIED", False, True),
        (Decimal("60"), "OCCUPIED", False, False),
        (Decimal("60"), "OCCUPIED", True, True),
    ],
)
def test_e11_stage_gate_uses_smt_usage_policy_thresholds(
    used_depth: Decimal,
    occupancy_status: str,
    prefer_full_box_exchange: bool,
    allowed: bool,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            occupancy = graph.bins["FULL-1"].occupancy
            occupancy.used_depth_mm = used_depth
            occupancy.remaining_depth_mm = Decimal("100") - used_depth
            occupancy.occupancy_status = occupancy_status
            if not allowed:
                with pytest.raises(ValueError, match=r"usage|threshold|full box"):
                    await reserve_exchange(
                        service_type(),
                        db,
                        graph,
                        full_box_id="FULL-1",
                        prefer_full_box_exchange=prefer_full_box_exchange,
                    )
            else:
                reservation = await reserve_exchange(
                    service_type(),
                    db,
                    graph,
                    full_box_id="FULL-1",
                    prefer_full_box_exchange=prefer_full_box_exchange,
                )
                assert reservation.created is True
                assert reservation.claim.prefer_full_box_exchange is prefer_full_box_exchange
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("used_depth", "prefer_full_box_exchange", "decision_status"),
    [
        (Decimal("90"), None, "REQUIRED_FULL_BOX_EXCHANGE_REQUESTED"),
        (Decimal("60"), True, "PREFERRED_FULL_BOX_EXCHANGE_REQUESTED"),
    ],
)
def test_e11_prepare_freezes_requested_usage_threshold_on_parent(
    used_depth: Decimal,
    prefer_full_box_exchange: bool | None,
    decision_status: str,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            occupancy = graph.bins["FULL-1"].occupancy
            occupancy.used_depth_mm = used_depth
            occupancy.remaining_depth_mm = Decimal("100") - used_depth
            reservation = await prepare_exchange(
                service=service_type(),
                projector=projector_type(),
                db=db,
                graph=graph,
                full_box_id="FULL-1",
                intent_id=4350,
                prefer_full_box_exchange=prefer_full_box_exchange,
            )
            assert reservation.claim.prefer_full_box_exchange is (prefer_full_box_exchange is True)
            assert graph.demand.decision_status == decision_status
            assert graph.demand.status == SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_followup_root_derives_frozen_preferred_threshold_and_rejects_conflict() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, full_bins=("FULL-1", "FULL-2"))
            for bin_facts in graph.bins.values():
                bin_facts.occupancy.used_depth_mm = Decimal("60")
                bin_facts.occupancy.remaining_depth_mm = Decimal("40")
                bin_facts.occupancy.occupancy_status = "OCCUPIED"
            await prepare_exchange(
                service=service_type(),
                projector=projector_type(),
                db=db,
                graph=graph,
                full_box_id="FULL-1",
                intent_id=4360,
                prefer_full_box_exchange=True,
            )
            graph.demand.active_full_box_exchange_intent_id = None
            await db.flush()

            derived = await reserve_exchange(
                service_type(),
                db,
                graph,
                full_box_id="FULL-2",
            )
            assert derived.claim is not None
            assert derived.claim.prefer_full_box_exchange is True

            with pytest.raises(ValueError, match=r"threshold.*conflict|conflict.*threshold"):
                await reserve_exchange(
                    service_type(),
                    db,
                    graph,
                    full_box_id="FULL-2",
                    prefer_full_box_exchange=False,
                )
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(("bin_slot_count", "allowed"), [(6, False), (1, True)])
def test_e11_stage_gate_counts_empty_template_slots_in_usage_capacity(
    bin_slot_count: int,
    allowed: bool,
) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, bin_slot_count=bin_slot_count)
            if allowed:
                reservation = await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
                assert reservation.created is True
            else:
                with pytest.raises(ValueError, match=r"usage|threshold|full box"):
                    await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize("drift", ["extra_duplicate", "identity_drift"])
def test_e11_freeze_rejects_source_multiset_drift(drift: str) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            source = graph.bins["FULL-1"].source_items[0]
            if drift == "extra_duplicate":
                duplicate = type(source)(
                    handoff_demand_id=source.handoff_demand_id,
                    item_key=f"{source.item_key}:duplicate",
                    bin_code=source.bin_code,
                    bin_cell_index=source.bin_cell_index,
                    bin_cell_code=source.bin_cell_code,
                    material_identity_key=source.material_identity_key,
                    pkg_code=source.pkg_code,
                    status=source.status,
                )
                db.add(duplicate)
            else:
                source.material_identity_key = "DRIFTED-IDENTITY"
            await db.flush()

            with pytest.raises(ValueError, match=r"material/source|source.*set|multiset|duplicate"):
                await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_freeze_rejects_material_without_quantity_snapshot() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            graph.bins["FULL-1"].material_mounts[0].qty_snapshot = None
            with pytest.raises(ValueError, match=r"quantity|qty"):
                await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize("owner_type", ["STATION_TRANSPORT", "PIECE_SORTING"])
def test_e11_prepare_hook_rejects_active_e09_or_piece_owner_atomically(owner_type: str) -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db)
            await _insert_conflicting_owner(db, owner_type=owner_type)
            reservation = await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            with pytest.raises((ValueError, RuntimeError), match=r"owner|conflict"):
                await prepare_reservation(
                    projector=projector_type(),
                    db=db,
                    graph=graph,
                    reservation=reservation,
                    intent_id=4301,
                )
            await db.rollback()

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(MaterialFlowOwner)) == 0

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_positive_request_is_frozen_from_exact_locked_database_facts() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, materials_per_full_bin=2)
            reservation = await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            request = reservation.request
            facts = graph.bins["FULL-1"]
            assert request.exchange_request_key == f"wms-e11:{graph.demand.id}:FULL-1"
            assert request.dispatch_key == request.exchange_request_key
            assert request.station_code == "FULL-BOX-EXCHANGE"
            assert request.rack_id == "SINGLE-1"
            assert request.rack_face == "A"
            assert request.full_box_id == "FULL-1"
            assert request.source_slot_id == "A-01"
            assert [
                (item.occupancy_id, item.pkg_id, item.material_code, item.quantity) for item in request.occupancies
            ] == [
                (
                    str(facts.occupancy.id),
                    material.pkg_code,
                    material.material_code,
                    material.qty_snapshot,
                )
                for material in facts.material_mounts
            ]
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_owner_acquisition_exists_only_after_real_preparation_hook() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        async with session_factory() as db:
            graph = await seed_exchange_graph(db, materials_per_full_bin=2)
            reservation = await reserve_exchange(service_type(), db, graph, full_box_id="FULL-1")
            assert await db.scalar(select(func.count()).select_from(MaterialFlowOwner)) == 0
            assert not hasattr(service_type(), "acquire_owners"), "owner acquisition 不得成为可绕过 runtime 的公开 API"
            await prepare_reservation(
                projector=projector_type(),
                db=db,
                graph=graph,
                reservation=reservation,
                intent_id=4401,
            )
            owners = (await db.scalars(select(MaterialFlowOwner))).all()
            facts = graph.bins["FULL-1"]
            assert {(owner.object_type, owner.object_key) for owner in owners} == {
                ("RACK", "SINGLE-1"),
                ("RACK_FACE", "SINGLE-1:A"),
                ("BIN", "FULL-1"),
                ("OCCUPANCY", str(facts.occupancy.id)),
            }
            assert all(owner.owner_type == "FULL_BOX_EXCHANGE" for owner in owners)
            assert all(owner.owner_key == reservation.request.exchange_request_key for owner in owners)
            assert all(owner.owner_intent_id == 4401 for owner in owners)
            await db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e11_prepare_statement_budget_is_constant_for_one_occupancy_with_many_materials() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = domain_types()
        counts: list[int] = []
        for index, material_count in enumerate((1, 10, 100), start=1):
            async with session_factory() as db:
                graph = await seed_exchange_graph(db, materials_per_full_bin=material_count)
                statements = 0

                def count_statement(*_args: Any, **_kwargs: Any) -> None:
                    nonlocal statements
                    statements += 1

                event.listen(db.bind.sync_engine, "before_cursor_execute", count_statement)
                try:
                    reservation = await prepare_exchange(
                        service=service_type(),
                        projector=projector_type(),
                        db=db,
                        graph=graph,
                        full_box_id="FULL-1",
                        intent_id=4500 + index,
                    )
                finally:
                    event.remove(db.bind.sync_engine, "before_cursor_execute", count_statement)
                assert len(reservation.request.occupancies) == material_count
                assert await db.scalar(select(func.count()).select_from(MaterialFlowOwner)) == 4
                counts.append(statements)
                await db.rollback()
        assert counts[1:] == [counts[0], counts[0]]

    asyncio.run(with_database(scenario))


def test_e11_postgresql_suite_is_pinned_to_g43_revision() -> None:
    assert REVISION == "f9ffbef8992a"
