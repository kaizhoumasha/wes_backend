"""已应用 PickingTask 计划到货架进场意图的纯映射。"""

from wes_plugin_sdk import (
    PickingTaskPlanAppliedFact,
    PickingTaskPlanHandlingResult,
    PickingTaskRackTransportIntent,
    PositionBindingSnapshot,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
)

from manual_picking.definition import RETURN_RACK, TRANSFER_RACK


def _required_rack_position(
    bindings: tuple[PositionBindingSnapshot, ...],
    *,
    position_role: str,
) -> TransportRackPosition:
    matches = tuple(binding for binding in bindings if binding.position_role == position_role)
    if len(matches) != 1 or matches[0].location_type != "RACK_POSITION":
        raise ValueError(f"required RACK_POSITION position binding is unavailable: {position_role}")
    return TransportRackPosition(matches[0].location_id)


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
        transports.extend(
            PickingTaskRackTransportIntent(
                task_id=fact.task_id,
                fact_id=fact.fact_id,
                source_evidence_id=return_rack.source_evidence_id,
                position_role=RETURN_RACK.slot_key,
                rack_id=return_rack.rack_id,
                source=TransportRackReference(return_rack.rack_id),
                target=_required_rack_position(
                    fact.position_bindings,
                    position_role=RETURN_RACK.slot_key,
                ),
                target_face=return_rack.rack_faces[0],
                rcs_template_id=TransportRcsTemplateId.F01,
            )
            for return_rack in fact.pending_return_racks
        )

        return PickingTaskPlanHandlingResult(transports=tuple(transports))


__all__ = ["PickingTaskPlanAppliedHandler"]
