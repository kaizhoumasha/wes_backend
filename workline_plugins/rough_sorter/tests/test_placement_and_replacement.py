from __future__ import annotations

import pytest
from conftest import (
    EXECUTION_ID,
    TRACE_ID,
    runtime_snapshot,
)
from wes_plugin_sdk import (
    CompleteExecution,
    CreateTransportTask,
    CreateWmsConfirmation,
    DeferExecution,
    DevicePosition,
    PauseForReconciliation,
    TransportLeg,
    TransportRackPosition,
    TransportRcsTemplateId,
    TransportTaskType,
    TransportZonePosition,
)

from rough_sorter.facts import (
    CompletionKind,
    CompletionResult,
    DeviceOutcome,
    DevicePositionConfirmedFact,
    DeviceStep,
    PlacementCommandStatus,
    PlacementCompletedFact,
    PlacementConfirmationStatus,
    PlacementReleaseEvidence,
    PlacementResponseResult,
    RackMoveLegPlan,
    RackReleaseSnapshot,
    ReplacementPlanDecidedFact,
    ReplacementResult,
    rack_release_snapshot_ref,
)
from rough_sorter.handlers.device_position_confirmed import DevicePositionConfirmedHandler
from rough_sorter.handlers.placement_completed import PlacementCompletedHandler
from rough_sorter.handlers.replacement_plan_decided import ReplacementPlanDecidedHandler


def _position(location_id: str, location_type: str, **ids: str) -> DevicePosition:
    return DevicePosition(
        location_id=location_id,
        location_type=location_type,
        material_trace_id=TRACE_ID,
        **ids,
    )


def _readers(*_positions: tuple[str, str, str | None, bool]):
    return ()


def _placement_result(result: CompletionResult, **overrides: object) -> PlacementCompletedFact:
    values: dict[str, object] = {
        "fact_id": "evidence:6",
        "runtime_snapshot": runtime_snapshot(),
        "evidence_id": "6",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "operation_id": "019d0000-0000-7000-8000-000000000006",
        "material_trace_id": TRACE_ID,
        "kind": CompletionKind.PLACEMENT,
        "result": result,
        "affected_resource_ids": ("cell-1",),
    }
    values.update(overrides)
    return PlacementCompletedFact(**values)


def _replacement_fact(result: ReplacementResult, **overrides: object) -> ReplacementPlanDecidedFact:
    values: dict[str, object] = {
        "fact_id": "evidence:7",
        "runtime_snapshot": runtime_snapshot(),
        "evidence_id": "7",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "operation_id": "019d0000-0000-7000-8000-000000000007",
        "material_trace_id": TRACE_ID,
        "result": result,
        "current_rack_id": "rack-old",
    }
    values.update(overrides)
    return ReplacementPlanDecidedFact(**values)


def _release_snapshot(*, closed: bool) -> RackReleaseSnapshot:
    placement = PlacementReleaseEvidence(
        command_code="placement-command-1",
        command_status=PlacementCommandStatus.SUCCEEDED if closed else PlacementCommandStatus.ACKNOWLEDGED,
        command_result_evidence_id=51 if closed else None,
        confirmation_operation="inbound.material.placement_report@v1",
        confirmation_operation_id="placement-operation-1",
        confirmation_status=(PlacementConfirmationStatus.COMPLETED if closed else PlacementConfirmationStatus.PENDING),
        response_result=PlacementResponseResult.RECORDED if closed else None,
        response_evidence_id=61 if closed else None,
    )
    placements = (placement,)
    return RackReleaseSnapshot(
        current_rack_id="rack-old",
        placements=placements,
        snapshot_ref=rack_release_snapshot_ref("rack-old", placements),
    )


def _release_snapshot_without_confirmation(command_status: PlacementCommandStatus) -> RackReleaseSnapshot:
    placement = PlacementReleaseEvidence(
        command_code="placement-command-absent",
        command_status=command_status,
        command_result_evidence_id=None,
        confirmation_operation=None,
        confirmation_operation_id=None,
        confirmation_status=PlacementConfirmationStatus.ABSENT,
        response_result=None,
        response_evidence_id=None,
    )
    placements = (placement,)
    return RackReleaseSnapshot(
        current_rack_id="rack-old",
        placements=placements,
        snapshot_ref=rack_release_snapshot_ref("rack-old", placements),
    )


def test_successful_cell_position_creates_placement_report() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    cell = _position(
        "cell-1",
        "RACK_CELL",
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
        bin_cell_id="cell-1",
    )
    readers = _readers((cell.location_id, cell.location_type, TRACE_ID, False))
    fact = DevicePositionConfirmedFact(
        runtime_snapshot=runtime_snapshot(),
        fact_id="evidence:5",
        evidence_id="5",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        command_code="command-placement-1",
        device_code="placement-device-1",
        material_trace_id=TRACE_ID,
        step=DeviceStep.PLACEMENT_TO_CELL,
        device_role="PLACEMENT_DEVICE",
        outcome=DeviceOutcome.SUCCESS,
        source_position=outlet,
        target_position=cell,
        actual_position=cell,
        request_operation_id="019d0000-0000-7000-8000-000000000005",
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
        target_assignment_id="assignment-1",
        placement_sequence=1,
        placed_at_ms=1_787_000_000_000,
    )

    assert DevicePositionConfirmedHandler(*readers)(fact) == (
        CreateWmsConfirmation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            operation="inbound.material.placement_report@v1",
            operation_id=fact.request_operation_id,
            evidence_refs=(fact.evidence_id,),
            snapshot_refs=(
                f"execution:{EXECUTION_ID}",
                "command:command-placement-1",
                "target-assignment:assignment-1",
            ),
        ),
    )


def test_successful_ng_position_creates_ng_report() -> None:
    measurement = _position("measurement", "MEASUREMENT_POSITION")
    ng = _position("ng-1", "NG_POSITION")
    readers = _readers((ng.location_id, ng.location_type, TRACE_ID, False))
    fact = DevicePositionConfirmedFact(
        runtime_snapshot=runtime_snapshot(),
        fact_id="evidence:ng-command",
        evidence_id="ng-command",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        command_code="command-ng-1",
        device_code="measurement-device-1",
        material_trace_id=TRACE_ID,
        step=DeviceStep.MEASUREMENT_TO_NG,
        device_role="MEASUREMENT_DEVICE",
        outcome=DeviceOutcome.SUCCESS,
        source_position=measurement,
        target_position=ng,
        actual_position=ng,
        request_operation_id="019d0000-0000-7000-8000-000000000008",
        ng_evidence_id="ng-evidence-1",
        reason_code="GRN_REJECTED",
    )

    assert DevicePositionConfirmedHandler(*readers)(fact) == (
        CreateWmsConfirmation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            operation="inbound.material.ng_placement_report@v1",
            operation_id=fact.request_operation_id,
            evidence_refs=(fact.evidence_id, "ng-evidence-1"),
            snapshot_refs=(f"execution:{EXECUTION_ID}", "command:command-ng-1"),
        ),
    )


def test_recorded_or_duplicate_placement_is_the_only_automatic_close() -> None:
    handler = PlacementCompletedHandler()

    for result in (CompletionResult.RECORDED, CompletionResult.DUPLICATE):
        fact = _placement_result(result)
        assert handler(fact) == (
            CompleteExecution(
                material_execution_id=EXECUTION_ID,
                fact_id=fact.fact_id,
                reason_code=f"PLACEMENT_{result.value}",
            ),
        )


def test_placement_conflict_pauses_instead_of_closing() -> None:
    fact = _placement_result(CompletionResult.RECONCILING, reason_code="WMS_PLACEMENT_CONFLICT")

    assert PlacementCompletedHandler()(fact) == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="WMS_PLACEMENT_CONFLICT",
            affected_resource_ids=("cell-1",),
        ),
    )


def test_open_release_gate_creates_no_rack_move_and_does_not_claim_recovery() -> None:
    fact = _replacement_fact(
        ReplacementResult.READY,
        release_snapshot=_release_snapshot(closed=False),
        rack_replacement_id="replacement-1",
        old_loaded_rack=RackMoveLegPlan(
            rack_id="rack-old",
            source=TransportRackPosition("work-position"),
            target=TransportRackPosition("old-buffer"),
            target_face="90",
        ),
        new_empty_rack=RackMoveLegPlan(
            rack_id="rack-new",
            source=TransportRackPosition("new-buffer"),
            target=TransportRackPosition("work-position"),
            target_face="270",
        ),
    )
    handler = ReplacementPlanDecidedHandler()

    decisions = handler(fact)

    assert decisions == (
        DeferExecution(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="RACK_RELEASE_GATE_NOT_CLOSED",
        ),
    )
    assert not any(isinstance(decision, CreateTransportTask) for decision in decisions)


def test_active_placement_without_confirmation_defers_rack_release() -> None:
    fact = _replacement_fact(
        ReplacementResult.READY,
        release_snapshot=_release_snapshot_without_confirmation(PlacementCommandStatus.ACKNOWLEDGED),
        rack_replacement_id="replacement-1",
        old_loaded_rack=RackMoveLegPlan(
            rack_id="rack-old",
            source=TransportRackPosition("work-position"),
            target=TransportRackPosition("old-buffer"),
            target_face="90",
        ),
        new_empty_rack=RackMoveLegPlan(
            rack_id="rack-new",
            source=TransportRackPosition("new-buffer"),
            target=TransportRackPosition("work-position"),
            target_face="270",
        ),
    )

    assert ReplacementPlanDecidedHandler()(fact) == (
        DeferExecution(EXECUTION_ID, fact.fact_id, "RACK_RELEASE_GATE_NOT_CLOSED"),
    )


@pytest.mark.parametrize(
    "command_status",
    [
        PlacementCommandStatus.FAILED,
        PlacementCommandStatus.TIMED_OUT,
        PlacementCommandStatus.RECONCILING,
    ],
)
def test_conflicting_placement_without_confirmation_pauses_rack_release(
    command_status: PlacementCommandStatus,
) -> None:
    fact = _replacement_fact(
        ReplacementResult.READY,
        release_snapshot=_release_snapshot_without_confirmation(command_status),
        rack_replacement_id="replacement-1",
        old_loaded_rack=RackMoveLegPlan(
            rack_id="rack-old",
            source=TransportRackPosition("work-position"),
            target=TransportRackPosition("old-buffer"),
            target_face="90",
        ),
        new_empty_rack=RackMoveLegPlan(
            rack_id="rack-new",
            source=TransportRackPosition("new-buffer"),
            target=TransportRackPosition("work-position"),
            target_face="270",
        ),
    )

    assert ReplacementPlanDecidedHandler()(fact) == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="RACK_RELEASE_GATE_CONFLICT",
            affected_resource_ids=("rack-old",),
        ),
    )


def test_closed_release_gate_creates_two_independent_stable_rack_moves() -> None:
    old_plan = RackMoveLegPlan(
        rack_id="rack-old",
        source=TransportRackPosition("work-position"),
        target=TransportRackPosition("old-buffer"),
        target_face="90",
    )
    new_plan = RackMoveLegPlan(
        rack_id="rack-new",
        source=TransportRackPosition("new-buffer"),
        target=TransportRackPosition("work-position"),
        target_face="270",
    )
    fact = _replacement_fact(
        ReplacementResult.READY,
        release_snapshot=_release_snapshot(closed=True),
        rack_replacement_id="replacement-1",
        old_loaded_rack=old_plan,
        new_empty_rack=new_plan,
    )
    handler = ReplacementPlanDecidedHandler()

    first = handler(fact)
    second = handler(fact)

    assert first == second
    assert first == (
        CreateTransportTask(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            task_type=TransportTaskType.RACK_MOVE,
            rack_replacement_id="replacement-1",
            leg=TransportLeg.OLD_OUT,
            current_rack_id="rack-old",
            rack_id="rack-old",
            source=old_plan.source,
            target=old_plan.target,
            target_face="90",
            rcs_template_id=TransportRcsTemplateId.CTU03,
        ),
        CreateTransportTask(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            task_type=TransportTaskType.RACK_MOVE,
            rack_replacement_id="replacement-1",
            leg=TransportLeg.NEW_IN,
            current_rack_id="rack-old",
            rack_id="rack-new",
            source=new_plan.source,
            target=new_plan.target,
            target_face="270",
            rcs_template_id=TransportRcsTemplateId.CTU01,
        ),
    )
    assert {decision.business_identity for decision in first} == {
        ("replacement-1", TransportLeg.OLD_OUT),
        ("replacement-1", TransportLeg.NEW_IN),
    }


def test_replacement_legs_preserve_broad_positions_and_explicit_templates() -> None:
    old_plan = RackMoveLegPlan(
        rack_id="rack-old",
        source=TransportRackPosition("work-position"),
        target=TransportZonePosition("storage-zone"),
        target_face="FACE@01",
    )
    new_plan = RackMoveLegPlan(
        rack_id="rack-new",
        source=TransportZonePosition("storage-zone"),
        target=TransportRackPosition("work-position"),
        target_face="面-1",
    )
    fact = _replacement_fact(
        ReplacementResult.READY,
        release_snapshot=_release_snapshot(closed=True),
        rack_replacement_id="replacement-1",
        old_loaded_rack=old_plan,
        new_empty_rack=new_plan,
    )

    old_out, new_in = ReplacementPlanDecidedHandler()(fact)

    assert old_out.target is old_plan.target
    assert old_out.target_face == "FACE@01"
    assert old_out.rcs_template_id is TransportRcsTemplateId.CTU03
    assert new_in.source is new_plan.source
    assert new_in.target_face == "面-1"
    assert new_in.rcs_template_id is TransportRcsTemplateId.CTU01
