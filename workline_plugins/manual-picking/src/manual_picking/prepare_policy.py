"""人工 PickingTask 的纯准入策略。"""

from datetime import datetime

from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareRuntimeFacts, PrepareTaskType

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

    def is_ready(self, facts: PrepareRuntimeFacts, *, now: datetime) -> bool:
        del now
        expected_devices = sorted(role.role_key for role in DEFINITION.device_roles)
        expected_positions = sorted(slot.slot_key for slot in DEFINITION.position_slots)
        return sorted(facts.device_roles) == expected_devices and sorted(facts.position_roles) == expected_positions


__all__ = ["ManualPickingPreparePolicy"]
