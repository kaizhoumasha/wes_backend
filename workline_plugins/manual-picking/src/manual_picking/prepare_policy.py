"""人工 PickingTask 的纯准入策略。"""

from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareTaskType

from manual_picking.definition import DEFINITION


class ManualPickingPreparePolicy:
    """选择 MANUAL 任务；宿主负责事实读取、并发领取和可靠事务。"""

    def select_task_type(self, context: PrepareContext) -> PrepareTaskType | None:
        if (
            context.is_active
            and context.line_type == "MANUAL"
            and context.run_mode == "AUTO"
            and context.plugin_key == DEFINITION.plugin_key
            and context.flow_mode is None
        ):
            return PrepareTaskType.MANUAL
        return None


__all__ = ["ManualPickingPreparePolicy"]
