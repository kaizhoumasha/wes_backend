"""自动线准备准入；可靠派发由宿主负责。"""

from wes_plugin_sdk.prepare_policy import PrepareTaskType

from .definition import DEFINITION


class AutomaticPickingPreparePolicy:
    def select_task_type(self, context):
        if (
            context.is_active
            and context.line_type == "AUTO"
            and context.run_mode == "AUTO"
            and context.plugin_key == DEFINITION.plugin_key
            and context.flow_mode is None
        ):
            return PrepareTaskType.AUTO
        return None
