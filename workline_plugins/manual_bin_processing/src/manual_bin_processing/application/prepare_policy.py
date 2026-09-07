"""人工 PickingTask 的纯准入策略；宿主拥有事实读取、锁与可靠事务。"""

from datetime import datetime, timedelta

from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareRuntimeFacts, PrepareTaskType


class ManualPickingPreparePolicy:
    def select_task_type(self, context: PrepareContext) -> PrepareTaskType | None:
        if (
            context.is_active
            and context.line_type == "MANUAL"
            and context.run_mode == "AUTO"
            and context.plugin_key == "manual_bin_processing"
            and context.flow_mode == "MANUAL_BIN_PROCESSING"
        ):
            return PrepareTaskType.MANUAL
        return None

    def is_ready(self, facts: PrepareRuntimeFacts, *, now: datetime) -> bool:
        if (
            facts.has_active_incident
            or not facts.has_position_bindings
            or not facts.devices
            or facts.has_positioned_object
        ):
            return False
        return all(
            device.observed_contract_key == device.contract_key
            and device.observed_contract_version == device.contract_version
            and device.received_at is not None
            and device.received_at >= now - timedelta(milliseconds=device.status_max_age_ms)
            and device.mode == "AUTO"
            and device.status == "IDLE"
            and device.current_command_code is None
            for device in facts.devices
        )
