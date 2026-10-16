import pytest
from wes_plugin_sdk import PickingTaskPlanAppliedFact, PickingTaskPlanRack, PositionBindingSnapshot


def fact():
    return PickingTaskPlanAppliedFact(
        fact_id="plan:1",
        evidence_id="31",
        fact_version="1.0",
        task_id="TASK-1",
        plan_revision=2,
        target_rack=PickingTaskPlanRack("TARGET", ("B",), "30", 1),
        pending_bin_source_racks=(PickingTaskPlanRack("SOURCE", ("A", "B"), "31", 2),),
        pending_return_racks=(PickingTaskPlanRack("RETURN", ("C",), "31", 2),),
        position_bindings=tuple(
            PositionBindingSnapshot(role, code, "RACK_POSITION")
            for role, code in [("TRANSFER_RACK", "T-1"), ("FIVE_RACK", "S-1"), ("RETURN_RACK", "R-1")]
        ),
    )


def test_applied_plan_uses_bound_positions_and_each_original_member():
    from automatic_picking.handlers.picking_task_plan_applied import PickingTaskPlanAppliedHandler

    results = PickingTaskPlanAppliedHandler()(fact())
    assert [(i.rack_id, i.target.location_code, i.target_face, i.source_evidence_id) for i in results.transports] == [
        ("TARGET", "T-1", "B", "30"),
        ("SOURCE", "S-1", "A", "31"),
        ("RETURN", "R-1", "C", "31"),
    ]


def test_missing_position_is_rejected_before_creating_intents():
    from dataclasses import replace

    from automatic_picking.handlers.picking_task_plan_applied import PickingTaskPlanAppliedHandler

    with pytest.raises(ValueError):
        PickingTaskPlanAppliedHandler()(replace(fact(), position_bindings=()))
