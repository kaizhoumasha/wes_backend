from dataclasses import replace

from manual_picking.application.plan_admission import ManualPickingPlanAdmissionPolicy
from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecision,
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
)


def test_manual_picking_rejects_when_fact_has_direct_picks() -> None:
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-DIRECT", plan_revision=1, has_direct_picks=True)

    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.REJECT,
        reason_code="MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED",
    )


def test_manual_picking_accepts_plans_without_direct_picks() -> None:
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-BIN", plan_revision=1, has_direct_picks=False)

    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT
    )


def test_manual_picking_policy_reason_code_is_stable() -> None:
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-DIRECT", plan_revision=1, has_direct_picks=True)

    assert ManualPickingPlanAdmissionPolicy()(fact).reason_code == "MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED"


def test_manual_picking_policy_is_pure() -> None:
    policy = ManualPickingPlanAdmissionPolicy()
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-BIN", plan_revision=1, has_direct_picks=False)

    assert policy(fact) == policy(fact)
    assert fact == replace(fact)
