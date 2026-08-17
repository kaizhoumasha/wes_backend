from __future__ import annotations

import pytest
from conftest import (
    EXECUTION_ID,
    TRACE_ID,
    FakeEpochReader,
    FakeExecutionReader,
    FakePositionReader,
    epoch_snapshot,
    execution_snapshot,
    position_snapshot,
)
from wes_plugin_sdk import (
    CompleteExecution,
    CreateWmsConfirmation,
    DevicePosition,
    ExecutionLifecycle,
    PauseForReconciliation,
    RackFace,
    TransportLeg,
    TransportRackPosition,
    Wait,
)

from rough_sorter.facts import (
    ReconciliationDecidedFact,
    ReconciliationDecision,
    ResumeDeviceAction,
    ResumeWmsAction,
    TransportOutcome,
    TransportOutcomePublishedFact,
)
from rough_sorter.handlers.reconciliation_decided import ReconciliationDecidedHandler
from rough_sorter.handlers.transport_outcome_published import TransportOutcomePublishedHandler


def _outlet() -> DevicePosition:
    return DevicePosition(
        location_id="pipeline-outlet",
        location_type="PIPELINE_OUTLET",
        material_trace_id=TRACE_ID,
    )


def _transport_fact(leg: TransportLeg, outcome: TransportOutcome, **overrides: object) -> TransportOutcomePublishedFact:
    values: dict[str, object] = {
        "fact_id": f"transport:{leg.value}",
        "evidence_id": f"transport-evidence:{leg.value}",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "transport_task_id": f"task-{leg.value}",
        "material_trace_id": TRACE_ID,
        "rack_replacement_id": "replacement-1",
        "leg": leg,
        "outcome": outcome,
        "rack_id": "rack-old" if leg is TransportLeg.OLD_OUT else "rack-new",
        "expected_target": TransportRackPosition("old-buffer" if leg is TransportLeg.OLD_OUT else "work-position"),
        "expected_face": RackFace.A if leg is TransportLeg.OLD_OUT else RackFace.B,
    }
    values.update(overrides)
    return TransportOutcomePublishedFact(**values)


def _transport_handler() -> TransportOutcomePublishedHandler:
    return TransportOutcomePublishedHandler(
        FakeExecutionReader(execution_snapshot()),
        FakePositionReader(
            (
                position_snapshot(
                    "pipeline-outlet",
                    "PIPELINE_OUTLET",
                    material_trace_id=TRACE_ID,
                    accepts_material=False,
                ),
            )
        ),
        FakeEpochReader(epoch_snapshot()),
    )


def test_new_rack_matching_success_retries_target_without_waiting_for_old_rack() -> None:
    fact = _transport_fact(
        TransportLeg.NEW_IN,
        TransportOutcome.SUCCEEDED,
        final_position=TransportRackPosition("work-position"),
        arrival_face=RackFace.B,
        actual_rack_id="rack-new",
        source_position=_outlet(),
        request_operation_id="stable-target-request-after-new-rack",
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
    )

    assert _transport_handler()(fact) == (
        CreateWmsConfirmation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            operation="inbound.material.target_decide@v1",
            operation_id="stable-target-request-after-new-rack",
            evidence_refs=(fact.evidence_id,),
            snapshot_refs=(
                f"execution:{EXECUTION_ID}",
                "transport:task-NEW_IN",
                "wms-admission:admission-1",
                "position:pipeline-outlet",
                "rack:rack-new",
            ),
        ),
    )


def test_new_rack_success_with_wrong_actual_rack_reconciles_without_requesting_target() -> None:
    fact = _transport_fact(
        TransportLeg.NEW_IN,
        TransportOutcome.SUCCEEDED,
        final_position=TransportRackPosition("work-position"),
        arrival_face=RackFace.B,
        actual_rack_id="rack-unplanned",
        source_position=_outlet(),
        request_operation_id="must-not-be-sent",
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
    )

    decisions = _transport_handler()(fact)

    assert decisions == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="NEW_RACK_ARRIVAL_MISMATCH",
            affected_resource_ids=("rack-new", "rack-unplanned"),
        ),
    )
    assert not any(isinstance(decision, CreateWmsConfirmation) for decision in decisions)


@pytest.mark.parametrize(
    ("final_position", "arrival_face"),
    [
        (TransportRackPosition("unexpected-position"), RackFace.B),
        (TransportRackPosition("work-position"), RackFace.A),
    ],
)
def test_new_rack_success_with_wrong_position_or_face_reconciles_without_requesting_target(
    final_position: TransportRackPosition,
    arrival_face: RackFace,
) -> None:
    fact = _transport_fact(
        TransportLeg.NEW_IN,
        TransportOutcome.SUCCEEDED,
        final_position=final_position,
        arrival_face=arrival_face,
        actual_rack_id="rack-new",
        source_position=_outlet(),
        request_operation_id="must-not-be-sent",
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
    )

    decisions = _transport_handler()(fact)

    assert decisions == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="NEW_RACK_ARRIVAL_MISMATCH",
            affected_resource_ids=("rack-new",),
        ),
    )
    assert not any(isinstance(decision, CreateWmsConfirmation) for decision in decisions)


@pytest.mark.parametrize("outcome", [TransportOutcome.FAILED, TransportOutcome.UNKNOWN])
def test_new_rack_failure_or_unknown_blocks_target_and_reconciles(outcome: TransportOutcome) -> None:
    fact = _transport_fact(TransportLeg.NEW_IN, outcome, reason_code=f"NEW_RACK_{outcome.value}")

    decisions = _transport_handler()(fact)

    assert decisions == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code=f"NEW_RACK_{outcome.value}",
            affected_resource_ids=("rack-new",),
        ),
    )
    assert not any(isinstance(decision, CreateWmsConfirmation) for decision in decisions)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("actual_rack_id", "rack-new"),
        ("source_position", _outlet()),
        ("request_operation_id", "must-not-leak"),
        ("pkg_id", "pkg-1"),
        ("inbound_admission_id", "admission-1"),
    ],
)
def test_non_success_transport_result_rejects_success_fields(field_name: str, field_value: object) -> None:
    with pytest.raises(ValueError, match=r"non-success.*another result branch"):
        _transport_fact(
            TransportLeg.NEW_IN,
            TransportOutcome.FAILED,
            reason_code="MOVE_FAILED",
            **{field_name: field_value},
        )


@pytest.mark.parametrize(
    "outcome",
    [TransportOutcome.SUCCEEDED, TransportOutcome.FAILED, TransportOutcome.UNKNOWN],
)
def test_old_rack_outcome_cannot_enter_material_decision_lane(outcome: TransportOutcome) -> None:
    values: dict[str, object] = {"reason_code": f"OLD_RACK_{outcome.value}"}
    if outcome is TransportOutcome.SUCCEEDED:
        values = {
            "final_position": TransportRackPosition("old-buffer"),
            "arrival_face": RackFace.A,
        }

    with pytest.raises(ValueError, match="NEW_IN"):
        _transport_fact(TransportLeg.OLD_OUT, outcome, **values)


def _reconciliation_handler() -> ReconciliationDecidedHandler:
    return ReconciliationDecidedHandler(
        FakeExecutionReader(execution_snapshot(lifecycle=ExecutionLifecycle.RECONCILING)),
        FakePositionReader(
            (
                position_snapshot(
                    "pipeline-outlet",
                    "PIPELINE_OUTLET",
                    material_trace_id=TRACE_ID,
                    accepts_material=False,
                ),
            )
        ),
        FakeEpochReader(epoch_snapshot()),
    )


def test_reconciliation_abort_closes_without_deleting_or_inventing_position() -> None:
    fact = ReconciliationDecidedFact(
        fact_id="reconciliation:abort",
        evidence_id="reconciliation-evidence",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        reconciliation_id="reconciliation-1",
        material_trace_id=TRACE_ID,
        decision=ReconciliationDecision.ABORT,
        reason_code="MATERIAL_CONFIRMED_MISSING",
        authoritative_position=None,
        resume_action=None,
    )

    assert _reconciliation_handler()(fact) == (
        CompleteExecution(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="RECONCILIATION_ABORT:MATERIAL_CONFIRMED_MISSING",
        ),
    )


def test_reconciliation_authoritative_non_rack_position_rejects_rack_identity() -> None:
    invalid_position = DevicePosition(
        location_id="pipeline-outlet",
        location_type="PIPELINE_OUTLET",
        material_trace_id=TRACE_ID,
        rack_id="rack-must-not-be-here",
    )

    with pytest.raises(ValueError, match="non-RACK_CELL"):
        ReconciliationDecidedFact(
            fact_id="reconciliation:invalid-position",
            evidence_id="reconciliation-invalid-position-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            reconciliation_id="reconciliation-invalid-position",
            material_trace_id=TRACE_ID,
            decision=ReconciliationDecision.ABORT,
            reason_code="MATERIAL_CONFIRMED_MISSING",
            authoritative_position=invalid_position,
            resume_action=None,
        )


def test_reconciliation_authoritative_position_rejects_incomplete_rack_cell_identity() -> None:
    incomplete_cell = DevicePosition(
        location_id="cell-1",
        location_type="RACK_CELL",
        material_trace_id=TRACE_ID,
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
    )

    with pytest.raises(ValueError, match="RACK_CELL requires complete rack/bin identity"):
        ReconciliationDecidedFact(
            fact_id="reconciliation:incomplete-cell",
            evidence_id="reconciliation-incomplete-cell-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            reconciliation_id="reconciliation-incomplete-cell",
            material_trace_id=TRACE_ID,
            decision=ReconciliationDecision.ABORT,
            reason_code="MATERIAL_CONFIRMED_MISSING",
            authoritative_position=incomplete_cell,
            resume_action=None,
        )


def test_resume_device_action_rejects_non_rack_position_with_bin_identity() -> None:
    invalid_source = DevicePosition(
        location_id="pipeline-inlet",
        location_type="PIPELINE_INLET",
        material_trace_id=TRACE_ID,
        bin_id="bin-must-not-be-here",
    )

    with pytest.raises(ValueError, match="non-RACK_CELL"):
        ResumeDeviceAction(
            device_role="TRANSFER_DEVICE",
            task_type="MOVE_FORWARD",
            source=invalid_source,
            target=_outlet(),
            device_ready=True,
        )


def test_resume_device_action_rejects_incomplete_rack_cell_identity() -> None:
    incomplete_cell = DevicePosition(
        location_id="cell-1",
        location_type="RACK_CELL",
        material_trace_id=TRACE_ID,
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
    )

    with pytest.raises(ValueError, match="RACK_CELL requires complete rack/bin identity"):
        ResumeDeviceAction(
            device_role="PLACEMENT_DEVICE",
            task_type="PICK_AND_PUT",
            source=_outlet(),
            target=incomplete_cell,
            device_ready=True,
        )


def test_reconciliation_continue_uses_typed_resume_action_and_is_deterministic() -> None:
    action = ResumeWmsAction(
        operation="inbound.material.target_decide@v1",
        operation_id="stable-reconciled-target-request",
        evidence_refs=("reconciliation-evidence",),
        snapshot_refs=(f"execution:{EXECUTION_ID}", "position:pipeline-outlet", "rack:rack-new"),
    )
    fact = ReconciliationDecidedFact(
        fact_id="reconciliation:continue",
        evidence_id="reconciliation-evidence",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        reconciliation_id="reconciliation-2",
        material_trace_id=TRACE_ID,
        decision=ReconciliationDecision.CONTINUE,
        reason_code="POSITION_CONFIRMED",
        authoritative_position=_outlet(),
        resume_action=action,
    )
    handler = _reconciliation_handler()

    first = handler(fact)
    second = handler(fact)

    assert (
        first
        == second
        == (
            CreateWmsConfirmation(
                material_execution_id=EXECUTION_ID,
                fact_id=fact.fact_id,
                operation=action.operation,
                operation_id=action.operation_id,
                evidence_refs=action.evidence_refs,
                snapshot_refs=action.snapshot_refs,
            ),
        )
    )


def test_reconciliation_continue_can_wait_for_the_next_topology_device() -> None:
    inlet = DevicePosition(
        location_id="pipeline-inlet",
        location_type="PIPELINE_INLET",
        material_trace_id=TRACE_ID,
    )
    outlet = _outlet()
    fact = ReconciliationDecidedFact(
        fact_id="reconciliation:resume-device",
        evidence_id="reconciliation-device-evidence",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        reconciliation_id="reconciliation-device",
        material_trace_id=TRACE_ID,
        decision=ReconciliationDecision.CONTINUE,
        reason_code="INLET_POSITION_CONFIRMED",
        authoritative_position=inlet,
        resume_action=ResumeDeviceAction(
            device_role="TRANSFER_DEVICE",
            task_type="MOVE_FORWARD",
            source=inlet,
            target=outlet,
            device_ready=False,
        ),
    )
    handler = ReconciliationDecidedHandler(
        FakeExecutionReader(execution_snapshot(lifecycle=ExecutionLifecycle.RECONCILING)),
        FakePositionReader(
            (
                position_snapshot(
                    inlet.location_id,
                    inlet.location_type,
                    material_trace_id=TRACE_ID,
                    accepts_material=False,
                ),
                position_snapshot(
                    outlet.location_id,
                    outlet.location_type,
                    material_trace_id=None,
                    accepts_material=True,
                ),
            )
        ),
        FakeEpochReader(epoch_snapshot()),
    )

    assert handler(fact) == (
        Wait(material_execution_id=EXECUTION_ID, fact_id=fact.fact_id, reason_code="TRANSFER_DEVICE_NOT_READY"),
    )


def test_reconciliation_continue_requires_authoritative_position() -> None:
    with pytest.raises(ValueError, match="authoritative_position"):
        ReconciliationDecidedFact(
            fact_id="reconciliation:invalid",
            evidence_id="reconciliation-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            reconciliation_id="reconciliation-3",
            material_trace_id=TRACE_ID,
            decision=ReconciliationDecision.CONTINUE,
            reason_code="POSITION_UNKNOWN",
            authoritative_position=None,
            resume_action=ResumeWmsAction(
                operation="inbound.material.target_decide@v1",
                operation_id="stable-target-request",
                evidence_refs=("reconciliation-evidence",),
                snapshot_refs=(f"execution:{EXECUTION_ID}",),
            ),
        )
