"""人工拣料 PickingTask 计划的纯业务准入策略。"""

from wes_plugin_sdk import (
    PickingTaskPlanAdmissionDecision,
    PickingTaskPlanAdmissionDecisionKind,
    PickingTaskPlanAdmissionFact,
)


class ManualPickingPlanAdmissionPolicy:
    """人工拣料接纳所有 PickingTask 计划，包括 Bin 计划和 direct-pick。"""

    def __call__(self, _fact: PickingTaskPlanAdmissionFact) -> PickingTaskPlanAdmissionDecision:
        return PickingTaskPlanAdmissionDecision(kind=PickingTaskPlanAdmissionDecisionKind.ACCEPT)


__all__ = ["ManualPickingPlanAdmissionPolicy"]
