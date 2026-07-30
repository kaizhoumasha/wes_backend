"""E13 prefix ACK、业务拒绝、对账保护与事务重放的 PostgreSQL 证据。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select

from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.effect_bridges import EffectTransportBridge, EffectTransportResolution
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.app.sys.models.outbox import SystemOutbox
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.ports.fulfillment_operations import (
    WmsAcceptedScope,
    WmsEffectAck,
    accepted_scope_digest,
)
from tests.integration.workline_capabilities.test_wms_conveyor_return_batch_preparation_postgresql import (
    _prepare_return_reservation,
    _return_service_type,
    _seed_return_queue,
)
from tests.mock.wms_northbound_contract import build_typed_ack
from tests.support.wms_conveyor_batch_postgresql import with_database

E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


class _StatusClaimRepository:
    def __init__(self, claim: Any) -> None:
        self.claim = claim

    async def get_claim_for_update(self, _db: Any, **_kwargs: Any) -> Any:
        return self.claim

    async def release_claim(self, _db: Any, **_kwargs: Any) -> bool:
        self.claim.intent.status_check_after = None
        self.claim.intent.status_check_lease_token = None
        self.claim.intent.status_check_lease_until = None
        return True


def _prefix_ack(request: Any, *, idempotency_key: str, prefix_count: int) -> WmsEffectAck:
    payload = request.model_dump(mode="json")
    raw = build_typed_ack(E13, idempotency_key, payload, submission_state="ACCEPTED")
    object_keys = tuple(item.bin_id for item in request.candidate_items[:prefix_count])
    raw["accepted_scope"] = WmsAcceptedScope(
        object_keys=object_keys,
        scope_digest=accepted_scope_digest(object_keys),
    ).model_dump(mode="json")
    return WmsEffectAck.model_validate(raw)


def _ack_event(request: Any, ack: WmsEffectAck, *, event_suffix: str) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_ACCEPTED,
        dispatch_key=request.dispatch_key,
        occurred_at_ms=7_300,
        source_event_id=f"e13-ack:{event_suffix}",
        attempt_no=1,
        reason_code="WMS_ASYNC_ACK_ACCEPTED",
        evidence_json={
            "operation_identity": E13,
            "typed_ack_hash": typed_wms_effect_ack_hash(ack),
        },
        terminal_outcome=Success(payload=ack).model_dump(mode="json"),
    )


async def _prepare_batch(
    db: Any,
    *,
    graph_index: int,
    bin_count: int = 5,
) -> tuple[Any, Any, Any, Any]:
    graph, fifo_items, _source_intent_id = await _seed_return_queue(
        db,
        graph_index=graph_index,
        bin_count=bin_count,
    )
    service = _return_service_type()(
        id_factory=lambda: f"ack-{graph_index}",
        now_ms=lambda: 7_000,
    )
    reservation = await service.reserve_batch(
        db,
        workline_id=graph.workline_id,
        queue_code=graph.config["return_queue"]["code"],
        max_candidate_count=bin_count,
    )
    assert reservation.request is not None and reservation.claim is not None
    prepared = await _prepare_return_reservation(
        db=db,
        graph=graph,
        reservation=reservation,
        return_service=service,
    )
    assert prepared.intent_log is not None
    return graph, fifo_items, service, (reservation, prepared)


async def _record_ack(
    db: Any,
    *,
    projector: WmsFulfillmentDomainProjector,
    request: Any,
    event: EffectReducerEvent,
) -> tuple[Any, ...]:
    return await EffectTransportBridge(domain_projector=projector).record_result(
        db,
        dispatch_key=request.dispatch_key,
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E13,
        payload_json=request.model_dump(mode="json"),
        resolution=EffectTransportResolution(events=(event,)),
    )


@pytest.mark.integration
@pytest.mark.parametrize("ack_source", ["initial", "status"])
def test_e13_prefix_two_of_five_releases_suffix_for_next_batch_and_replay_is_idempotent(
    ack_source: str,
) -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            graph, fifo_items, service, pair = await _prepare_batch(
                db,
                graph_index=930 if ack_source == "initial" else 931,
            )
            reservation, prepared = pair
            await db.commit()

        request = reservation.request
        ack = _prefix_ack(
            request,
            idempotency_key=prepared.idempotency_key,
            prefix_count=2,
        )
        ack_event = _ack_event(request, ack, event_suffix=ack_source)
        projector = WmsFulfillmentDomainProjector(conveyor_return_batch=service)
        async with session_factory() as db:
            if ack_source == "initial":
                reductions = await _record_ack(
                    db,
                    projector=projector,
                    request=request,
                    event=ack_event,
                )
                assert len(reductions) == 1
            else:
                intent = await db.scalar(
                    select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == request.dispatch_key)
                )
                outbox = await db.scalar(select(SystemOutbox).where(SystemOutbox.dispatch_key == request.dispatch_key))
                assert intent is not None and outbox is not None
                result = await WmsEffectStatusService(
                    domain_projector=projector,
                )._record_recovered_ack(
                    db,
                    claim=SimpleNamespace(intent=intent, outbox=outbox),
                    ack=ack,
                    source="status",
                )
                assert result is None
            await db.commit()

        async with session_factory() as replay_db:
            replay_reductions = await _record_ack(
                replay_db,
                projector=projector,
                request=request,
                event=ack_event,
            )
            assert len(replay_reductions) == 1
            await replay_db.commit()

        async with session_factory() as verify_db:
            members = tuple(
                (
                    await verify_db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            memberships = tuple(
                (
                    await verify_db.execute(
                        select(ConveyorQueueMembership)
                        .where(
                            ConveyorQueueMembership.id.in_(
                                tuple(member.source_queue_membership_id for member in members)
                            )
                        )
                        .order_by(ConveyorQueueMembership.queue_position)
                    )
                )
                .scalars()
                .all()
            )
            assert tuple(member.member_state for member in members) == (
                "ACCEPTED",
                "ACCEPTED",
                "RELEASED",
                "RELEASED",
                "RELEASED",
            )
            assert all(membership.e13_claim_intent_id == prepared.intent_log.id for membership in memberships[:2])
            assert all(
                membership.e13_claim_intent_id is None
                and membership.e13_claim_token is None
                and membership.e13_claim_until is None
                for membership in memberships[2:]
            )
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(ReconciliationCase)
                    .where(ReconciliationCase.dispatch_key == request.dispatch_key)
                )
            ) == 0
            next_batch = await service.reserve_batch(
                verify_db,
                workline_id=graph.workline_id,
                queue_code=graph.config["return_queue"]["code"],
                max_candidate_count=3,
            )
            assert next_batch.request is not None
            assert tuple(item.bin_id for item in next_batch.request.candidate_items) == tuple(
                item.bin_id for item in fifo_items[2:]
            )
            await verify_db.rollback()

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e13_zero_capacity_reject_releases_all_claims_without_status_task() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, _fifo_items, service, pair = await _prepare_batch(db, graph_index=932)
            reservation, prepared = pair
            await db.commit()

        request = reservation.request
        reject_event = EffectReducerEvent(
            event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
            dispatch_key=request.dispatch_key,
            occurred_at_ms=7_400,
            source_event_id="e13-zero-capacity",
            attempt_no=1,
            reason_code="NO_DESTINATION_CAPACITY",
            evidence_json={"operation_identity": E13},
        )
        projector = WmsFulfillmentDomainProjector(conveyor_return_batch=service)
        async with session_factory() as db:
            await EffectTransportBridge(domain_projector=projector).record_result(
                db,
                dispatch_key=request.dispatch_key,
                attempt_no=1,
                result=SimpleNamespace(),
                retry_exhausted=False,
                occurred_at_ms=reject_event.occurred_at_ms,
                operation_identity=E13,
                payload_json=request.model_dump(mode="json"),
                resolution=EffectTransportResolution(events=(reject_event,)),
            )
            await db.commit()

        async with session_factory() as verify_db:
            members = tuple(
                (
                    await verify_db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            memberships = tuple(
                (
                    await verify_db.execute(
                        select(ConveyorQueueMembership).where(
                            ConveyorQueueMembership.id.in_(
                                tuple(member.source_queue_membership_id for member in members)
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            intent = await verify_db.get(RuntimeIntentLog, prepared.intent_log.id)
            assert intent is not None
            assert intent.effect_status == RuntimeIntentStatus.REJECTED
            assert intent.status_check_after is None
            assert {member.member_state for member in members} == {"RELEASED"}
            assert all(
                membership.e13_claim_intent_id is None
                and membership.e13_claim_token is None
                and membership.e13_claim_until is None
                for membership in memberships
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e13_resubmit_reject_after_physical_action_opens_case_and_preserves_facts() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, _fifo_items, service, pair = await _prepare_batch(
                db,
                graph_index=937,
                bin_count=3,
            )
            reservation, prepared = pair
            await db.commit()

        request = reservation.request
        async with session_factory() as physical_db:
            first_member = await physical_db.scalar(
                select(WmsConveyorBatchMember)
                .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                .order_by(WmsConveyorBatchMember.sequence_no)
                .limit(1)
            )
            assert first_member is not None
            route = await physical_db.get(BinRouteInstance, first_member.route_instance_id)
            assert route is not None
            route.current_node = "CTU_RETURN_IN_FLIGHT"
            route.route_version += 1
            route.last_transition_source = "TEST_CTU_RETURN"
            route.last_transition_source_event_id = "test-ctu-return-before-resubmit"
            await physical_db.commit()

        projector = WmsFulfillmentDomainProjector(conveyor_return_batch=service)
        async with session_factory() as db:
            intent = await db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == request.dispatch_key)
            )
            outbox = await db.scalar(select(SystemOutbox).where(SystemOutbox.dispatch_key == request.dispatch_key))
            assert intent is not None and outbox is not None
            claim = SimpleNamespace(intent=intent, outbox=outbox, lease_token="lease-e13-resubmit")
            status_service = WmsEffectStatusService(
                repository=_StatusClaimRepository(claim),
                domain_projector=projector,
            )
            result = ExternalHttpTransportResult.accepted(
                http_status_code=409,
                protocol_result=ExternalHttpProtocolResult.REJECTED,
                protocol_error_code="NO_DESTINATION_CAPACITY",
                response_body=b'{"reason_code":"NO_DESTINATION_CAPACITY"}',
            )
            interpreted = BusinessReject(
                reason_code="NO_DESTINATION_CAPACITY",
                message="return destination has no capacity",
                details={"typed_reject_hash": "c" * 64},
            )
            with (
                patch.object(
                    WmsEffectStatusService,
                    "_build_request",
                    return_value=SimpleNamespace(
                        operation_identity=E13,
                        request_payload=request.model_dump(mode="json"),
                        frozen_ack=None,
                    ),
                ),
                patch(
                    "src.app.runtime.orchestration.services.wms_effect_status_service."
                    "interpret_async_effect_ack_response",
                    return_value=interpreted,
                ),
            ):
                recorded = await status_service._record_resubmit_result(
                    db,
                    claim=claim,
                    result=result,
                    evidence={"recovery": "original-key"},
                )
            assert recorded.outcome == "RECONCILING"

        async with session_factory() as verify_db:
            members = tuple(
                (
                    await verify_db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            memberships = tuple(
                (
                    await verify_db.execute(
                        select(ConveyorQueueMembership).where(
                            ConveyorQueueMembership.id.in_(
                                tuple(member.source_queue_membership_id for member in members)
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            route = await verify_db.get(BinRouteInstance, members[0].route_instance_id)
            case = await verify_db.scalar(
                select(ReconciliationCase).where(
                    ReconciliationCase.dispatch_key == request.dispatch_key,
                    ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                )
            )
            assert case is not None
            assert route is not None and route.current_node == "CTU_RETURN_IN_FLIGHT"
            assert {member.member_state for member in members} == {"CANDIDATE"}
            assert all(
                membership.e13_claim_intent_id == prepared.intent_log.id
                and membership.e13_claim_token is not None
                and membership.e13_claim_until is not None
                for membership in memberships
            )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("prefix_count", "expects_reconciliation"),
    [(2, False), (1, True)],
)
def test_e13_left_member_with_released_claim_is_valid_only_when_in_ack_prefix(
    prefix_count: int,
    expects_reconciliation: bool,
) -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, _fifo_items, service, pair = await _prepare_batch(
                db,
                graph_index=933 if expects_reconciliation else 934,
                bin_count=3,
            )
            reservation, prepared = pair
            await db.commit()

        request = reservation.request
        async with session_factory() as physical_db:
            members = tuple(
                (
                    await physical_db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            source = await physical_db.get(ConveyorQueueMembership, members[1].source_queue_membership_id)
            assert source is not None
            source.membership_status = "LEFT"
            source.left_at = 7_450
            source.e13_claim_intent_id = None
            source.e13_claim_token = None
            source.e13_claim_until = None
            await physical_db.commit()

        ack = _prefix_ack(
            request,
            idempotency_key=prepared.idempotency_key,
            prefix_count=prefix_count,
        )
        event = _ack_event(request, ack, event_suffix=f"left-{prefix_count}")
        projector = WmsFulfillmentDomainProjector(conveyor_return_batch=service)
        async with session_factory() as db:
            await _record_ack(db, projector=projector, request=request, event=event)
            await db.commit()

        async with session_factory() as verify_db:
            members = tuple(
                (
                    await verify_db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            case = await verify_db.scalar(
                select(ReconciliationCase).where(
                    ReconciliationCase.dispatch_key == request.dispatch_key,
                    ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                )
            )
            if expects_reconciliation:
                assert case is not None
                assert {member.member_state for member in members} == {"CANDIDATE"}
            else:
                assert case is None
                assert tuple(member.member_state for member in members) == (
                    "ACCEPTED",
                    "ACCEPTED",
                    "RELEASED",
                )

    asyncio.run(with_database(scenario))


@pytest.mark.integration
@pytest.mark.parametrize("fault_point", ["before_flush", "before_commit"])
def test_e13_ack_failure_rolls_back_and_same_event_replays_cleanly(fault_point: str) -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, _fifo_items, service, pair = await _prepare_batch(
                db,
                graph_index=935 if fault_point == "before_flush" else 936,
                bin_count=3,
            )
            reservation, prepared = pair
            await db.commit()

        request = reservation.request
        ack = _prefix_ack(
            request,
            idempotency_key=prepared.idempotency_key,
            prefix_count=2,
        )
        event = _ack_event(request, ack, event_suffix=fault_point)
        projector = WmsFulfillmentDomainProjector(conveyor_return_batch=service)
        async with session_factory() as failing_db:

            def fail_member_flush(session, _flush_context, _instances) -> None:  # type: ignore[no-untyped-def]
                if any(isinstance(value, WmsConveyorBatchMember) for value in session.dirty):
                    raise RuntimeError("injected E13 ACK flush failure")

            def fail_commit(_session) -> None:  # type: ignore[no-untyped-def]
                raise RuntimeError("injected E13 ACK commit failure")

            listener = fail_member_flush if fault_point == "before_flush" else fail_commit
            sqlalchemy_event.listen(failing_db.sync_session, fault_point, listener)
            try:
                with pytest.raises(
                    RuntimeError, match=f"injected E13 ACK {fault_point.removeprefix('before_')} failure"
                ):
                    await _record_ack(
                        failing_db,
                        projector=projector,
                        request=request,
                        event=event,
                    )
                    await failing_db.commit()
            finally:
                sqlalchemy_event.remove(failing_db.sync_session, fault_point, listener)
            await failing_db.rollback()

        async with session_factory() as replay_db:
            await _record_ack(
                replay_db,
                projector=projector,
                request=request,
                event=event,
            )
            await replay_db.commit()

        async with session_factory() as verify_db:
            members = tuple(
                (
                    await verify_db.execute(
                        select(WmsConveyorBatchMember)
                        .where(WmsConveyorBatchMember.runtime_intent_log_id == prepared.intent_log.id)
                        .order_by(WmsConveyorBatchMember.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            assert tuple(member.member_state for member in members) == (
                "ACCEPTED",
                "ACCEPTED",
                "RELEASED",
            )
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(ReconciliationCase)
                    .where(ReconciliationCase.dispatch_key == request.dispatch_key)
                )
            ) == 0

    asyncio.run(with_database(scenario))
