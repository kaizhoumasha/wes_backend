"""人工拣料 PickingTask 计划的纯业务准入策略。"""

from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecision,
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
)

from manual_picking.definition import TRANSFER_RACK


class ManualPickingPlanAdmissionPolicy:
    """接纳 Bin 和 direct-pick 计划；受管目标架必须具有转运位置。"""

    def __call__(self, fact: PickingTaskPlanAdmissionFact) -> PickingTaskPlanAdmissionDecision:
        if fact.has_target_rack:
            targets = tuple(
                binding for binding in fact.position_bindings if binding.position_role == TRANSFER_RACK.slot_key
            )
            if len(targets) != 1 or targets[0].location_type != "RACK_POSITION":
                return PickingTaskPlanAdmissionDecision(
                    kind=PickingTaskPlanAdmissionDecisionKind.REJECT, reason_code="REFERENCE_CONFLICT"
                )
        return PickingTaskPlanAdmissionDecision(kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT)


__all__ = ["ManualPickingPlanAdmissionPolicy"]
