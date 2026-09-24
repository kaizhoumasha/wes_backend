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
    PositionBindingSnapshot("RETURN_RACK", "RETURN-RACK-POSITION", "RACK_POSITION"),
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


def test_plan_applied_fact_distinguishes_members_across_revisions() -> None:
    with pytest.raises(ValueError, match="duplicate members"):
        replace(
            FACT,
            pending_bin_source_racks=(
                PickingTaskPlanRack("FIVE-1", ("90",), source_evidence_id="101", plan_revision=1),
                PickingTaskPlanRack("FIVE-1", ("270",), source_evidence_id="101", plan_revision=1),
            ),
        )
    repeated_rack = replace(
        FACT,
        pending_bin_source_racks=(
            PickingTaskPlanRack("FIVE-1", ("90",), source_evidence_id="101", plan_revision=1),
            PickingTaskPlanRack("FIVE-1", ("90",), source_evidence_id="102", plan_revision=2),
        ),
    )
    assert len(PickingTaskPlanAppliedHandler()(repeated_rack).transports) == 1


def test_plan_handler_creates_return_rack_intent_per_rack_with_f01() -> None:
    fact = replace(
        FACT,
        pending_return_racks=(
            PickingTaskPlanRack("RET-1", ("A",), "200", 1),
            PickingTaskPlanRack("RET-2", ("A", "B"), "201", 1),
        ),
    )

    result = PickingTaskPlanAppliedHandler()(fact)

    by_rack = {intent.rack_id: intent for intent in result.transports}
    assert set(by_rack) == {"TRANSFER-1", "RET-1", "RET-2"}
    for rack_id in ("RET-1", "RET-2"):
        intent = by_rack[rack_id]
        assert intent.position_role == "RETURN_RACK"
        assert intent.target == TransportRackPosition("RETURN-RACK-POSITION")
        assert intent.rcs_template_id is TransportRcsTemplateId.F01
        assert intent.task_id == "PICK-1"
        assert intent.fact_id == fact.fact_id
    assert by_rack["RET-1"].source_evidence_id == "200"
    assert by_rack["RET-1"].target_face == "A"
    assert by_rack["RET-2"].target_face == "A"


def test_plan_handler_emits_one_intent_per_unique_return_rack() -> None:
    fact = replace(
        FACT,
        pending_return_racks=(
            PickingTaskPlanRack("RET-1", ("A", "B"), "200", 1),
            PickingTaskPlanRack("RET-2", ("A",), "201", 1),
        ),
    )

    result = PickingTaskPlanAppliedHandler()(fact)

    assert [intent.rack_id for intent in result.transports] == ["TRANSFER-1", "RET-1", "RET-2"]
    by_rack = {intent.rack_id: intent for intent in result.transports}
    assert by_rack["RET-1"].target_face == "A"
    assert by_rack["RET-2"].source_evidence_id == "201"


def test_plan_handler_returns_empty_when_no_pending_racks() -> None:
    fact = replace(FACT, target_rack=None, pending_return_racks=())

    assert PickingTaskPlanAppliedHandler()(fact).transports == ()


def test_plan_handler_requires_return_rack_position_binding() -> None:
    fact = replace(
        FACT,
        target_rack=None,
        position_bindings=(POSITIONS[0],),
        pending_return_racks=(PickingTaskPlanRack("RET-1", ("A",), "200", 1),),
    )

    with pytest.raises(ValueError, match="RETURN_RACK"):
        PickingTaskPlanAppliedHandler()(fact)


def test_plan_handler_does_not_mutate_return_racks_when_target_only() -> None:
    result = PickingTaskPlanAppliedHandler()(FACT)

    assert [intent.position_role for intent in result.transports] == ["TRANSFER_RACK"]
