"""按原计划成员生成货架进场意图；可靠身份由宿主冻结。"""

from wes_plugin_sdk import (
    PickingTaskPlanHandlingResult,
    PickingTaskRackTransportIntent,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
)


class PickingTaskPlanAppliedHandler:
    def __call__(self, fact):
        resources = [(r, "FIVE_RACK") for r in fact.pending_bin_source_racks]
        resources.extend((r, "RETURN_RACK") for r in fact.pending_return_racks)
        if fact.target_rack is not None:
            resources.insert(0, (fact.target_rack, "TRANSFER_RACK"))
        intents = []
        for rack, role in resources:
            bindings = tuple(b for b in fact.position_bindings if b.position_role == role)
            if len(bindings) != 1 or bindings[0].location_type != "RACK_POSITION":
                raise ValueError(f"missing bound RACK_POSITION: {role}")
            intents.append(
                PickingTaskRackTransportIntent(
                    task_id=fact.task_id,
                    fact_id=fact.fact_id,
                    source_evidence_id=rack.source_evidence_id,
                    position_role=role,
                    rack_id=rack.rack_id,
                    source=TransportRackReference(rack.rack_id),
                    target=TransportRackPosition(bindings[0].location_id),
                    target_face=rack.rack_faces[0],
                    rcs_template_id=TransportRcsTemplateId.F01,
                )
            )
        return PickingTaskPlanHandlingResult(transports=tuple(intents))
