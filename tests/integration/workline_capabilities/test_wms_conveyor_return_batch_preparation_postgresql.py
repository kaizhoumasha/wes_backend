"""E13 RETURN_QUEUE reserve/preparation 的 PostgreSQL 并发与原子性证据。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from importlib import import_module
from typing import Any

import pytest
from sqlalchemy import func, select

from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.runtime.orchestration.services.wms_conveyor_batch_service import WmsConveyorBatchService
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectPreparationRuntime
from src.app.sys.models.outbox import SystemOutbox
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.support.wms_conveyor_batch_postgresql import (
    execution_ctx,
    prepare_reservation,
    reserve_batch,
    seed_batch_graph,
    with_database,
)

RETURN_SERVICE_MODULE = "src.app.runtime.orchestration.services.wms_conveyor_return_batch_service"


def _return_service_type() -> type[Any]:
    return import_module(RETURN_SERVICE_MODULE).WmsConveyorReturnBatchService


async def _seed_return_queue(
    db: Any,
    *,
    graph_index: int,
    bin_count: int = 3,
) -> tuple[Any, tuple[Any, ...], int]:
    graph = await seed_batch_graph(
        db,
        graph_index=graph_index,
        entry_capacity=bin_count,
        ctu_capacity=bin_count,
        bin_count=bin_count,
    )
    inbound_service = WmsConveyorBatchService(id_factory=lambda: f"e13-source-{graph_index}")
    reservation = await reserve_batch(inbound_service, db, graph)
    prepared = await prepare_reservation(
        projector=WmsFulfillmentDomainProjector(conveyor_batch=inbound_service),
        db=db,
        graph=graph,
        reservation=reservation,
    )
    assert prepared.intent_log is not None
    await db.flush()
    routes = tuple(
        (
            await db.execute(
                select(BinRouteInstance).where(
                    BinRouteInstance.route_instance_id.in_(
                        tuple(item.route_instance_id for item in reservation.request.items)
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {route.route_instance_id: route for route in routes}
    fifo_items = (
        reservation.request.items[1],
        reservation.request.items[0],
        *reservation.request.items[2:],
    )
    for queue_position, item in enumerate(fifo_items, start=1):
        route = by_id[item.route_instance_id]
        route.current_node = "RETURN_QUEUE"
        route.current_rack_code = None
        route.current_slot_code = None
        route.route_version += 1
        route.last_transition_source = "TEST_SCAN3_RETURN"
        route.last_transition_source_event_id = f"test-return:{item.route_instance_id}"
        db.add(
            ConveyorQueueMembership(
                bin_code=item.bin_id,
                workline_id=graph.workline_id,
                conveyor_code="RETURN_CONVEYOR",
                queue_code=graph.config["return_queue"]["code"],
                queue_role="RETURN_QUEUE",
                membership_status="ACTIVE",
                entered_at=1000 + queue_position,
                route_instance_id=item.route_instance_id,
                scan3_enqueued_at=datetime(2026, 7, 30, 8, queue_position),
                queue_position=queue_position,
            )
        )
    await _add_ineligible_fifo_noise(
        db,
        workline_id=graph.workline_id,
        queue_code=graph.config["return_queue"]["code"],
        intent_id=prepared.intent_log.id,
    )
    await db.flush()
    return graph, fifo_items, prepared.intent_log.id


async def _add_ineligible_fifo_noise(
    db: Any,
    *,
    workline_id: int,
    queue_code: str,
    intent_id: int,
) -> None:
    """混入更早但不合格的行，证明 E13 查询依赖完整谓词。"""

    specs = (
        ("ENTRY", "ACTIVE", "RETURN_QUEUE", workline_id, queue_code, False),
        ("RETURN_QUEUE", "ACTIVE", "NG_LINE", workline_id, queue_code, False),
        ("RETURN_QUEUE", "LEFT", "RETURN_QUEUE", workline_id, queue_code, False),
        ("RETURN_QUEUE", "RECONCILING", "RETURN_QUEUE", workline_id, queue_code, False),
        ("RETURN_QUEUE", "ACTIVE", "RETURN_QUEUE", workline_id, queue_code, True),
        ("RETURN_QUEUE", "ACTIVE", "RETURN_QUEUE", workline_id + 1, queue_code, False),
        ("RETURN_QUEUE", "ACTIVE", "RETURN_QUEUE", workline_id, f"{queue_code}_OTHER", False),
    )
    routes: list[BinRouteInstance] = []
    memberships: list[ConveyorQueueMembership] = []
    for index, (role, status, node, row_workline_id, row_queue_code, claimed) in enumerate(specs, start=1):
        route_id = f"e13-noise:{workline_id}:{index}"
        bin_code = f"E13-NOISE-{workline_id}-{index}"
        routes.append(
            BinRouteInstance(
                route_instance_id=route_id,
                bin_code=bin_code,
                workline_id=row_workline_id,
                created_by_e12_intent_id=intent_id,
                current_node=node,
                route_version=1,
                lifecycle_state="ACTIVE",
                last_transition_source="TEST_E13_NOISE",
                last_transition_source_event_id=route_id,
            )
        )
        memberships.append(
            ConveyorQueueMembership(
                bin_code=bin_code,
                workline_id=row_workline_id,
                conveyor_code="RETURN_CONVEYOR",
                queue_code=row_queue_code,
                queue_role=role,
                membership_status=status,
                entered_at=100 + index,
                left_at=200 + index if status == "LEFT" else None,
                route_instance_id=route_id,
                scan3_enqueued_at=datetime(2026, 7, 30, 7, index),
                queue_position=10 + index,
                e13_claim_intent_id=intent_id if claimed else None,
                e13_claim_token=f"claimed-{index}" if claimed else None,
                e13_claim_until=datetime(2026, 7, 30, 9, 0) if claimed else None,
            )
        )
    db.add_all([*routes, *memberships])


async def _prepare_return_reservation(
    *,
    db: Any,
    graph: Any,
    reservation: Any,
    return_service: Any,
) -> Any:
    ctx = await execution_ctx(db, graph)
    ctx["wms_conveyor_return_batch_claim"] = reservation.claim
    capability_key, contract_version = reservation.operation.identity.rsplit("@", maxsplit=1)
    definition = SystemCapabilityIntentService().get_effect_definition(capability_key, contract_version)
    assert definition is not None
    intent = RuntimeIntent.system_capability(
        capability_key=capability_key,
        contract_version=contract_version,
        operation_key=reservation.request.batch_id,
        dispatch_key=reservation.request.dispatch_key,
        payload=reservation.request,
        precondition={"candidate_digest": reservation.request.candidate_digest},
        fact_version=reservation.request.candidate_digest,
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={
            "binding_id": ctx["plugin_binding"].id,
            "binding_version": ctx["plugin_binding"].binding_version,
        },
        provider_snapshot={"provider_code": "RUNTIME", "profile": definition.admission},
    )
    prepared = await SystemCapabilityIntentService().prepare_and_claim(ctx, intent)
    assert prepared.intent_log is not None
    execution = type(
        "_Execution",
        (),
        {
            "db": db,
            "ctx": ctx,
            "intent": intent,
            "intent_log": prepared.intent_log,
            "idempotency_key": prepared.idempotency_key,
        },
    )()
    await WmsEffectPreparationRuntime(
        catalog=build_provider_catalog(),
        allow_new_claim=lambda _definition: True,
        domain_projector=WmsFulfillmentDomainProjector(conveyor_return_batch=return_service),
    ).prepare(
        reservation.operation,
        reservation.request,
        execution=execution,
    )
    return prepared


@pytest.mark.integration
def test_e13_fifo_workers_filter_ineligible_rows_and_skip_locked_candidates() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as setup_db:
            graph, fifo_items, _source_intent_id = await _seed_return_queue(setup_db, graph_index=913)
            await setup_db.commit()

        service = _return_service_type()(id_factory=lambda: "worker")
        queue_code = graph.config["return_queue"]["code"]
        async with session_factory() as first_db, session_factory() as second_db:
            first = await service.reserve_batch(
                first_db,
                workline_id=graph.workline_id,
                queue_code=queue_code,
                max_candidate_count=2,
            )
            assert first.request is not None
            second = await service.reserve_batch(
                second_db,
                workline_id=graph.workline_id,
                queue_code=queue_code,
                max_candidate_count=2,
            )
            assert second.request is not None

            assert tuple(item.bin_id for item in first.request.candidate_items) == tuple(
                item.bin_id for item in fifo_items[:2]
            )
            assert tuple(item.queue_position for item in first.request.candidate_items) == (1, 2)
            assert tuple(item.bin_id for item in second.request.candidate_items) == (fifo_items[2].bin_id,)
            assert tuple(item.queue_position for item in second.request.candidate_items) == (3,)
            await first_db.rollback()
            await second_db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e13_preparation_claim_member_root_and_outbox_are_one_rollback_boundary() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as setup_db:
            graph, fifo_items, _source_intent_id = await _seed_return_queue(setup_db, graph_index=914)
            await setup_db.commit()

        queue_code = graph.config["return_queue"]["code"]
        return_service = _return_service_type()(
            id_factory=lambda: "rollback-winner",
            now_for_db=lambda: datetime(2026, 7, 30, 8, 30),
            now_ms=lambda: 1_234,
        )
        async with session_factory() as db:
            reservation = await return_service.reserve_batch(
                db,
                workline_id=graph.workline_id,
                queue_code=queue_code,
                max_candidate_count=2,
            )
            assert reservation.claim is not None and reservation.request is not None
            membership_ids = tuple(candidate.membership_id for candidate in reservation.claim.candidates)
            route_ids = tuple(candidate.route_instance_id for candidate in reservation.claim.candidates)
            routes_before = tuple(
                (
                    route.route_instance_id,
                    route.current_node,
                    route.route_version,
                    route.current_rack_code,
                    route.current_slot_code,
                )
                for route in (
                    (
                        await db.execute(
                            select(BinRouteInstance)
                            .where(BinRouteInstance.route_instance_id.in_(route_ids))
                            .order_by(BinRouteInstance.route_instance_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            )
            prepared = await _prepare_return_reservation(
                db=db,
                graph=graph,
                reservation=reservation,
                return_service=return_service,
            )
            await db.flush()

            memberships = tuple(
                (
                    await db.execute(
                        select(ConveyorQueueMembership)
                        .where(ConveyorQueueMembership.id.in_(membership_ids))
                        .order_by(ConveyorQueueMembership.id)
                    )
                )
                .scalars()
                .all()
            )
            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(
                            WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id,
                            WmsConveyorBatchMember.direction == "RETURN",
                        )
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            routes_after = tuple(
                (
                    route.route_instance_id,
                    route.current_node,
                    route.route_version,
                    route.current_rack_code,
                    route.current_slot_code,
                )
                for route in (
                    (
                        await db.execute(
                            select(BinRouteInstance)
                            .where(BinRouteInstance.route_instance_id.in_(route_ids))
                            .order_by(BinRouteInstance.route_instance_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            )
            assert all(membership.e13_claim_intent_id == prepared.intent_log.id for membership in memberships)
            assert all(membership.e13_claim_token == reservation.claim.claim_token for membership in memberships)
            assert all(membership.e13_claim_until == datetime(2026, 7, 30, 8, 31) for membership in memberships)
            assert tuple(member.source_queue_membership_id for member in members) == membership_ids
            assert all(
                member.direction == "RETURN"
                and member.member_state == "CANDIDATE"
                and member.accepted_at_ms is None
                and member.reserved_queue_position is None
                for member in members
            )
            assert routes_after == routes_before
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(RuntimeIntentLog)
                    .where(RuntimeIntentLog.dispatch_key == reservation.request.dispatch_key)
                )
            ) == 1
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(SystemOutbox)
                    .where(SystemOutbox.dispatch_key == reservation.request.dispatch_key)
                )
            ) == 1
            await db.rollback()

        async with session_factory() as verify_db:
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(RuntimeIntentLog)
                    .where(RuntimeIntentLog.dispatch_key == reservation.request.dispatch_key)
                )
            ) == 0
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(SystemOutbox)
                    .where(SystemOutbox.dispatch_key == reservation.request.dispatch_key)
                )
            ) == 0
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(WmsConveyorBatchMember)
                    .where(WmsConveyorBatchMember.source_queue_membership_id.in_(membership_ids))
                )
            ) == 0
            rolled_back_memberships = tuple(
                (
                    await verify_db.execute(
                        select(ConveyorQueueMembership)
                        .where(ConveyorQueueMembership.id.in_(membership_ids))
                        .order_by(ConveyorQueueMembership.id)
                    )
                )
                .scalars()
                .all()
            )
            assert all(
                membership.e13_claim_intent_id is None
                and membership.e13_claim_token is None
                and membership.e13_claim_until is None
                for membership in rolled_back_memberships
            )
            replay = await return_service.reserve_batch(
                verify_db,
                workline_id=graph.workline_id,
                queue_code=queue_code,
                max_candidate_count=2,
            )
            assert replay.request is not None
            assert tuple(item.bin_id for item in replay.request.candidate_items) == tuple(
                item.bin_id for item in fifo_items[:2]
            )
            await verify_db.rollback()

    asyncio.run(with_database(scenario))
