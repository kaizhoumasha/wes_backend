from dataclasses import replace

from manual_picking.application.plan_admission import ManualPickingPlanAdmissionPolicy
from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecision,
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
)


def test_manual_picking_accepts_plans_with_direct_picks() -> None:
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-DIRECT", plan_revision=1, has_direct_picks=True)

    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT
    )


def test_manual_picking_accepts_plans_without_direct_picks() -> None:
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-BIN", plan_revision=1, has_direct_picks=False)

    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT
    )


def test_manual_picking_policy_is_pure() -> None:
    policy = ManualPickingPlanAdmissionPolicy()
    fact = PickingTaskPlanAdmissionFact(task_id="PICK-BIN", plan_revision=1, has_direct_picks=False)

    assert policy(fact) == policy(fact)
    assert fact == replace(fact)
