"""人工拣料 PickingTask 计划的纯业务准入策略。"""

from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecision,
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
)


class ManualPickingPlanAdmissionPolicy:
    """人工拣料只接纳 Bin 计划，不接纳 direct-pick。"""

    def __call__(self, fact: PickingTaskPlanAdmissionFact) -> PickingTaskPlanAdmissionDecision:
        if fact.has_direct_picks:
            return PickingTaskPlanAdmissionDecision(
                kind=PickingTaskPlanAdmissionDecisionKind.REJECT,
                reason_code="MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED",
            )
        return PickingTaskPlanAdmissionDecision(kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT)


__all__ = ["ManualPickingPlanAdmissionPolicy"]
