from dataclasses import replace

from manual_picking.application.plan_admission import ManualPickingPlanAdmissionPolicy
from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecision,
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
    PositionBindingSnapshot,
)


def test_manual_picking_accepts_plans_with_direct_picks() -> None:
    fact = PickingTaskPlanAdmissionFact(
        task_id="PICK-DIRECT", plan_revision=1, has_direct_picks=True, has_target_rack=False, position_bindings=()
    )

    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT
    )


def test_manual_picking_accepts_plans_without_direct_picks() -> None:
    fact = PickingTaskPlanAdmissionFact(
        task_id="PICK-BIN", plan_revision=1, has_direct_picks=False, has_target_rack=False, position_bindings=()
    )

    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT
    )


def test_manual_picking_policy_is_pure() -> None:
    policy = ManualPickingPlanAdmissionPolicy()
    fact = PickingTaskPlanAdmissionFact(
        task_id="PICK-BIN", plan_revision=1, has_direct_picks=False, has_target_rack=False, position_bindings=()
    )

    assert policy(fact) == policy(fact)
    assert fact == replace(fact)


def test_managed_target_requires_a_transfer_rack_position() -> None:
    fact = PickingTaskPlanAdmissionFact(
        task_id="PICK-TARGET",
        plan_revision=1,
        has_direct_picks=False,
        has_target_rack=True,
        position_bindings=(),
    )
    assert ManualPickingPlanAdmissionPolicy()(fact) == PickingTaskPlanAdmissionDecision(
        kind=PickingTaskPlanAdmissionDecisionKind.REJECT, reason_code="REFERENCE_CONFLICT"
    )
    bound = replace(
        fact,
        position_bindings=(
            PositionBindingSnapshot(
                position_role="TRANSFER_RACK",
                location_type="RACK_POSITION",
                location_id="TRANSFER-POS",
            ),
        ),
    )
    assert ManualPickingPlanAdmissionPolicy()(bound).kind is PickingTaskPlanAdmissionDecisionKind.ACCEPT
