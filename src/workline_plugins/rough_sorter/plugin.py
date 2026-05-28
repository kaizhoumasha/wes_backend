"""粗分机工作线插件。"""

from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    ACTION_TARGET_ROLES,
    CONTRACT_VERSION,
    EVENT_SCAN_COMPLETED,
    NG_REASON_BARCODE_INCOMPLETE,
    NG_REASON_BARCODE_INVALID,
    NG_REASON_BARCODE_RULE_NG,
    NG_REASON_MEASUREMENT_NG,
    NG_REASON_WMS_REJECTED,
    PLUGIN_KEY,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_MEASURER,
    ROLE_OUTPUT_ARM,
    ROLE_SCANNER,
    classify_rough_sorter_result,
    resolve_rough_sorter_business_key,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_base import WorklinePlugin, on_event
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent


def _ng_reason(canonical_code: str, label: str) -> NgReasonDefinition:
    return NgReasonDefinition(
        canonical_code=canonical_code,
        label=label,
        source=NgReasonSource.PLUGIN,
        plugin_key=PLUGIN_KEY,
        contract_version=CONTRACT_VERSION,
        maps_from=(canonical_code,),
    )


class RoughSorterPlugin(WorklinePlugin):
    """粗分机插件。"""

    plugin_key = PLUGIN_KEY
    contract_version = CONTRACT_VERSION

    manifest = WorklinePluginManifest(
        plugin_key=PLUGIN_KEY,
        contract_version=CONTRACT_VERSION,
        required_device_roles=(
            DeviceRoleRequirement(role=ROLE_SCANNER, min_count=1),
            DeviceRoleRequirement(role=ROLE_MEASURER, min_count=1),
            DeviceRoleRequirement(role=ROLE_INPUT_ARM, min_count=1),
            DeviceRoleRequirement(role=ROLE_CONVEYOR, min_count=1),
            DeviceRoleRequirement(role=ROLE_OUTPUT_ARM, min_count=1),
        ),
        business_key_resolver=resolve_rough_sorter_business_key,
        result_classifier=classify_rough_sorter_result,
        context_model=RoughSorterContext,
        supported_events=frozenset({EVENT_SCAN_COMPLETED}),
        supported_commands=frozenset(ACTION_TARGET_ROLES),
        event_source_roles={EVENT_SCAN_COMPLETED: ROLE_SCANNER},
        command_target_roles={
            ACTION_MEASUREMENT_REEL: ROLE_MEASURER,
            ACTION_PICK_AND_PUT: ROLE_INPUT_ARM,
            ACTION_MOVE_FORWARD: ROLE_CONVEYOR,
            ACTION_PUT_TO_BIN: ROLE_OUTPUT_ARM,
            ACTION_MOVE_TO_NG: ROLE_OUTPUT_ARM,
        },
        ng_reason_catalog=(
            _ng_reason(NG_REASON_BARCODE_INVALID, "条码无效"),
            _ng_reason(NG_REASON_BARCODE_INCOMPLETE, "条码不完整"),
            _ng_reason(NG_REASON_BARCODE_RULE_NG, "条码规则判定 NG"),
            _ng_reason(NG_REASON_MEASUREMENT_NG, "测量业务判定 NG"),
            _ng_reason(NG_REASON_WMS_REJECTED, "WMS 库存校验拒绝"),
        ),
    )

    @on_event(EVENT_SCAN_COMPLETED)
    async def handle_scan_completed(self, _ctx, _inbox) -> list[RuntimeIntent]:
        """Task 1 仅交付合同，入口事件必须显式阻塞而不是静默丢弃。"""

        return [
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code="ROUGH_SORTER_HANDLER_NOT_IMPLEMENTED",
                message="粗分机扫码入口将在 Task 2 实现，当前合同层只允许显式阻塞",
                suggested_action="完成 rough_sorter Task 2 后再启用真实入口流程",
            )
        ]


rough_sorter_plugin = RoughSorterPlugin()

__all__ = ["RoughSorterPlugin", "rough_sorter_plugin"]
