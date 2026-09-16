from dataclasses import replace

import pytest
from manual_picking.handlers import PickingTaskPlanAppliedHandler
from wes_plugin_sdk import (
    PickingTaskPlanAppliedFact,
    PickingTaskPlanRack,
    PositionBindingSnapshot,
    TransportRackPosition,
    TransportRcsTemplateId,
)

POSITIONS = (
    PositionBindingSnapshot("FIVE_RACK", "FIVE-RACK-POSITION", "RACK_POSITION"),
    PositionBindingSnapshot("TRANSFER_RACK", "TRANSFER-RACK-POSITION", "RACK_POSITION"),
)
FACT = PickingTaskPlanAppliedFact(
    fact_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
    evidence_id="101",
    fact_version="1.0",
    task_id="PICK-1",
    plan_revision=1,
    target_rack=PickingTaskPlanRack("TRANSFER-1", ("90",), "100", 1),
    pending_bin_source_racks=(
        PickingTaskPlanRack("FIVE-1", ("90", "270"), "101", 1),
        PickingTaskPlanRack("FIVE-2", ("SIDE-X",), "102", 2),
    ),
    position_bindings=POSITIONS,
)


def test_plugin_assembles_plan_applied_handler() -> None:
    from manual_picking.application.plugin import build_plugin

    plugin = build_plugin(scan_flow=object(), batch_driver=object(), completion_driver=object())

    assert type(plugin.picking_task_plan_applied_handler) is PickingTaskPlanAppliedHandler


def test_plan_handler_only_creates_target_and_defers_sources_to_batch_driver() -> None:
    result = PickingTaskPlanAppliedHandler()(FACT)
    assert len(result.transports) == 1
    target = result.transports[0]
    assert target.rack_id == "TRANSFER-1"
    assert target.source_evidence_id == "100"
    assert target.target == TransportRackPosition("TRANSFER-RACK-POSITION")
    assert target.target_face == "90"
    assert target.rcs_template_id is TransportRcsTemplateId.F01
    assert PickingTaskPlanAppliedHandler()(replace(FACT, target_rack=None)).transports == ()


def test_plan_handler_requires_target_position() -> None:
    with pytest.raises(ValueError, match="position binding"):
        PickingTaskPlanAppliedHandler()(replace(FACT, position_bindings=(POSITIONS[0],)))


def test_plan_applied_fact_rejects_duplicate_physical_racks() -> None:
    with pytest.raises(ValueError, match="duplicate rack_id"):
        replace(
            FACT,
            pending_bin_source_racks=(
                PickingTaskPlanRack("FIVE-1", ("90",), source_evidence_id="101", plan_revision=1),
                PickingTaskPlanRack("FIVE-1", ("270",), source_evidence_id="102", plan_revision=2),
            ),
        )
