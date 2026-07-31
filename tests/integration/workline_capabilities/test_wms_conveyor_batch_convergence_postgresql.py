"""E12 ACK、拒绝与 terminal 收敛的真实 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.effect_bridges import EffectTransportBridge, EffectTransportResolution
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import (
    MoveBinsToConveyorEntryResult,
    WmsEffectAck,
)
from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result
from tests.support.wms_conveyor_batch_postgresql import (
    E12,
    domain_types,
    prepare_reservation,
    reserve_batch,
    seed_batch_graph,
    with_database,
)


def _typed_ack(request: object) -> WmsEffectAck:
    payload = request.model_dump(mode="json")  # type: ignore[attr-defined]
    return WmsEffectAck.model_validate(
        build_typed_ack(E12, "idem-e12-convergence", payload, submission_state="ACCEPTED")
    )


def _success_result(request: object, ack: WmsEffectAck) -> MoveBinsToConveyorEntryResult:
    payload = request.model_dump(mode="json")  # type: ignore[attr-defined]
    return WMS_OPERATION_BY_IDENTITY[E12].result_model.model_validate(
        build_typed_result(
            E12,
            payload,
            source_version=3,
            completed_at="2026-07-30T09:00:00+00:00",
            provider_reference=ack.provider_reference,
        )
    )


def _partial_result(request: object, ack: WmsEffectAck) -> MoveBinsToConveyorEntryResult:
    payload = request.model_dump(mode="json")  # type: ignore[attr-defined]
    terminal = deepcopy(
        build_typed_result(
            E12,
            payload,
            source_version=4,
            completed_at="2026-07-30T09:01:00+00:00",
            provider_reference=ack.provider_reference,
        )
    )
    terminal["task_outcome"] = "PARTIAL_FAILURE"
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


@pytest.mark.integration
def test_e12_ack_accepts_every_member_without_advancing_physical_routes() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 2_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=70,
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

            await service.project_ack(
                db,
                request=reservation.request,
                ack=_typed_ack(reservation.request),
                occurred_at_ms=2_100,
                source_event_id="e12-ack-70",
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            routes = tuple(
                (
                    await db.execute(
                        select(BinRouteInstance)
                        .where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
                        .order_by(BinRouteInstance.route_instance_id)
                    )
                )
                .scalars()
                .all()
            )

            assert {member.member_state for member in members} == {"ACCEPTED"}
            assert {member.accepted_at_ms for member in members} == {2_100}
            assert {route.current_node for route in routes} == {"FIVE_RACK"}
            assert {route.route_version for route in routes} == {1}
            assert (
                await db.scalar(
                    select(ConveyorQueueMembership.id).where(ConveyorQueueMembership.workline_id == graph.workline_id)
                )
                is None
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_immediate_submit_reject_releases_members_and_closes_source_routes() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 3_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=71,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
            )
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert reservation.request is not None and prepared.intent_log is not None

            await service.project_reject(
                db,
                request=reservation.request,
                occurred_at_ms=3_100,
                source_event_id="e12-reject-71",
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            routes = tuple(
                (
                    await db.execute(
                        select(BinRouteInstance)
                        .where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
                        .order_by(BinRouteInstance.route_instance_id)
                    )
                )
                .scalars()
                .all()
            )

            assert {member.member_state for member in members} == {"RELEASED"}
            assert {member.reservation_released_at_ms for member in members} == {3_100}
            assert {route.lifecycle_state for route in routes} == {"CLOSED"}
            assert {route.current_node for route in routes} == {"FIVE_RACK"}
            assert {route.closed_at_ms for route in routes} == {3_100}

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_late_submit_reject_opens_case_and_freezes_physical_route_without_release() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 3_200)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=78,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
            )
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert reservation.request is not None and prepared.intent_log is not None
            routes = tuple(
                (
                    await db.execute(
                        select(BinRouteInstance)
                        .where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
                        .order_by(BinRouteInstance.route_instance_id)
                    )
                )
                .scalars()
                .all()
            )
            routes[0].current_node = "SCAN1"
            routes[0].route_version = 2
            routes[0].current_rack_code = None
            routes[0].current_slot_code = None
            routes[0].last_transition_source = "LOCAL_SCAN1"
            routes[0].last_transition_source_event_id = "scan1-before-reject"
            await db.flush()

            event = EffectReducerEvent(
                event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
                dispatch_key=reservation.request.dispatch_key,
                occurred_at_ms=3_201,
                source_event_id="e12-late-reject-78",
                attempt_no=1,
                reason_code="BATCH_MEMBER_INVALID",
                evidence_json={"operation_identity": E12},
            )
            await EffectTransportBridge(domain_projector=projector).record_result(
                db,
                dispatch_key=reservation.request.dispatch_key,
                attempt_no=1,
                result=SimpleNamespace(),
                retry_exhausted=False,
                occurred_at_ms=event.occurred_at_ms,
                operation_identity=E12,
                payload_json=reservation.request.model_dump(mode="json"),
                resolution=EffectTransportResolution(events=(event,)),
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            case = await db.scalar(
                select(ReconciliationCase).where(
                    ReconciliationCase.dispatch_key == reservation.request.dispatch_key,
                    ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                )
            )
            assert case is not None and case.id is not None
            assert {member.member_state for member in members} == {"CANDIDATE"}
            assert routes[0].current_node == "SCAN1"
            assert routes[0].lifecycle_state == "RECONCILING"
            assert routes[0].reconciliation_case_id == case.id
            assert routes[1].current_node == "FIVE_RACK"
            assert routes[1].lifecycle_state == "RECONCILING"
            assert routes[1].reconciliation_case_id == case.id

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_transport_not_sent_retry_exhausted_releases_pristine_batch_positions() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 3_300)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=79,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
            )
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert reservation.request is not None and prepared.intent_log is not None

            event = EffectReducerEvent(
                event_type=EffectReducerEventType.TRANSPORT_NOT_SENT,
                dispatch_key=reservation.request.dispatch_key,
                occurred_at_ms=3_301,
                source_event_id="e12-not-sent-exhausted-79",
                attempt_no=1,
                retry_exhausted=True,
                reason_code="DELIVERY_RETRY_EXHAUSTED",
                evidence_json={"operation_identity": E12},
            )
            await EffectTransportBridge(domain_projector=projector).record_result(
                db,
                dispatch_key=reservation.request.dispatch_key,
                attempt_no=1,
                result=SimpleNamespace(),
                retry_exhausted=True,
                occurred_at_ms=event.occurred_at_ms,
                operation_identity=E12,
                payload_json=reservation.request.model_dump(mode="json"),
                resolution=EffectTransportResolution(events=(event,)),
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            routes = tuple(
                (
                    await db.execute(
                        select(BinRouteInstance)
                        .where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
                        .order_by(BinRouteInstance.route_instance_id)
                    )
                )
                .scalars()
                .all()
            )
            intent_log = await db.get(RuntimeIntentLog, prepared.intent_log.id)
            open_case = await db.scalar(
                select(ReconciliationCase.id).where(
                    ReconciliationCase.dispatch_key == reservation.request.dispatch_key,
                    ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                )
            )
            assert intent_log is not None
            assert intent_log.effect_status == RuntimeIntentStatus.TECHNICAL_FAILED
            assert {member.member_state for member in members} == {"RELEASED"}
            assert {member.reservation_released_at_ms for member in members} == {3_301}
            assert {route.lifecycle_state for route in routes} == {"CLOSED"}
            assert {route.current_node for route in routes} == {"FIVE_RACK"}
            assert open_case is None

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_success_terminal_atomically_handoffs_reservations_to_entry_memberships() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 4_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=72,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
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
                occurred_at_ms=4_100,
                source_event_id="e12-ack-72",
            )

            await service.project_success(
                db,
                request=reservation.request,
                result=_success_result(reservation.request, ack),
                occurred_at_ms=4_200,
                source_event_id="e12-terminal-72",
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            routes = tuple(
                (
                    await db.execute(
                        select(BinRouteInstance)
                        .where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
                        .order_by(BinRouteInstance.route_instance_id)
                    )
                )
                .scalars()
                .all()
            )
            memberships = tuple(
                (
                    await db.execute(
                        select(ConveyorQueueMembership)
                        .where(ConveyorQueueMembership.workline_id == graph.workline_id)
                        .order_by(ConveyorQueueMembership.queue_position)
                    )
                )
                .scalars()
                .all()
            )

            assert {member.member_state for member in members} == {"TERMINAL"}
            assert {member.terminal_outcome for member in members} == {"SUCCESS"}
            assert {route.current_node for route in routes} == {"CONVEYOR_ENTRY"}
            assert {route.current_rack_code for route in routes} == {None}
            assert {route.current_slot_code for route in routes} == {None}
            assert tuple(membership.membership_status for membership in memberships) == ("ACTIVE", "ACTIVE")
            assert tuple(membership.queue_position for membership in memberships) == (1, 2)
            assert tuple(membership.route_instance_id for membership in memberships) == tuple(
                item.route_instance_id for item in reservation.request.items
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_late_success_terminal_does_not_regress_scan_route_or_create_ghost_entry() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 5_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=73,
                entry_capacity=1,
                ctu_capacity=1,
                bin_count=1,
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
                occurred_at_ms=5_100,
                source_event_id="e12-ack-73",
            )
            route = await db.scalar(
                select(BinRouteInstance).where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
            )
            assert route is not None
            route.current_node = "SCAN1"
            route.current_rack_code = None
            route.current_slot_code = None
            route.route_version = 3
            route.last_transition_source = "LOCAL_SCAN"
            route.last_transition_source_event_id = "scan1-73"
            await db.flush()

            await service.project_success(
                db,
                request=reservation.request,
                result=_success_result(reservation.request, ack),
                occurred_at_ms=5_200,
                source_event_id="e12-terminal-73",
            )
            await db.refresh(route)
            member = await db.scalar(
                select(WmsConveyorBatchMember).where(
                    WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id
                )
            )

            assert member is not None and member.member_state == "TERMINAL"
            assert route.current_node == "SCAN1"
            assert route.route_version == 3
            assert route.last_transition_source == "LOCAL_SCAN"
            assert (
                await db.scalar(
                    select(ConveyorQueueMembership.id).where(
                        ConveyorQueueMembership.route_instance_id == route.route_instance_id,
                        ConveyorQueueMembership.membership_status.in_(("ACTIVE", "RECONCILING")),
                    )
                )
                is None
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_partial_terminal_projects_known_success_failed_and_unknown_members_atomically() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 6_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=74,
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
                occurred_at_ms=6_100,
                source_event_id="e12-ack-74",
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
                opened_at_ms=6_200,
            )
            db.add(case)
            await db.flush()
            assert case.id is not None

            partial_result = _partial_result(reservation.request, ack)
            await projector.project_reconciliation_opened(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E12],
                dispatch_key=reservation.request.dispatch_key,
                reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
                evidence_json={
                    "snapshot": {
                        "result": partial_result.model_dump(mode="json"),
                    }
                },
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
            assert (failed.member_state, failed.terminal_outcome) == ("TERMINAL", "FAILED")
            assert (unknown.member_state, unknown.terminal_outcome) == ("TERMINAL", "UNKNOWN")
            assert unknown.reservation_released_at_ms is None
            assert routes[success.route_instance_id].current_node == "CONVEYOR_ENTRY"
            assert routes[success.route_instance_id].lifecycle_state == "ACTIVE"
            assert memberships[success.route_instance_id].membership_status == "ACTIVE"
            assert routes[failed.route_instance_id].current_node == "CONVEYOR_ENTRY"
            assert routes[failed.route_instance_id].lifecycle_state == "RECONCILING"
            assert routes[failed.route_instance_id].reconciliation_case_id == case.id
            assert memberships[failed.route_instance_id].membership_status == "RECONCILING"
            assert routes[unknown.route_instance_id].current_node == "FIVE_RACK"
            assert routes[unknown.route_instance_id].lifecycle_state == "RECONCILING"
            assert routes[unknown.route_instance_id].reconciliation_case_id == case.id
            assert unknown.route_instance_id not in memberships

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_status_reject_after_ack_terminalizes_members_and_closes_unmoved_routes() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 7_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=75,
                entry_capacity=2,
                ctu_capacity=2,
                bin_count=2,
            )
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert reservation.request is not None and prepared.intent_log is not None
            await service.project_ack(
                db,
                request=reservation.request,
                ack=_typed_ack(reservation.request),
                occurred_at_ms=7_100,
                source_event_id="e12-ack-75",
            )

            await service.project_status_reject(
                db,
                request=reservation.request,
                occurred_at_ms=7_200,
                source_event_id="e12-status-reject-75",
                reason_code="CONVEYOR_ENTRY_CAPACITY_CHANGED",
            )

            members = tuple(
                (
                    await db.execute(
                        select(WmsConveyorBatchMember).where(
                            WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            routes = tuple(
                (
                    await db.execute(
                        select(BinRouteInstance).where(
                            BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id
                        )
                    )
                )
                .scalars()
                .all()
            )

            assert {member.member_state for member in members} == {"TERMINAL"}
            assert {member.terminal_outcome for member in members} == {"REJECTED"}
            assert {member.accepted_at_ms for member in members} == {7_100}
            assert {member.reservation_released_at_ms for member in members} == {7_200}
            assert {route.lifecycle_state for route in routes} == {"CLOSED"}
            assert {route.current_node for route in routes} == {"FIVE_RACK"}
            assert {route.closed_at_ms for route in routes} == {7_200}

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_late_status_reject_opens_case_and_never_regresses_scan_route() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 8_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=76,
                entry_capacity=1,
                ctu_capacity=1,
                bin_count=1,
            )
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert reservation.request is not None and prepared.intent_log is not None
            await service.project_ack(
                db,
                request=reservation.request,
                ack=_typed_ack(reservation.request),
                occurred_at_ms=8_100,
                source_event_id="e12-ack-76",
            )
            route = await db.scalar(
                select(BinRouteInstance).where(BinRouteInstance.created_by_e12_intent_id == prepared.intent_log.id)
            )
            assert route is not None
            route.current_node = "SCAN1"
            route.current_rack_code = None
            route.current_slot_code = None
            route.route_version = 4
            route.last_transition_source = "LOCAL_SCAN"
            route.last_transition_source_event_id = "scan1-76"
            await db.flush()

            assert (
                await service.should_reconcile_status_reject(
                    db,
                    dispatch_key=reservation.request.dispatch_key,
                    queue_code=reservation.request.destination_station_code,
                )
                is True
            )
            intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
            assert intent is not None
            intent.effect_status = RuntimeIntentStatus.RECONCILING
            case = ReconciliationCase(
                runtime_intent_log_id=intent.id,
                dispatch_key=intent.dispatch_key,
                status=ReconciliationCaseStatus.OPEN,
                reason_code="WMS_E12_REJECT_AFTER_PHYSICAL_EVIDENCE",
                evidence_history_json=[],
                decision_json={},
                opened_at_ms=8_200,
            )
            db.add(case)
            await db.flush()

            await projector.project_reconciliation_opened(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E12],
                dispatch_key=reservation.request.dispatch_key,
                reason_code="WMS_E12_REJECT_AFTER_PHYSICAL_EVIDENCE",
                evidence_json={
                    "snapshot": {
                        "state": "REJECTED",
                        "reason_code": "CONVEYOR_ENTRY_CAPACITY_CHANGED",
                        "result": None,
                    }
                },
            )
            await db.refresh(route)
            member = await db.scalar(
                select(WmsConveyorBatchMember).where(WmsConveyorBatchMember.runtime_intent_log_id == intent.id)
            )

            assert member is not None and member.member_state == "ACCEPTED"
            assert route.current_node == "SCAN1"
            assert route.route_version == 4
            assert route.last_transition_source == "LOCAL_SCAN"
            assert route.lifecycle_state == "RECONCILING"
            assert route.reconciliation_case_id == case.id

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e12_generic_reconciliation_without_terminal_result_keeps_candidate_reservation() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        service_type, projector_type = domain_types()
        service = service_type(now_ms=lambda: 9_000)
        projector = projector_type(conveyor_batch=service)
        async with session_factory() as db:
            graph = await seed_batch_graph(
                db,
                graph_index=77,
                entry_capacity=1,
                ctu_capacity=1,
                bin_count=1,
            )
            reservation = await reserve_batch(service, db, graph)
            prepared = await prepare_reservation(
                projector=projector,
                db=db,
                graph=graph,
                reservation=reservation,
            )
            assert reservation.request is not None and prepared.intent_log is not None
            intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
            assert intent is not None
            intent.effect_status = RuntimeIntentStatus.RECONCILING
            case = ReconciliationCase(
                runtime_intent_log_id=intent.id,
                dispatch_key=intent.dispatch_key,
                status=ReconciliationCaseStatus.OPEN,
                reason_code="WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED",
                evidence_history_json=[],
                decision_json={},
                opened_at_ms=9_100,
            )
            db.add(case)
            await db.flush()

            await projector.project_reconciliation_opened(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E12],
                dispatch_key=reservation.request.dispatch_key,
                reason_code="WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED",
                evidence_json={"confirmation_budget": {"attempts": 3}},
            )

            member = await db.scalar(
                select(WmsConveyorBatchMember).where(WmsConveyorBatchMember.runtime_intent_log_id == intent.id)
            )
            route = await db.scalar(
                select(BinRouteInstance).where(BinRouteInstance.created_by_e12_intent_id == intent.id)
            )
            assert member is not None and route is not None
            assert member.member_state == "CANDIDATE"
            assert member.reservation_released_at_ms is None
            assert member.terminal_at_ms is None
            assert route.current_node == "FIVE_RACK"
            assert route.lifecycle_state == "RECONCILING"
            assert route.reconciliation_case_id == case.id
            assert (
                await db.scalar(
                    select(ConveyorQueueMembership.id).where(
                        ConveyorQueueMembership.route_instance_id == route.route_instance_id
                    )
                )
                is None
            )

    asyncio.run(with_database(scenario))
