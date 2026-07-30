"""E13 terminal/reconciliation 的批量资源投影与原子回滚证据。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy import func, select

from src.app.resource.models import (
    Rack,
    RackBinMount,
    RackBinMountStatus,
    RackKind,
    RackPlacement,
    RackSlotKind,
    RackSlotSide,
    RackSlotTemplate,
    RackType,
    ResourceSourceSystem,
)
from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.wms_integration.ports.fulfillment_operations import (
    MoveBinsFromConveyorExitResult,
    accepted_scope_digest,
)
from tests.integration.workline_capabilities.test_wms_conveyor_return_batch_ack_postgresql import (
    _prefix_ack,
    _prepare_batch,
)
from tests.mock.wms_northbound_contract import build_typed_result
from tests.support.wms_conveyor_batch_postgresql import NOW, with_database

E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


async def _prepare_acked_batch(db: Any, *, graph_index: int) -> tuple[Any, Any, Any, Any, Any, str]:
    graph, _fifo_items, service, pair = await _prepare_batch(
        db,
        graph_index=graph_index,
        bin_count=3,
    )
    reservation, prepared = pair
    request = reservation.request
    ack = _prefix_ack(
        request,
        idempotency_key=prepared.idempotency_key,
        prefix_count=3,
    )
    await service.project_ack(
        db,
        request=request,
        ack=ack,
        occurred_at_ms=12_000,
        source_event_id=f"e13-terminal-ack:{graph_index}",
    )
    prepared.intent_log.outcome_json = {
        "outcome": {
            "kind": "success",
            "payload": ack.model_dump(mode="json"),
        }
    }

    current_placement = await db.scalar(
        select(RackPlacement).where(
            RackPlacement.workline_id == graph.workline_id,
            RackPlacement.position_code == "TARGET_STATION",
            RackPlacement.ended_at.is_(None),
        )
    )
    assert current_placement is not None
    current_placement.ended_at = NOW
    destination_rack_code = f"E13-DEST-{graph_index}"
    rack_type = RackType(
        rack_type_code=f"E13-DEST-TYPE-{graph_index}",
        rack_type_name=f"E13 terminal destination {graph_index}",
        rack_kind=RackKind.FIVE_LAYER,
        slot_count=3,
        has_side=True,
    )
    rack = Rack(
        rack_code=destination_rack_code,
        rack_type_code=rack_type.rack_type_code,
        source_system=ResourceSourceSystem.WMS,
    )
    slots = tuple(
        RackSlotTemplate(
            rack_type_code=rack_type.rack_type_code,
            slot_code=f"RETURN-{index}",
            side=RackSlotSide.A,
            layer_no=index,
            position_no=1,
            slot_kind=RackSlotKind.BIN_SLOT,
        )
        for index in range(1, 4)
    )
    placement = RackPlacement(
        rack_code=destination_rack_code,
        rack_kind=RackKind.FIVE_LAYER,
        workline_id=graph.workline_id,
        workline_code=current_placement.workline_code,
        position_code="TARGET_STATION",
        position_role=current_placement.position_role,
        placement_status="ARRIVED",
        source_system=ResourceSourceSystem.WMS,
        source_event_id=f"e13-destination-arrived:{graph_index}",
        started_at=NOW,
    )
    db.add_all([rack_type, rack, *slots, placement])
    await db.flush()
    return graph, service, request, prepared, ack, destination_rack_code


def _terminal_result(
    request: Any,
    *,
    destination_rack_code: str,
    provider_reference: str,
    outcomes: tuple[str, str, str] = ("SUCCESS", "SUCCESS", "SUCCESS"),
) -> MoveBinsFromConveyorExitResult:
    payload = build_typed_result(
        E13,
        request.model_dump(mode="json"),
        source_version=12,
        completed_at="2026-07-30T10:12:00+00:00",
        provider_reference=provider_reference,
    )
    payload["task_outcome"] = (
        "SUCCESS"
        if set(outcomes) == {"SUCCESS"}
        else "PARTIAL_FAILURE"
        if "SUCCESS" in outcomes
        else "FAILED_AFTER_EXECUTION"
    )
    for index, (item, outcome) in enumerate(zip(payload["items"], outcomes, strict=True), start=1):
        item["item_outcome"] = outcome
        if outcome == "UNKNOWN":
            item.update(
                {
                    "final_rack_id": None,
                    "final_slot_id": None,
                    "final_queue_position": None,
                }
            )
        else:
            item["final_rack_id"] = destination_rack_code
            item["final_slot_id"] = f"RETURN-{index}"
    return MoveBinsFromConveyorExitResult.model_validate(payload)


async def _members(db: Any, *, intent_id: int) -> tuple[WmsConveyorBatchMember, ...]:
    return tuple(
        (
            await db.execute(
                select(WmsConveyorBatchMember)
                .where(WmsConveyorBatchMember.runtime_intent_log_id == intent_id)
                .order_by(WmsConveyorBatchMember.sequence_no)
            )
        )
        .scalars()
        .all()
    )


async def _assert_physical_first_preserved(db: Any, *, graph_index: int, same_target: bool) -> None:
    _graph, service, request, prepared, ack, destination_rack = await _prepare_acked_batch(
        db,
        graph_index=graph_index,
    )
    members = await _members(db, intent_id=prepared.intent_log.id)
    first = members[0]
    route = await db.get(BinRouteInstance, first.route_instance_id)
    source = await db.get(ConveyorQueueMembership, first.source_queue_membership_id)
    mount = await db.scalar(
        select(RackBinMount).where(
            RackBinMount.bin_code == first.bin_code,
            RackBinMount.ended_at.is_(None),
        )
    )
    assert route is not None and source is not None and mount is not None and mount.id is not None
    mount_id = mount.id
    mount.rack_code = destination_rack
    mount.rack_slot_code = "RETURN-1"
    mount.source_event_id = f"local-physical-first:{graph_index}"
    route.current_node = "FIVE_RACK"
    route.current_rack_code = destination_rack
    route.current_slot_code = "RETURN-1"
    route.route_version += 1
    preserved_version = route.route_version
    route.last_transition_source = "LOCAL_PHYSICAL"
    route.last_transition_source_event_id = f"local-physical-first:{graph_index}"
    source.membership_status = "LEFT"
    source.left_at = 12_700
    source.e13_claim_intent_id = None
    source.e13_claim_token = None
    source.e13_claim_until = None
    await db.flush()

    if same_target:
        result = _terminal_result(
            request,
            destination_rack_code=destination_rack,
            provider_reference=ack.provider_reference,
        )
        await service.project_success(
            db,
            request=request,
            result=result,
            occurred_at_ms=12_710,
            source_event_id=f"e13-terminal-after-local:{graph_index}",
        )
    else:
        result = _terminal_result(
            request,
            destination_rack_code=destination_rack,
            provider_reference=ack.provider_reference,
            outcomes=("FAILED", "UNKNOWN", "UNKNOWN"),
        )
        raw = result.model_dump(mode="json")
        raw["items"][0]["final_slot_id"] = "WMS-DIFFERENT-SLOT"
        result = MoveBinsFromConveyorExitResult.model_validate(raw)
        intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
        assert intent is not None
        intent.effect_status = RuntimeIntentStatus.RECONCILING
        case = ReconciliationCase(
            runtime_intent_log_id=intent.id,
            dispatch_key=intent.dispatch_key,
            status=ReconciliationCaseStatus.OPEN,
            reason_code="WMS_E13_PHYSICAL_TERMINAL_CONFLICT",
            evidence_history_json=[],
            decision_json={},
            opened_at_ms=12_720,
        )
        db.add(case)
        await db.flush()
        assert case.id is not None
        await service.project_reconciliation(
            db,
            request=request,
            result=result,
            reconciliation_case_id=case.id,
            occurred_at_ms=12_730,
            source_event_id=f"e13-terminal-conflicts-local:{graph_index}",
            reason_code=case.reason_code,
        )

    assert route.current_node == "FIVE_RACK"
    assert (route.current_rack_code, route.current_slot_code) == (destination_rack, "RETURN-1")
    assert route.route_version == preserved_version
    assert route.last_transition_source == "LOCAL_PHYSICAL"
    assert route.last_transition_source_event_id == f"local-physical-first:{graph_index}"
    active_mount = await db.scalar(
        select(RackBinMount).where(
            RackBinMount.bin_code == first.bin_code,
            RackBinMount.ended_at.is_(None),
        )
    )
    assert active_mount is not None
    assert active_mount.id == mount_id
    assert active_mount.source_event_id == f"local-physical-first:{graph_index}"


@pytest.mark.integration
def test_e13_all_success_atomically_moves_mounts_and_closes_return_routes() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, service, request, prepared, ack, destination_rack = await _prepare_acked_batch(
                db,
                graph_index=940,
            )
            prepared_members = await _members(db, intent_id=prepared.intent_log.id)
            stale_mount = await db.scalar(
                select(RackBinMount).where(
                    RackBinMount.bin_code == prepared_members[0].bin_code,
                    RackBinMount.ended_at.is_(None),
                )
            )
            assert stale_mount is not None and stale_mount.id is not None
            stale_mount_id = stale_mount.id
            stale_mount.rack_code = destination_rack
            stale_mount.rack_slot_code = "RETURN-1"
            stale_mount.source_event_id = "stale-source-mount:940"
            await db.flush()
            result = _terminal_result(
                request,
                destination_rack_code=destination_rack,
                provider_reference=ack.provider_reference,
            )

            await service.project_success(
                db,
                request=request,
                result=result,
                occurred_at_ms=12_100,
                source_event_id="e13-terminal-success:940",
            )
            await db.commit()

        async with session_factory() as verify_db:
            members = await _members(verify_db, intent_id=prepared.intent_log.id)
            routes = tuple(
                (
                    await verify_db.execute(
                        select(BinRouteInstance)
                        .where(
                            BinRouteInstance.route_instance_id.in_(
                                tuple(member.route_instance_id for member in members)
                            )
                        )
                        .order_by(BinRouteInstance.route_instance_id)
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
            mounts = tuple(
                (
                    await verify_db.execute(
                        select(RackBinMount)
                        .where(
                            RackBinMount.bin_code.in_(tuple(member.bin_code for member in members)),
                            RackBinMount.ended_at.is_(None),
                        )
                        .order_by(RackBinMount.bin_code)
                    )
                )
                .scalars()
                .all()
            )
            assert {(member.member_state, member.terminal_outcome) for member in members} == {("TERMINAL", "SUCCESS")}
            assert {(route.lifecycle_state, route.current_node, route.current_rack_code) for route in routes} == {
                ("CLOSED", "FIVE_RACK", destination_rack)
            }
            assert all(
                membership.membership_status == "LEFT"
                and membership.left_at == 12_100
                and membership.e13_claim_intent_id is None
                for membership in memberships
            )
            assert {(mount.rack_code, mount.rack_slot_code) for mount in mounts} == {
                (destination_rack, "RETURN-1"),
                (destination_rack, "RETURN-2"),
                (destination_rack, "RETURN-3"),
            }
            replaced = next(mount for mount in mounts if mount.bin_code == members[0].bin_code)
            assert replaced.id != stale_mount_id
            assert replaced.source_event_id == "e13-terminal-success:940"

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e13_partial_terminal_projects_known_mounts_and_freezes_unknown_without_fabrication() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, service, request, prepared, ack, destination_rack = await _prepare_acked_batch(
                db,
                graph_index=941,
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
                opened_at_ms=12_200,
            )
            db.add(case)
            await db.flush()
            assert case.id is not None
            result = _terminal_result(
                request,
                destination_rack_code=destination_rack,
                provider_reference=ack.provider_reference,
                outcomes=("SUCCESS", "FAILED", "UNKNOWN"),
            )

            await service.project_reconciliation(
                db,
                request=request,
                result=result,
                reconciliation_case_id=case.id,
                occurred_at_ms=12_210,
                source_event_id="e13-terminal-partial:941",
                reason_code=case.reason_code,
            )
            await db.commit()

        async with session_factory() as verify_db:
            members = await _members(verify_db, intent_id=prepared.intent_log.id)
            routes = {
                route.route_instance_id: route
                for route in (
                    (
                        await verify_db.execute(
                            select(BinRouteInstance).where(
                                BinRouteInstance.route_instance_id.in_(
                                    tuple(member.route_instance_id for member in members)
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            }
            memberships = {
                membership.id: membership
                for membership in (
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
            }
            success, failed, unknown = members
            assert (success.member_state, success.terminal_outcome) == ("TERMINAL", "SUCCESS")
            assert routes[success.route_instance_id].lifecycle_state == "CLOSED"
            assert (failed.member_state, failed.terminal_outcome) == ("TERMINAL", "FAILED")
            assert routes[failed.route_instance_id].lifecycle_state == "RECONCILING"
            assert routes[failed.route_instance_id].current_node == "FIVE_RACK"
            assert memberships[failed.source_queue_membership_id].membership_status == "LEFT"
            for member in (success, failed):
                source = memberships[member.source_queue_membership_id]
                assert (
                    source.membership_status,
                    source.e13_claim_intent_id,
                    source.e13_claim_token,
                    source.e13_claim_until,
                ) == ("LEFT", None, None, None)
                active_mount = await verify_db.scalar(
                    select(RackBinMount).where(
                        RackBinMount.bin_code == member.bin_code,
                        RackBinMount.ended_at.is_(None),
                    )
                )
                assert active_mount is not None
                assert (active_mount.rack_code, active_mount.rack_slot_code) == (
                    destination_rack,
                    f"RETURN-{member.sequence_no}",
                )
            assert (unknown.member_state, unknown.terminal_outcome) == ("TERMINAL", "UNKNOWN")
            assert routes[unknown.route_instance_id].current_node == "RETURN_QUEUE"
            assert routes[unknown.route_instance_id].lifecycle_state == "RECONCILING"
            assert memberships[unknown.source_queue_membership_id].membership_status == "RECONCILING"
            assert memberships[unknown.source_queue_membership_id].e13_claim_intent_id == prepared.intent_log.id
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(RackBinMount)
                    .where(
                        RackBinMount.bin_code == unknown.bin_code,
                        RackBinMount.rack_code == destination_rack,
                        RackBinMount.ended_at.is_(None),
                    )
                )
            ) == 0

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e13_target_slot_conflict_rolls_back_every_domain_fact() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, service, request, prepared, ack, destination_rack = await _prepare_acked_batch(
                db,
                graph_index=942,
            )
            intent_id = prepared.intent_log.id
            authoritative_ack = deepcopy(prepared.intent_log.outcome_json)
            drifted_ack = deepcopy(authoritative_ack)
            drifted_keys = tuple(ack.accepted_scope.object_keys[:2])
            drifted_ack["outcome"]["payload"]["accepted_scope"] = {
                "object_keys": list(drifted_keys),
                "scope_digest": accepted_scope_digest(drifted_keys),
            }
            prepared.intent_log.outcome_json = drifted_ack
            valid_result = _terminal_result(
                request,
                destination_rack_code=destination_rack,
                provider_reference=ack.provider_reference,
            )
            with pytest.raises(ValueError, match=r"accepted members do not match ACK"):
                await service.project_success(
                    db,
                    request=request,
                    result=valid_result,
                    occurred_at_ms=12_290,
                    source_event_id="e13-terminal-ack-scope-drift:942",
                )
            prepared.intent_log.outcome_json = authoritative_ack
            provider_drift = _terminal_result(
                request,
                destination_rack_code=destination_rack,
                provider_reference="different-provider-reference",
            )
            with pytest.raises(ValueError, match=r"provider_reference"):
                await service.project_success(
                    db,
                    request=request,
                    result=provider_drift,
                    occurred_at_ms=12_291,
                    source_event_id="e13-terminal-ack-provider-drift:942",
                )
            assert {member.member_state for member in await _members(db, intent_id=intent_id)} == {"ACCEPTED"}
            await db.commit()
            db.add(
                RackBinMount(
                    rack_code=destination_rack,
                    rack_slot_code="RETURN-2",
                    bin_code="E13-CONFLICT-942",
                    mount_status=RackBinMountStatus.MOUNTED,
                    source_system=ResourceSourceSystem.WMS,
                    source_event_id="e13-target-conflict:942",
                    started_at=NOW,
                )
            )
            await db.flush()
            result = valid_result

            with pytest.raises(ValueError, match=r"target.*occupied|destination.*occupied"):
                await service.project_success(
                    db,
                    request=request,
                    result=result,
                    occurred_at_ms=12_300,
                    source_event_id="e13-terminal-conflict:942",
                )
            await db.rollback()

        async with session_factory() as verify_db:
            members = await _members(verify_db, intent_id=intent_id)
            assert {member.member_state for member in members} == {"ACCEPTED"}
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
            assert all(membership.e13_claim_intent_id == intent_id for membership in memberships)

    asyncio.run(with_database(scenario))


@pytest.mark.integration
def test_e13_duplicate_and_late_conflicting_terminal_preserve_first_result() -> None:
    async def scenario(session_factory) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            _graph, service, request, prepared, ack, destination_rack = await _prepare_acked_batch(
                db,
                graph_index=943,
            )
            first = _terminal_result(
                request,
                destination_rack_code=destination_rack,
                provider_reference=ack.provider_reference,
            )
            await service.project_success(
                db,
                request=request,
                result=first,
                occurred_at_ms=12_400,
                source_event_id="e13-terminal-first:943",
            )
            await service.project_success(
                db,
                request=request,
                result=first,
                occurred_at_ms=12_401,
                source_event_id="e13-terminal-replay:943",
            )
            intent = await db.get(RuntimeIntentLog, prepared.intent_log.id)
            assert intent is not None
            intent.effect_status = RuntimeIntentStatus.RECONCILING
            case = ReconciliationCase(
                runtime_intent_log_id=intent.id,
                dispatch_key=intent.dispatch_key,
                status=ReconciliationCaseStatus.OPEN,
                reason_code="WMS_E13_TERMINAL_CONFLICT",
                evidence_history_json=[],
                decision_json={},
                opened_at_ms=12_410,
            )
            db.add(case)
            await db.flush()
            assert case.id is not None
            late = deepcopy(first.model_dump(mode="json"))
            late["task_outcome"] = "PARTIAL_FAILURE"
            late["items"][0]["item_outcome"] = "FAILED"
            conflicting = MoveBinsFromConveyorExitResult.model_validate(late)
            await service.project_reconciliation(
                db,
                request=request,
                result=conflicting,
                reconciliation_case_id=case.id,
                occurred_at_ms=12_420,
                source_event_id="e13-terminal-late-conflict:943",
                reason_code=case.reason_code,
            )
            await _assert_physical_first_preserved(db, graph_index=946, same_target=True)
            await _assert_physical_first_preserved(db, graph_index=947, same_target=False)
            await db.commit()

        async with session_factory() as verify_db:
            members = await _members(verify_db, intent_id=prepared.intent_log.id)
            assert {(member.member_state, member.terminal_outcome) for member in members} == {("TERMINAL", "SUCCESS")}
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(RackBinMount)
                    .where(
                        RackBinMount.bin_code.in_(tuple(member.bin_code for member in members)),
                        RackBinMount.ended_at.is_(None),
                    )
                )
            ) == 3

    asyncio.run(with_database(scenario))
