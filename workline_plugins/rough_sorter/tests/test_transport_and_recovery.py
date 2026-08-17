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
    RecoveryDecidedFact,
    RecoveryDecision,
    RecoveryDeviceContinuation,
    RecoveryWmsContinuation,
    TransportOutcome,
    TransportOutcomePublishedFact,
)
from rough_sorter.handlers.recovery_decided import RecoveryDecidedHandler
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


def _recovery_handler() -> RecoveryDecidedHandler:
    return RecoveryDecidedHandler(
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


def test_recovery_abort_closes_without_deleting_or_inventing_position() -> None:
    fact = RecoveryDecidedFact(
        fact_id="recovery:abort",
        evidence_id="recovery-evidence",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        recovery_id="recovery-1",
        material_trace_id=TRACE_ID,
        decision=RecoveryDecision.ABORT,
        reason_code="MATERIAL_CONFIRMED_MISSING",
        authoritative_position=None,
        reconciling_evidence_id="causal-unknown-evidence",
        continuation=None,
    )

    assert _recovery_handler()(fact) == (
        CompleteExecution(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="RECOVERY_ABORT:MATERIAL_CONFIRMED_MISSING",
        ),
    )


def test_recovery_authoritative_non_rack_position_rejects_rack_identity() -> None:
    invalid_position = DevicePosition(
        location_id="pipeline-outlet",
        location_type="PIPELINE_OUTLET",
        material_trace_id=TRACE_ID,
        rack_id="rack-must-not-be-here",
    )

    with pytest.raises(ValueError, match="non-RACK_CELL"):
        RecoveryDecidedFact(
            fact_id="recovery:invalid-position",
            evidence_id="recovery-invalid-position-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            recovery_id="recovery-invalid-position",
            material_trace_id=TRACE_ID,
            decision=RecoveryDecision.ABORT,
            reason_code="MATERIAL_CONFIRMED_MISSING",
            authoritative_position=invalid_position,
            reconciling_evidence_id="causal-unknown-evidence",
            continuation=None,
        )


def test_recovery_authoritative_position_rejects_incomplete_rack_cell_identity() -> None:
    incomplete_cell = DevicePosition(
        location_id="cell-1",
        location_type="RACK_CELL",
        material_trace_id=TRACE_ID,
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
    )

    with pytest.raises(ValueError, match="RACK_CELL requires complete rack/bin identity"):
        RecoveryDecidedFact(
            fact_id="recovery:incomplete-cell",
            evidence_id="recovery-incomplete-cell-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            recovery_id="recovery-incomplete-cell",
            material_trace_id=TRACE_ID,
            decision=RecoveryDecision.ABORT,
            reason_code="MATERIAL_CONFIRMED_MISSING",
            authoritative_position=incomplete_cell,
            reconciling_evidence_id="causal-unknown-evidence",
            continuation=None,
        )


def test_recovery_device_continuation_rejects_non_rack_position_with_bin_identity() -> None:
    invalid_source = DevicePosition(
        location_id="pipeline-inlet",
        location_type="PIPELINE_INLET",
        material_trace_id=TRACE_ID,
        bin_id="bin-must-not-be-here",
    )

    with pytest.raises(ValueError, match="non-RACK_CELL"):
        RecoveryDeviceContinuation(
            device_role="TRANSFER_DEVICE",
            task_type="MOVE_FORWARD",
            source=invalid_source,
            target=_outlet(),
            device_ready=True,
        )


def test_recovery_device_continuation_rejects_incomplete_rack_cell_identity() -> None:
    incomplete_cell = DevicePosition(
        location_id="cell-1",
        location_type="RACK_CELL",
        material_trace_id=TRACE_ID,
        rack_id="rack-current",
        rack_slot_code="slot-1",
        bin_id="bin-1",
    )

    with pytest.raises(ValueError, match="RACK_CELL requires complete rack/bin identity"):
        RecoveryDeviceContinuation(
            device_role="PLACEMENT_DEVICE",
            task_type="PICK_AND_PUT",
            source=_outlet(),
            target=incomplete_cell,
            device_ready=True,
        )


def test_recovery_continue_uses_typed_continuation_and_is_deterministic() -> None:
    continuation = RecoveryWmsContinuation(
        operation="inbound.material.target_decide@v1",
        operation_id="stable-reconciled-target-request",
        evidence_refs=("recovery-evidence",),
        snapshot_refs=(f"execution:{EXECUTION_ID}", "position:pipeline-outlet", "rack:rack-new"),
    )
    fact = RecoveryDecidedFact(
        fact_id="recovery:continue",
        evidence_id="recovery-evidence",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        recovery_id="recovery-2",
        material_trace_id=TRACE_ID,
        decision=RecoveryDecision.CONTINUE,
        reason_code="POSITION_CONFIRMED",
        authoritative_position=_outlet(),
        reconciling_evidence_id="causal-unknown-evidence",
        continuation=continuation,
    )
    handler = _recovery_handler()

    first = handler(fact)
    second = handler(fact)

    assert (
        first
        == second
        == (
            CreateWmsConfirmation(
                material_execution_id=EXECUTION_ID,
                fact_id=fact.fact_id,
                operation=continuation.operation,
                operation_id=continuation.operation_id,
                evidence_refs=continuation.evidence_refs,
                snapshot_refs=continuation.snapshot_refs,
            ),
        )
    )


def test_recovery_continue_can_wait_for_the_next_topology_device() -> None:
    inlet = DevicePosition(
        location_id="pipeline-inlet",
        location_type="PIPELINE_INLET",
        material_trace_id=TRACE_ID,
    )
    outlet = _outlet()
    fact = RecoveryDecidedFact(
        fact_id="recovery:continue-device",
        evidence_id="recovery-device-evidence",
        fact_version="1.0",
        material_execution_id=EXECUTION_ID,
        recovery_id="recovery-device",
        material_trace_id=TRACE_ID,
        decision=RecoveryDecision.CONTINUE,
        reason_code="INLET_POSITION_CONFIRMED",
        authoritative_position=inlet,
        reconciling_evidence_id="causal-unknown-evidence",
        continuation=RecoveryDeviceContinuation(
            device_role="TRANSFER_DEVICE",
            task_type="MOVE_FORWARD",
            source=inlet,
            target=outlet,
            device_ready=False,
        ),
    )
    handler = RecoveryDecidedHandler(
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


def test_recovery_continue_requires_authoritative_position() -> None:
    with pytest.raises(ValueError, match="authoritative_position"):
        RecoveryDecidedFact(
            fact_id="recovery:invalid",
            evidence_id="recovery-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            recovery_id="recovery-3",
            material_trace_id=TRACE_ID,
            decision=RecoveryDecision.CONTINUE,
            reason_code="POSITION_UNKNOWN",
            authoritative_position=None,
            reconciling_evidence_id="causal-unknown-evidence",
            continuation=RecoveryWmsContinuation(
                operation="inbound.material.target_decide@v1",
                operation_id="stable-target-request",
                evidence_refs=("recovery-evidence",),
                snapshot_refs=(f"execution:{EXECUTION_ID}",),
            ),
        )


def test_recovery_continue_requires_typed_continuation() -> None:
    with pytest.raises(TypeError, match="typed continuation"):
        RecoveryDecidedFact(
            fact_id="recovery:missing-continuation",
            evidence_id="recovery-evidence",
            fact_version="1.0",
            material_execution_id=EXECUTION_ID,
            recovery_id="recovery-4",
            decision=RecoveryDecision.CONTINUE,
            authoritative_position=_outlet(),
            reason_code="POSITION_CONFIRMED",
            material_trace_id=TRACE_ID,
            reconciling_evidence_id="causal-unknown-evidence",
            continuation=None,
        )


def test_recovery_abort_forbids_continuation_and_self_causal_identity() -> None:
    continuation = RecoveryWmsContinuation(
        operation="inbound.material.target_decide@v1",
        operation_id="new-target-operation",
        evidence_refs=("recovery-evidence",),
        snapshot_refs=(f"execution:{EXECUTION_ID}",),
    )
    values = {
        "fact_id": "recovery:abort-invalid",
        "evidence_id": "recovery-evidence",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "recovery_id": "recovery-5",
        "decision": RecoveryDecision.ABORT,
        "authoritative_position": None,
        "reason_code": "MATERIAL_CONFIRMED_MISSING",
        "material_trace_id": TRACE_ID,
    }

    with pytest.raises(ValueError, match="ABORT must not include continuation"):
        RecoveryDecidedFact(**values, reconciling_evidence_id="causal-unknown-evidence", continuation=continuation)
    with pytest.raises(ValueError, match="prior causal evidence"):
        RecoveryDecidedFact(**values, reconciling_evidence_id="recovery-evidence", continuation=None)
