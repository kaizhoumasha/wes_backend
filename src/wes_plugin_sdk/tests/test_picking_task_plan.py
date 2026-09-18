"""PickingTask plan admission SDK contract tests."""

from dataclasses import FrozenInstanceError
from inspect import getsourcefile, signature
from pathlib import Path

import pytest
import wes_plugin_sdk as sdk


def test_admission_fact_is_immutable_and_accepts_bin_only_plan():
    fact = sdk.PickingTaskPlanAdmissionFact(task_id="PICK-1", plan_revision=1, has_direct_picks=False)

    assert fact.task_id == "PICK-1"  # nosec B101 - pytest assertion
    assert fact.plan_revision == 1  # nosec B101 - pytest assertion
    assert fact.has_direct_picks is False  # nosec B101 - pytest assertion
    with pytest.raises(FrozenInstanceError):
        fact.task_id = "PICK-2"


def test_admission_fact_rejects_invalid_identity_values():
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAdmissionFact(task_id=" ", plan_revision=1, has_direct_picks=False)
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAdmissionFact(task_id="PICK-1", plan_revision=0, has_direct_picks=False)
    with pytest.raises(TypeError):
        sdk.PickingTaskPlanAdmissionFact(task_id="PICK-1", plan_revision=1, has_direct_picks=0)


def test_direct_pick_rejection_requires_and_preserves_stable_reason_code():
    decision = sdk.PickingTaskPlanAdmissionDecision(
        kind=sdk.PickingTaskPlanAdmissionDecisionKind.REJECT,
        reason_code="MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED",
    )

    assert decision.kind is sdk.PickingTaskPlanAdmissionDecisionKind.REJECT  # nosec B101 - pytest assertion
    assert decision.reason_code == "MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED"  # nosec B101 - pytest assertion
    with pytest.raises(FrozenInstanceError):
        decision.reason_code = "OTHER_REASON"
    assert decision == sdk.PickingTaskPlanAdmissionDecision(  # nosec B101 - pytest assertion
        kind=sdk.PickingTaskPlanAdmissionDecisionKind.REJECT,
        reason_code="MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED",
    )

    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAdmissionDecision(
            kind=sdk.PickingTaskPlanAdmissionDecisionKind.REJECT,
            reason_code="",
        )
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAdmissionDecision(
            kind=sdk.PickingTaskPlanAdmissionDecisionKind.REJECT,
            reason_code=" ",
        )


def test_accept_decision_is_closed_and_has_no_rejection_reason():
    decision = sdk.PickingTaskPlanAdmissionDecision(kind=sdk.PickingTaskPlanAdmissionDecisionKind.ACCEPT)

    assert decision.kind is sdk.PickingTaskPlanAdmissionDecisionKind.ACCEPT  # nosec B101 - pytest assertion
    assert decision.reason_code is None  # nosec B101 - pytest assertion
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAdmissionDecision(
            kind=sdk.PickingTaskPlanAdmissionDecisionKind.ACCEPT,
            reason_code="UNEXPECTED_REASON",
        )


def test_applied_fact_defaults_pending_return_racks_to_empty_tuple():
    fact = sdk.PickingTaskPlanAppliedFact(
        fact_id="fact-1",
        evidence_id="ev-1",
        fact_version="1.0",
        task_id="PICK-1",
        plan_revision=1,
        target_rack=None,
        pending_bin_source_racks=(),
        position_bindings=(),
    )

    assert fact.pending_return_racks == ()  # nosec B101 - pytest assertion


def test_applied_fact_accepts_pending_return_racks():
    rack = sdk.PickingTaskPlanRack("RET-1", ("A",), "ev-1", 1)

    fact = sdk.PickingTaskPlanAppliedFact(
        fact_id="fact-1",
        evidence_id="ev-1",
        fact_version="1.0",
        task_id="PICK-1",
        plan_revision=1,
        target_rack=None,
        pending_bin_source_racks=(),
        pending_return_racks=(rack,),
        position_bindings=(),
    )

    assert fact.pending_return_racks == (rack,)  # nosec B101 - pytest assertion


def test_applied_fact_rejects_non_tuple_pending_return_racks():
    with pytest.raises(TypeError):
        sdk.PickingTaskPlanAppliedFact(
            fact_id="fact-1",
            evidence_id="ev-1",
            fact_version="1.0",
            task_id="PICK-1",
            plan_revision=1,
            target_rack=None,
            pending_bin_source_racks=(),
            pending_return_racks=[sdk.PickingTaskPlanRack("RET-1", ("A",), "ev-1", 1)],
            position_bindings=(),
        )


def test_applied_fact_rejects_duplicate_return_rack_ids():
    rack = sdk.PickingTaskPlanRack("RET-1", ("A",), "ev-1", 1)
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAppliedFact(
            fact_id="fact-1",
            evidence_id="ev-1",
            fact_version="1.0",
            task_id="PICK-1",
            plan_revision=1,
            target_rack=None,
            pending_bin_source_racks=(),
            pending_return_racks=(rack, rack),
            position_bindings=(),
        )


def test_applied_fact_rejects_return_rack_id_overlap_with_target_or_bin_rack():
    target = sdk.PickingTaskPlanRack("RET-1", ("A",), "ev-1", 1)
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAppliedFact(
            fact_id="fact-1",
            evidence_id="ev-1",
            fact_version="1.0",
            task_id="PICK-1",
            plan_revision=1,
            target_rack=target,
            pending_bin_source_racks=(),
            pending_return_racks=(sdk.PickingTaskPlanRack("RET-1", ("B",), "ev-2", 1),),
            position_bindings=(),
        )
    bin_rack = sdk.PickingTaskPlanRack("BIN-1", ("90",), "ev-1", 1)
    with pytest.raises(ValueError):
        sdk.PickingTaskPlanAppliedFact(
            fact_id="fact-1",
            evidence_id="ev-1",
            fact_version="1.0",
            task_id="PICK-1",
            plan_revision=1,
            target_rack=None,
            pending_bin_source_racks=(bin_rack,),
            pending_return_racks=(sdk.PickingTaskPlanRack("BIN-1", ("A",), "ev-2", 1),),
            position_bindings=(),
        )


def test_admission_policy_is_a_pure_typed_protocol():
    assert callable(sdk.PickingTaskPlanAdmissionPolicy)  # nosec B101 - pytest assertion
    policy_signature = signature(sdk.PickingTaskPlanAdmissionPolicy.__call__)
    assert tuple(policy_signature.parameters) == ("self", "fact")  # nosec B101 - pytest assertion
    assert policy_signature.parameters["fact"].annotation is sdk.PickingTaskPlanAdmissionFact  # nosec B101 - pytest assertion
    assert policy_signature.return_annotation is sdk.PickingTaskPlanAdmissionDecision  # nosec B101 - pytest assertion
    source = Path(getsourcefile(sdk.PickingTaskPlanAdmissionFact) or "")
    assert source.name == "picking_task_plan.py"  # nosec B101 - pytest assertion
    module_source = source.read_text(encoding="utf-8")
    for forbidden in ("sqlalchemy", "httpx", "celery", "manual_picking"):
        assert forbidden not in module_source.lower()  # nosec B101 - pytest assertion
