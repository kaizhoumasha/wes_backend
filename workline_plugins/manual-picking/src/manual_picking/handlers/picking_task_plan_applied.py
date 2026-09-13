"""已应用 PickingTask 计划到货架进场意图的纯映射。"""

from wes_plugin_sdk import (
    PickingTaskPlanAppliedFact,
    PickingTaskPlanHandlingResult,
    PickingTaskRackTransportIntent,
    PositionBindingSnapshot,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    handler,
)

from manual_picking.definition import FIVE_RACK, TRANSFER_RACK


def _required_rack_position(
    bindings: tuple[PositionBindingSnapshot, ...],
    *,
    position_role: str,
) -> TransportRackPosition:
    matches = tuple(binding for binding in bindings if binding.position_role == position_role)
    if len(matches) != 1 or matches[0].location_type != "RACK_POSITION":
        raise ValueError(f"required RACK_POSITION position binding is unavailable: {position_role}")
    return TransportRackPosition(matches[0].location_id)


@handler(
    fact_type=PickingTaskPlanAppliedFact,
    name="picking_task_plan_applied",
    supported_versions=("1.0",),
)
class PickingTaskPlanAppliedHandler:
    """只决定计划资源如何进场；宿主负责持久化、幂等与 Transport。"""

    def __call__(self, fact: PickingTaskPlanAppliedFact) -> PickingTaskPlanHandlingResult:
        transports: list[PickingTaskRackTransportIntent] = []
        if fact.target_rack is not None:
            transports.append(
                PickingTaskRackTransportIntent(
                    task_id=fact.task_id,
                    fact_id=fact.fact_id,
                    source_evidence_id=fact.target_rack.source_evidence_id,
                    position_role=TRANSFER_RACK.slot_key,
                    rack_id=fact.target_rack.rack_id,
                    source=TransportRackReference(fact.target_rack.rack_id),
                    target=_required_rack_position(
                        fact.position_bindings,
                        position_role=TRANSFER_RACK.slot_key,
                    ),
                    target_face=fact.target_rack.rack_faces[0],
                    rcs_template_id=TransportRcsTemplateId.F01,
                )
            )

        ordered_bin_racks = tuple(
            sorted(fact.pending_bin_source_racks, key=lambda rack: (rack.plan_revision, rack.rack_id))
        )
        bin_source_target = (
            _required_rack_position(fact.position_bindings, position_role=FIVE_RACK.slot_key)
            if ordered_bin_racks
            else None
        )
        if bin_source_target is not None:
            transports.extend(
                PickingTaskRackTransportIntent(
                    task_id=fact.task_id,
                    fact_id=fact.fact_id,
                    source_evidence_id=rack.source_evidence_id,
                    position_role=FIVE_RACK.slot_key,
                    rack_id=rack.rack_id,
                    source=TransportRackReference(rack.rack_id),
                    target=bin_source_target,
                    target_face=min(rack.rack_faces),
                    rcs_template_id=TransportRcsTemplateId.CTU01,
                )
                for rack in ordered_bin_racks
            )
        return PickingTaskPlanHandlingResult(transports=tuple(transports))


__all__ = ["PickingTaskPlanAppliedHandler"]
