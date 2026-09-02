from __future__ import annotations

import pytest
from conftest import (
    EXECUTION_ID,
    TRACE_ID,
    runtime_snapshot,
)
from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateWmsConfirmation,
    DeferExecution,
    DevicePosition,
    PauseForReconciliation,
    Wait,
)

from rough_sorter.facts import (
    DeviceOutcome,
    DevicePositionConfirmedFact,
    DeviceStep,
    TargetDecidedFact,
    TargetResult,
)
from rough_sorter.handlers.device_position_confirmed import DevicePositionConfirmedHandler
from rough_sorter.handlers.target_decided import TargetDecidedHandler


def _position(location_id: str, location_type: str, **ids: str) -> DevicePosition:
    return DevicePosition(
        location_id=location_id,
        location_type=location_type,
        material_trace_id=TRACE_ID,
        **ids,
    )


def _readers(*_positions: tuple[str, str, str | None, bool]):
    return ()


def _device_fact(step: DeviceStep, **overrides: object) -> DevicePositionConfirmedFact:
    source = _position("measurement", "MEASUREMENT_POSITION")
    target = _position("pipeline-inlet", "PIPELINE_INLET")
    values: dict[str, object] = {
        "fact_id": "evidence:3",
        "runtime_snapshot": runtime_snapshot(),
        "evidence_id": "3",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "command_code": "command-1",
        "device_code": "measurement-device-1",
        "material_trace_id": TRACE_ID,
        "step": step,
        "device_role": "MEASUREMENT_DEVICE",
        "outcome": DeviceOutcome.SUCCESS,
        "source_position": source,
        "target_position": target,
        "actual_position": target,
    }
    values.update(overrides)
    return DevicePositionConfirmedFact(**values)


def _target_fact(result: TargetResult, **overrides: object) -> TargetDecidedFact:
    values: dict[str, object] = {
        "fact_id": "evidence:4",
        "runtime_snapshot": runtime_snapshot(),
        "evidence_id": "4",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "operation_id": "019d0000-0000-7000-8000-000000000002",
        "material_trace_id": TRACE_ID,
        "result": result,
        "source_position": _position("pipeline-outlet", "PIPELINE_OUTLET"),
        "current_rack_id": "rack-current",
        "current_rack_fenced": False,
        "device_ready": True,
    }
    values.update(overrides)
    return TargetDecidedFact(**values)


def test_inlet_success_creates_transfer_command_only_after_confirmed_position() -> None:
    inlet = _position("pipeline-inlet", "PIPELINE_INLET")
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    readers = _readers(
        (inlet.location_id, inlet.location_type, TRACE_ID, False),
        (outlet.location_id, outlet.location_type, None, True),
    )
    fact = _device_fact(
        DeviceStep.MEASUREMENT_TO_INLET,
        target_position=inlet,
        actual_position=inlet,
        next_position=outlet,
        next_device_ready=True,
    )

    assert DevicePositionConfirmedHandler(*readers)(fact) == (
        CreateDeviceCommand(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            device_role="TRANSFER_DEVICE",
            task_type="MOVE_FORWARD",
            material_trace_id=TRACE_ID,
            source=inlet,
            target=outlet,
        ),
    )


def test_outlet_success_requests_target_cell_and_does_not_place_early() -> None:
    inlet = _position("pipeline-inlet", "PIPELINE_INLET")
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    readers = _readers((outlet.location_id, outlet.location_type, TRACE_ID, False))
    fact = _device_fact(
        DeviceStep.TRANSFER_TO_OUTLET,
        device_role="TRANSFER_DEVICE",
        source_position=inlet,
        target_position=outlet,
        actual_position=outlet,
        request_operation_id="019d0000-0000-7000-8000-000000000003",
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
        current_rack_id="rack-current",
    )

    decision = DevicePositionConfirmedHandler(*readers)(fact)[0]

    assert isinstance(decision, CreateWmsConfirmation)
    assert decision.operation == "inbound.material.target_decide@v1"
    assert decision.operation_id == fact.request_operation_id
    assert decision.request_data == {
        "material_execution_id": EXECUTION_ID,
        "material_trace_id": TRACE_ID,
        "pkg_id": "pkg-1",
        "inbound_admission_id": "admission-1",
        "source_position": {"type": "HANDOFF_POSITION", "location_code": "pipeline-outlet"},
        "current_rack_id": "rack-current",
    }


@pytest.mark.parametrize("step", [DeviceStep.MEASUREMENT_TO_NG, DeviceStep.PLACEMENT_TO_NG])
def test_ng_report_references_callback_evidence_once(step: DeviceStep) -> None:
    ng = _position("ng-1", "NG_POSITION")
    fact = _device_fact(
        step,
        device_role="MEASUREMENT_DEVICE" if step is DeviceStep.MEASUREMENT_TO_NG else "PLACEMENT_DEVICE",
        source_position=(
            _position("measurement", "MEASUREMENT_POSITION")
            if step is DeviceStep.MEASUREMENT_TO_NG
            else _position("pipeline-outlet", "PIPELINE_OUTLET")
        ),
        target_position=ng,
        actual_position=ng,
        request_operation_id="019d0000-0000-7000-8000-000000000009",
        ng_evidence_id="3",
        reason_code="QUALITY_NG",
    )

    decision = DevicePositionConfirmedHandler()(fact)[0]

    assert isinstance(decision, CreateWmsConfirmation)
    assert decision.request_data["ng_evidence_id"] == "3"


@pytest.mark.parametrize(
    ("outcome", "reason_code"),
    [
        (DeviceOutcome.FAILED, "ACTION_FAILED"),
        (DeviceOutcome.UNKNOWN, "DEVICE_DELIVERY_UNKNOWN"),
        (DeviceOutcome.IDENTITY_CONFLICT, "MATERIAL_IDENTITY_CONFLICT"),
    ],
)
def test_failed_or_unknown_device_result_never_replays_equivalent_action(
    outcome: DeviceOutcome,
    reason_code: str,
) -> None:
    source = _position("pipeline-inlet", "PIPELINE_INLET")
    target = _position("pipeline-outlet", "PIPELINE_OUTLET")
    readers = _readers((source.location_id, source.location_type, TRACE_ID, False))
    fact = _device_fact(
        DeviceStep.TRANSFER_TO_OUTLET,
        device_role="TRANSFER_DEVICE",
        outcome=outcome,
        source_position=source,
        target_position=target,
        actual_position=None,
        reason_code=reason_code,
    )

    decisions = DevicePositionConfirmedHandler(*readers)(fact)

    assert decisions == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code=reason_code,
            affected_resource_ids=(fact.device_code, source.location_id, target.location_id),
        ),
    )
    assert not any(isinstance(decision, CreateDeviceCommand) for decision in decisions)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("request_operation_id", "must-not-leak"),
        ("next_position", _position("pipeline-outlet", "PIPELINE_OUTLET")),
        ("target_assignment_id", "assignment-1"),
        ("ng_evidence_id", "ng-evidence-1"),
    ],
)
def test_non_success_device_result_rejects_success_branch_fields(field_name: str, field_value: object) -> None:
    source = _position("pipeline-inlet", "PIPELINE_INLET")
    target = _position("pipeline-outlet", "PIPELINE_OUTLET")

    with pytest.raises(ValueError, match=r"non-success.*another result branch"):
        _device_fact(
            DeviceStep.TRANSFER_TO_OUTLET,
            device_role="TRANSFER_DEVICE",
            outcome=DeviceOutcome.FAILED,
            source_position=source,
            target_position=target,
            actual_position=None,
            reason_code="ACTION_FAILED",
            **{field_name: field_value},
        )


@pytest.mark.parametrize(
    ("step", "overrides", "foreign_field"),
    [
        (
            DeviceStep.MEASUREMENT_TO_INLET,
            {
                "next_position": _position("pipeline-outlet", "PIPELINE_OUTLET"),
                "next_device_ready": True,
                "request_operation_id": "foreign-operation",
            },
            "request_operation_id",
        ),
        (
            DeviceStep.TRANSFER_TO_OUTLET,
            {
                "device_role": "TRANSFER_DEVICE",
                "source_position": _position("pipeline-inlet", "PIPELINE_INLET"),
                "target_position": _position("pipeline-outlet", "PIPELINE_OUTLET"),
                "actual_position": _position("pipeline-outlet", "PIPELINE_OUTLET"),
                "request_operation_id": "target-operation",
                "pkg_id": "pkg-1",
                "inbound_admission_id": "admission-1",
                "current_rack_id": "rack-current",
                "ng_evidence_id": "foreign-ng-evidence",
            },
            "ng_evidence_id",
        ),
        (
            DeviceStep.PLACEMENT_TO_CELL,
            {
                "device_role": "PLACEMENT_DEVICE",
                "source_position": _position("pipeline-outlet", "PIPELINE_OUTLET"),
                "target_position": _position(
                    "cell-1",
                    "RACK_CELL",
                    rack_id="rack-current",
                    rack_slot_code="slot-1",
                    bin_id="bin-1",
                    bin_cell_id="cell-1",
                ),
                "actual_position": _position(
                    "cell-1",
                    "RACK_CELL",
                    rack_id="rack-current",
                    rack_slot_code="slot-1",
                    bin_id="bin-1",
                    bin_cell_id="cell-1",
                ),
                "request_operation_id": "placement-operation",
                "pkg_id": "pkg-1",
                "inbound_admission_id": "admission-1",
                "target_assignment_id": "assignment-1",
                "placement_sequence": 1,
                "placed_at_ms": 1,
                "current_rack_id": "foreign-rack",
            },
            "current_rack_id",
        ),
        (
            DeviceStep.PLACEMENT_TO_NG,
            {
                "device_role": "PLACEMENT_DEVICE",
                "source_position": _position("pipeline-outlet", "PIPELINE_OUTLET"),
                "target_position": _position("ng-1", "NG_POSITION"),
                "actual_position": _position("ng-1", "NG_POSITION"),
                "request_operation_id": "ng-operation",
                "ng_evidence_id": "ng-evidence-1",
                "reason_code": "TARGET_REJECTED",
                "pkg_id": "foreign-pkg",
            },
            "pkg_id",
        ),
    ],
)
def test_success_device_step_rejects_fields_from_another_step(
    step: DeviceStep,
    overrides: dict[str, object],
    foreign_field: str,
) -> None:
    with pytest.raises(ValueError, match=foreign_field):
        _device_fact(step, **overrides)


def test_transfer_waits_when_device_is_not_ready() -> None:
    inlet = _position("pipeline-inlet", "PIPELINE_INLET")
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    readers = _readers((inlet.location_id, inlet.location_type, TRACE_ID, False))
    fact = _device_fact(
        DeviceStep.MEASUREMENT_TO_INLET,
        target_position=inlet,
        actual_position=inlet,
        next_position=outlet,
        next_device_ready=False,
    )

    assert DevicePositionConfirmedHandler(*readers)(fact) == (
        DeferExecution(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="TRANSFER_DEVICE_NOT_READY",
        ),
    )


def test_assigned_target_defers_when_placement_device_is_not_ready() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    cell = _position(
        "cell-1",
        "RACK_CELL",
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
        bin_cell_id="cell-1",
    )
    fact = _target_fact(
        TargetResult.ASSIGNED,
        source_position=outlet,
        target_position=cell,
        target_assignment_id="assignment-1",
        placement_sequence=1,
        expected_height_mm="2.0",
        device_ready=False,
    )

    assert TargetDecidedHandler()(fact) == (
        DeferExecution(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="PLACEMENT_DEVICE_NOT_READY",
        ),
    )


def test_assigned_target_creates_placement_pick_and_put() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    cell = _position(
        "cell-1",
        "RACK_CELL",
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
        bin_cell_id="cell-1",
    )
    readers = _readers(
        (outlet.location_id, outlet.location_type, TRACE_ID, False),
        (cell.location_id, cell.location_type, None, True),
    )
    fact = _target_fact(
        TargetResult.ASSIGNED,
        source_position=outlet,
        target_position=cell,
        target_assignment_id="assignment-1",
        placement_sequence=1,
        expected_height_mm="2.0",
    )

    assert TargetDecidedHandler(*readers)(fact) == (
        CreateDeviceCommand(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            device_role="PLACEMENT_DEVICE",
            task_type="PICK_AND_PUT",
            material_trace_id=TRACE_ID,
            source=outlet,
            target=cell,
        ),
    )


def test_assigned_target_for_fenced_current_rack_reconciles_without_device_command() -> None:
    fact = _target_fact(
        TargetResult.ASSIGNED,
        current_rack_fenced=True,
        target_position=_position(
            "cell-1",
            "RACK_CELL",
            rack_id="rack-current",
            rack_slot_code="slot-1",
            bin_id="bin-1",
            bin_cell_id="cell-1",
        ),
        target_assignment_id="assignment-1",
        placement_sequence=1,
        expected_height_mm="2.0",
    )

    assert TargetDecidedHandler()(fact) == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="CURRENT_RACK_ALREADY_REPLACED",
            affected_resource_ids=("rack-current",),
        ),
    )


def test_assigned_target_for_different_actual_rack_reconciles_without_device_command() -> None:
    fact = _target_fact(
        TargetResult.ASSIGNED,
        target_position=_position(
            "cell-2",
            "RACK_CELL",
            rack_id="rack-actual",
            rack_slot_code="slot-2",
            bin_id="bin-2",
            bin_cell_id="cell-2",
        ),
        target_assignment_id="assignment-2",
        placement_sequence=2,
        expected_height_mm="2.0",
    )

    assert TargetDecidedHandler()(fact) == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="TARGET_RACK_MISMATCH",
            affected_resource_ids=("rack-current", "rack-actual"),
        ),
    )


def test_placement_device_result_rejects_incomplete_rack_cell_identity() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    incomplete_cell = _position(
        "cell-1",
        "RACK_CELL",
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
    )

    with pytest.raises(ValueError, match="RACK_CELL requires complete rack/bin identity"):
        _device_fact(
            DeviceStep.PLACEMENT_TO_CELL,
            device_role="PLACEMENT_DEVICE",
            source_position=outlet,
            target_position=incomplete_cell,
            actual_position=incomplete_cell,
            request_operation_id="placement-operation",
            pkg_id="pkg-1",
            inbound_admission_id="admission-1",
            target_assignment_id="assignment-1",
            placement_sequence=1,
            placed_at_ms=1,
        )


def test_no_available_cell_requests_stable_replacement_plan_without_device_command() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    readers = _readers((outlet.location_id, outlet.location_type, TRACE_ID, False))
    fact = _target_fact(
        TargetResult.NO_AVAILABLE_CELL,
        reason_code="RACK_FULL",
        request_operation_id="019d0000-0000-7000-8000-000000000004",
    )

    decisions = TargetDecidedHandler(*readers)(fact)

    assert decisions[0].operation == "inbound.source_rack.replacement_plan_decide@v1"
    assert decisions[0].operation_id == fact.request_operation_id
    assert decisions[0].request_data == {
        "material_execution_id": EXECUTION_ID,
        "material_trace_id": TRACE_ID,
        "current_rack_id": "rack-current",
    }
    assert not any(isinstance(decision, CreateDeviceCommand) for decision in decisions)


def test_target_wait_keeps_material_at_outlet() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    readers = _readers((outlet.location_id, outlet.location_type, TRACE_ID, False))
    fact = _target_fact(TargetResult.WAIT, reason_code="WMS_RETRY_LATER")

    assert TargetDecidedHandler(*readers)(fact) == (
        Wait(material_execution_id=EXECUTION_ID, fact_id=fact.fact_id, reason_code="WMS_RETRY_LATER"),
    )


@pytest.mark.parametrize(
    ("result", "required_fields", "foreign_fields"),
    [
        (TargetResult.WAIT, {"reason_code": "WAIT"}, {"target_assignment_id": "assignment-1"}),
        (
            TargetResult.RECONCILING,
            {"reason_code": "RECONCILING"},
            {"request_operation_id": "foreign-operation"},
        ),
        (
            TargetResult.REJECT,
            {"reason_code": "REJECTED", "target_position": _position("ng-1", "NG_POSITION")},
            {"placement_sequence": 1},
        ),
        (
            TargetResult.NO_AVAILABLE_CELL,
            {"reason_code": "RACK_FULL", "request_operation_id": "replacement-operation"},
            {"target_position": _position("ng-1", "NG_POSITION")},
        ),
    ],
)
def test_non_assigned_target_result_rejects_fields_from_another_branch(
    result: TargetResult,
    required_fields: dict[str, object],
    foreign_fields: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="another result branch"):
        _target_fact(result, **required_fields, **foreign_fields)


def test_non_rack_cell_position_rejects_rack_and_bin_identity() -> None:
    invalid_source = _position("pipeline-outlet", "PIPELINE_OUTLET", rack_id="rack-current")

    with pytest.raises(ValueError, match="non-RACK_CELL"):
        _target_fact(TargetResult.WAIT, source_position=invalid_source, reason_code="WAIT")


def test_target_reject_uses_placement_device_from_outlet_to_wms_ng() -> None:
    outlet = _position("pipeline-outlet", "PIPELINE_OUTLET")
    ng = _position("ng-1", "NG_POSITION")
    readers = _readers(
        (outlet.location_id, outlet.location_type, TRACE_ID, False),
        (ng.location_id, ng.location_type, None, True),
    )
    fact = _target_fact(TargetResult.REJECT, reason_code="TARGET_REJECTED", target_position=ng)

    assert TargetDecidedHandler(*readers)(fact) == (
        CreateDeviceCommand(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            device_role="PLACEMENT_DEVICE",
            task_type="PICK_AND_PUT",
            material_trace_id=TRACE_ID,
            source=outlet,
            target=ng,
        ),
    )
