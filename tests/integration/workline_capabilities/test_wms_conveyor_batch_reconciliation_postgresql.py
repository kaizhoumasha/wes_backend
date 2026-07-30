"""E12 late reconciliation 的真实 PostgreSQL 冻结与事实保留合同。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import effect_reducer
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import (
    MoveBinsToConveyorEntryResult,
    WmsEffectAck,
)
from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result
from tests.support.wms_conveyor_batch_postgresql import (
    E12,
    NOW,
    domain_types,
    prepare_reservation,
    reserve_batch,
    seed_batch_graph,
    with_database,
)


def _typed_ack(request: object) -> WmsEffectAck:
    payload = request.model_dump(mode="json")  # type: ignore[attr-defined]
    return WmsEffectAck.model_validate(
        build_typed_ack(E12, "idem-e12-reconciliation", payload, submission_state="ACCEPTED")
    )


def _result(
    request: object,
    ack: WmsEffectAck,
    *,
    task_outcome: str = "SUCCESS",
) -> MoveBinsToConveyorEntryResult:
    payload = request.model_dump(mode="json")  # type: ignore[attr-defined]
    terminal = build_typed_result(
        E12,
        payload,
        source_version=11,
        completed_at="2026-07-30T09:11:00+00:00",
        provider_reference=ack.provider_reference,
    )
    terminal["task_outcome"] = task_outcome
    if task_outcome != "SUCCESS":
        terminal["items"][1]["item_outcome"] = "FAILED"
        terminal["items"][2].update(
            {
                "item_outcome": "UNKNOWN",
                "final_rack_id": None,
                "final_slot_id": None,
                "final_queue_position": None,
            }
        )
    return MoveBinsToConveyorEntryResult.model_validate(terminal)


def _all_unknown_result(request: object, ack: WmsEffectAck) -> MoveBinsToConveyorEntryResult:
    terminal = _result(request, ack, task_outcome="PARTIAL_FAILURE").model_dump(mode="json")
    for item in terminal["items"]:
        item.update(
            {
                "item_outcome": "UNKNOWN",
                "final_rack_id": None,
                "final_slot_id": None,
                "final_queue_position": None,
            }
        )
    return MoveBinsToConveyorEntryResult.model_validate(terminal)


async def _prepare_acked_batch(db, *, graph_index: int):  # type: ignore[no-untyped-def]
    service_type, projector_type = domain_types()
    service = service_type(now_ms=lambda: 11_000)
    projector = projector_type(conveyor_batch=service)
    graph = await seed_batch_graph(
        db,
        graph_index=graph_index,
        entry_capacity=3,
        ctu_capacity=3,
        bin_count=3,
    )
    reservation = await reserve_batch(service, db, graph)
    prepared = await prepare_reservation(
        projector=projector,
        db=db,
        graph=graph,
        reservation=reservation,
    )
    assert reservation.request is not None and prepared.intent_log is not None
    ack = _typed_ack(reservation.request)
    await service.project_ack(
        db,
        request=reservation.request,
        ack=ack,
        occurred_at_ms=11_100,
        source_event_id=f"e12-ack-{graph_index}",
    )
    return service, projector, graph, reservation, prepared, ack


@pytest.mark.integration
def test_e12_partial_late_routes_freeze_non_success_memberships_and_release_entry_reservation() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            service, projector, graph, reservation, prepared, ack = await _prepare_acked_batch(
                db,
                graph_index=80,
            )
            intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
            assert intent is not None
            intent.effect_status = RuntimeIntentStatus.RECONCILING
            case = ReconciliationCase(
                runtime_intent_log_id=intent.id,
                dispatch_key=intent.dispatch_key,
                status=ReconciliationCaseStatus.OPEN,
                reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
                evidence_history_json=[],
                decision_json={},
                opened_at_ms=11_200,
            )
            db.add(case)
            await db.flush()
            assert case.id is not None

            routes = {
                route.route_instance_id: route
                for route in (
                    (
                        await db.execute(
                            select(BinRouteInstance).where(BinRouteInstance.created_by_e12_intent_id == intent.id)
                        )
                    )
                    .scalars()
                    .all()
                )
            }
            success_item, failed_item, unknown_item = reservation.request.items
            routes[success_item.route_instance_id].current_node = "SCAN3"
            routes[failed_item.route_instance_id].current_node = "RETURN_QUEUE"
            routes[unknown_item.route_instance_id].current_node = "RETURN_QUEUE"
            for route in routes.values():
                route.current_rack_code = None
                route.current_slot_code = None
                route.route_version = 4
                route.last_transition_source = "LOCAL_SCAN"
                route.last_transition_source_event_id = f"late:{route.route_instance_id}"
            return_queue = str(graph.config["return_queue"]["code"])
            for item in reservation.request.items:
                db.add(
                    ConveyorQueueMembership(
                        bin_code=item.bin_id,
                        workline_id=graph.workline_id,
                        conveyor_code=return_queue,
                        queue_code=return_queue,
                        queue_role="RETURN_QUEUE",
                        membership_status="ACTIVE",
                        entered_at=11_150,
                        route_instance_id=item.route_instance_id,
                        scan3_enqueued_at=NOW,
                        queue_position=item.reserved_queue_position,
                        evidence_json={"source": "late-reconciliation-pg"},
                    )
                )
            await db.flush()

            partial = _result(reservation.request, ack, task_outcome="PARTIAL_FAILURE")
            await projector.project_reconciliation_opened(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E12],
                dispatch_key=reservation.request.dispatch_key,
                reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
                evidence_json={"snapshot": {"result": partial.model_dump(mode="json")}},
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == intent.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            memberships = {
                membership.route_instance_id: membership
                for membership in (
                    (
                        await db.execute(
                            select(ConveyorQueueMembership).where(
                                ConveyorQueueMembership.workline_id == graph.workline_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            }
            success, failed, unknown = members
            assert (success.member_state, success.terminal_outcome) == ("TERMINAL", "SUCCESS")
            assert routes[success.route_instance_id].lifecycle_state == "ACTIVE"
            assert memberships[success.route_instance_id].membership_status == "ACTIVE"
            assert (failed.member_state, failed.terminal_outcome) == ("TERMINAL", "FAILED")
            assert routes[failed.route_instance_id].lifecycle_state == "RECONCILING"
            assert memberships[failed.route_instance_id].membership_status == "RECONCILING"
            assert (unknown.member_state, unknown.terminal_outcome) == ("TERMINAL", "UNKNOWN")
            assert unknown.reservation_released_at_ms == case.opened_at_ms
            assert routes[unknown.route_instance_id].current_node == "RETURN_QUEUE"
            assert routes[unknown.route_instance_id].lifecycle_state == "RECONCILING"
            assert memberships[unknown.route_instance_id].membership_status == "RECONCILING"
            assert (
                await service._repository.lock_active_member_positions(
                    db,
                    workline_id=graph.workline_id,
                    queue_code=reservation.request.destination_station_code,
                )
                == frozenset()
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_unknown_preserves_closed_route_and_only_five_rack_keeps_entry_position() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            service, projector, graph, reservation, prepared, ack = await _prepare_acked_batch(
                db,
                graph_index=83,
            )
            intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
            assert intent is not None
            intent.effect_status = RuntimeIntentStatus.RECONCILING
            case = ReconciliationCase(
                runtime_intent_log_id=intent.id,
                dispatch_key=intent.dispatch_key,
                status=ReconciliationCaseStatus.OPEN,
                reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
                evidence_history_json=[],
                decision_json={},
                opened_at_ms=11_300,
            )
            db.add(case)
            await db.flush()
            assert case.id is not None
            routes = {
                route.route_instance_id: route
                for route in (
                    (
                        await db.execute(
                            select(BinRouteInstance).where(BinRouteInstance.created_by_e12_intent_id == intent.id)
                        )
                    )
                    .scalars()
                    .all()
                )
            }
            moved_unknown_item, closed_unknown_item, five_rack_unknown_item = reservation.request.items
            moved_route = routes[moved_unknown_item.route_instance_id]
            moved_route.current_node = "SCAN1"
            moved_route.current_rack_code = None
            moved_route.current_slot_code = None
            moved_route.route_version = 4
            moved_route.last_transition_source = "LOCAL_SCAN1"
            moved_route.last_transition_source_event_id = "local-scan1-83"
            closed_route = routes[closed_unknown_item.route_instance_id]
            closed_route.current_node = "NG_LINE"
            closed_route.current_rack_code = None
            closed_route.current_slot_code = None
            closed_route.route_version = 5
            closed_route.lifecycle_state = "CLOSED"
            closed_route.closed_at_ms = 11_350
            closed_route.last_transition_source = "LOCAL_NG"
            closed_route.last_transition_source_event_id = "local-ng-83"
            await db.flush()

            await projector.project_reconciliation_opened(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E12],
                dispatch_key=reservation.request.dispatch_key,
                reason_code="WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED",
                evidence_json={"confirmation_budget": {"attempts": 3}},
            )
            terminal = _all_unknown_result(reservation.request, ack)
            await projector.project_reconciliation_opened(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E12],
                dispatch_key=reservation.request.dispatch_key,
                reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
                evidence_json={"snapshot": {"result": terminal.model_dump(mode="json")}},
            )
            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == intent.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            moved_unknown, closed_unknown, five_rack_unknown = members
            assert (moved_unknown.member_state, moved_unknown.terminal_outcome) == (
                "TERMINAL",
                "UNKNOWN",
            )
            assert moved_unknown.reservation_released_at_ms == case.opened_at_ms
            assert moved_route.current_node == "SCAN1"
            assert moved_route.lifecycle_state == "RECONCILING"
            assert (closed_unknown.member_state, closed_unknown.terminal_outcome) == (
                "TERMINAL",
                "UNKNOWN",
            )
            assert closed_unknown.reservation_released_at_ms == case.opened_at_ms
            assert closed_route.current_node == "NG_LINE"
            assert closed_route.lifecycle_state == "CLOSED"
            assert closed_route.closed_at_ms == 11_350
            assert closed_route.reconciliation_case_id is None
            assert (
                await db.scalar(
                    select(ConveyorQueueMembership.id).where(
                        ConveyorQueueMembership.route_instance_id == closed_route.route_instance_id
                    )
                )
                is None
            )
            assert (five_rack_unknown.member_state, five_rack_unknown.terminal_outcome) == (
                "TERMINAL",
                "UNKNOWN",
            )
            assert five_rack_unknown.reservation_released_at_ms is None
            five_rack_route = routes[five_rack_unknown_item.route_instance_id]
            assert five_rack_route.current_node == "FIVE_RACK"
            assert five_rack_route.lifecycle_state == "RECONCILING"
            assert await service._repository.lock_active_member_positions(
                db,
                workline_id=graph.workline_id,
                queue_code=reservation.request.destination_station_code,
            ) == frozenset({five_rack_unknown.reserved_queue_position})

            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    moved_unknown.member_state = "CANDIDATE"
                    moved_unknown.accepted_at_ms = None
                    moved_unknown.reservation_released_at_ms = None
                    moved_unknown.terminal_at_ms = None
                    moved_unknown.terminal_outcome = None
                    moved_unknown.reserved_queue_position = five_rack_unknown.reserved_queue_position
                    await db.flush()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_success_terminal_contradictions_open_case_without_rewriting_terminal_facts() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            for graph_index, contradictory_event in (
                (81, EffectReducerEventType.STATUS_REJECTED),
                (82, EffectReducerEventType.RECONCILIATION_OPENED),
            ):
                service, projector, graph, reservation, prepared, ack = await _prepare_acked_batch(
                    db,
                    graph_index=graph_index,
                )
                await service.project_success(
                    db,
                    request=reservation.request,
                    result=_result(reservation.request, ack),
                    occurred_at_ms=12_000 + graph_index,
                    source_event_id=f"e12-success-{graph_index}",
                )
                intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
                assert intent is not None
                intent.effect_status = RuntimeIntentStatus.COMPLETED
                evidence = (
                    {"operation_identity": E12}
                    if contradictory_event is EffectReducerEventType.STATUS_REJECTED
                    else {
                        "snapshot": {
                            "result": _result(
                                reservation.request,
                                ack,
                                task_outcome="PARTIAL_FAILURE",
                            ).model_dump(mode="json")
                        }
                    }
                )
                event = EffectReducerEvent(
                    event_type=contradictory_event,
                    dispatch_key=reservation.request.dispatch_key,
                    occurred_at_ms=12_100 + graph_index,
                    source_event_id=f"e12-contradiction-{graph_index}",
                    reason_code="WMS_TERMINAL_FACT_CONTRADICTION",
                    evidence_json=evidence,
                )
                reduction = await effect_reducer.reduce(db, event)
                assert reduction.case_created is True
                await projector.project_reconciliation_opened(
                    db,
                    operation=WMS_OPERATION_BY_IDENTITY[E12],
                    dispatch_key=reservation.request.dispatch_key,
                    reason_code=event.reason_code,
                    evidence_json=evidence,
                )

                members = tuple(
                    (
                        await db.execute(
                            select(WmsConveyorBatchMember).where(
                                WmsConveyorBatchMember.runtime_intent_log_id == intent.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                routes = tuple(
                    (
                        await db.execute(
                            select(BinRouteInstance).where(BinRouteInstance.created_by_e12_intent_id == intent.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                memberships = tuple(
                    (
                        await db.execute(
                            select(ConveyorQueueMembership).where(
                                ConveyorQueueMembership.workline_id == graph.workline_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {(member.member_state, member.terminal_outcome) for member in members} == {
                    ("TERMINAL", "SUCCESS")
                }
                assert {route.lifecycle_state for route in routes} == {"RECONCILING"}
                assert {membership.membership_status for membership in memberships} == {"RECONCILING"}
                assert (
                    await db.scalar(
                        select(ReconciliationCase.id).where(
                            ReconciliationCase.dispatch_key == reservation.request.dispatch_key,
                            ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                        )
                    )
                    is not None
                )

    asyncio.run(with_database(scenario))
