from dataclasses import replace

import pytest
from manual_picking.handlers import PickingTaskPlanAppliedHandler
from wes_plugin_sdk import (
    PickingTaskPlanAppliedFact,
    PickingTaskPlanRack,
    PositionBindingSnapshot,
    TransportRackPosition,
    TransportRackReference,
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

    plugin = build_plugin()

    assert type(plugin.picking_task_plan_applied_handler) is PickingTaskPlanAppliedHandler


def test_plan_rack_transport_intent_preserves_source_evidence_identity() -> None:
    try:
        rack = PickingTaskPlanRack("FIVE-1", ("90",), source_evidence_id="101", plan_revision=1)
    except TypeError as exc:
        pytest.fail(f"plan rack must accept source evidence identity: {exc}")

    fact = replace(FACT, target_rack=None, pending_bin_source_racks=(rack,))

    result = PickingTaskPlanAppliedHandler()(fact)

    assert result.transports[0].source_evidence_id == "101"


def test_plan_handler_owns_stable_revision_then_rack_scheduling_order() -> None:
    try:
        later = PickingTaskPlanRack("BIN-A", ("90",), "102", plan_revision=2)
        earlier = PickingTaskPlanRack("BIN-B", ("270",), "101", plan_revision=1)
    except TypeError as exc:
        pytest.fail(f"plan rack must expose its first plan revision: {exc}")
    fact = replace(
        FACT,
        target_rack=None,
        pending_bin_source_racks=(later, earlier),
    )

    result = PickingTaskPlanAppliedHandler()(fact)

    assert [intent.rack_id for intent in result.transports] == ["BIN-B", "BIN-A"]


def test_plan_handler_owns_deterministic_face_order() -> None:
    fact = replace(
        FACT,
        target_rack=None,
        pending_bin_source_racks=(PickingTaskPlanRack("BIN-1", ("Z", "A"), "101", 1),),
    )

    result = PickingTaskPlanAppliedHandler()(fact)

    assert result.transports[0].target_face == "A"


def test_plan_applied_maps_target_and_all_bin_racks_to_transport_intents() -> None:
    result = PickingTaskPlanAppliedHandler()(FACT)

    assert len(result.transports) == 3
    target, source, queued_source = result.transports
    assert target.task_id == FACT.task_id
    assert target.fact_id == FACT.fact_id
    assert target.source_evidence_id == "100"
    assert target.rack_id == "TRANSFER-1"
    assert target.source == TransportRackReference("TRANSFER-1")
    assert target.target == TransportRackPosition("TRANSFER-RACK-POSITION")
    assert target.target_face == "90"
    assert target.rcs_template_id is TransportRcsTemplateId.F01
    assert source.rack_id == "FIVE-1"
    assert source.source_evidence_id == "101"
    assert source.source == TransportRackReference("FIVE-1")
    assert source.target == TransportRackPosition("FIVE-RACK-POSITION")
    assert source.target_face == "270"
    assert source.rcs_template_id is TransportRcsTemplateId.CTU01
    assert queued_source.rack_id == "FIVE-2"
    assert queued_source.target == source.target
    assert queued_source.rcs_template_id is TransportRcsTemplateId.CTU01


def test_plan_applied_groups_faces_by_physical_rack_and_preserves_face_values() -> None:
    fact = replace(
        FACT,
        target_rack=None,
        pending_bin_source_racks=(
            PickingTaskPlanRack("FIVE-1", ("270", "90"), source_evidence_id="101", plan_revision=1),
            PickingTaskPlanRack("FIVE-2", (" opaque ",), source_evidence_id="102", plan_revision=2),
        ),
    )

    result = PickingTaskPlanAppliedHandler()(fact)

    assert [(item.rack_id, item.target_face) for item in result.transports] == [
        ("FIVE-1", "270"),
        ("FIVE-2", " opaque "),
    ]


@pytest.mark.parametrize(
    "position_bindings",
    [
        (POSITIONS[0],),
        (POSITIONS[1],),
        (
            PositionBindingSnapshot("FIVE_RACK", "FIVE-RACK-POSITION", "ZONE"),
            POSITIONS[1],
        ),
    ],
)
def test_plan_applied_fails_closed_for_missing_or_invalid_required_position(
    position_bindings: tuple[PositionBindingSnapshot, ...],
) -> None:
    with pytest.raises(ValueError, match="position binding"):
        PickingTaskPlanAppliedHandler()(replace(FACT, position_bindings=position_bindings))


def test_plan_applied_requires_bin_position_when_bin_racks_are_pending() -> None:
    with pytest.raises(ValueError, match="FIVE_RACK"):
        PickingTaskPlanAppliedHandler()(
            replace(
                FACT,
                target_rack=None,
                position_bindings=(POSITIONS[1],),
            )
        )


def test_plan_applied_fact_rejects_duplicate_physical_racks() -> None:
    with pytest.raises(ValueError, match="duplicate rack_id"):
        replace(
            FACT,
            pending_bin_source_racks=(
                PickingTaskPlanRack("FIVE-1", ("90",), source_evidence_id="101", plan_revision=1),
                PickingTaskPlanRack("FIVE-1", ("270",), source_evidence_id="102", plan_revision=2),
            ),
        )
