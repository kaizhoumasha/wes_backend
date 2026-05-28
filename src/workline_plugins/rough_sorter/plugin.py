"""粗分机工作线插件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.workline.domain.models import BarcodeDecisionType
from src.app.workline.domain.services.barcode_decision_service import barcode_decision_service
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_TO_NG,
    ACTION_TARGET_ROLES,
    EVENT_SCAN_COMPLETED,
    NG_REASON_BARCODE_INCOMPLETE,
    NG_REASON_BARCODE_INVALID,
    NG_REASON_BARCODE_RULE_NG,
    NG_REASON_MEASUREMENT_NG,
    NG_REASON_WMS_REJECTED,
    PHASE_MEASURING,
    PHASE_NG_MOVING,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
    build_measurement_reel_payload,
    build_move_to_ng_payload,
    classify_rough_sorter_result,
    normalize_six_in_one_payload,
    resolve_rough_sorter_business_key,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

DEFAULT_NG_LOCATION = "NG-01"


def _ng_reason(canonical_code: str, label: str) -> NgReasonDefinition:
    return NgReasonDefinition(
        canonical_code=canonical_code,
        label=label,
        source=NgReasonSource.PLUGIN,
        plugin_key=ROUGH_SORTER_PLUGIN_KEY,
        contract_version=ROUGH_SORTER_CONTRACT_VERSION,
        maps_from=(canonical_code,),
    )


class RoughSorterPlugin(WorklinePlugin):
    """粗分机插件。"""

    plugin_key = ROUGH_SORTER_PLUGIN_KEY
    contract_version = ROUGH_SORTER_CONTRACT_VERSION

    manifest = WorklinePluginManifest(
        plugin_key=ROUGH_SORTER_PLUGIN_KEY,
        contract_version=ROUGH_SORTER_CONTRACT_VERSION,
        required_device_roles=(
            DeviceRoleRequirement(role=ROLE_INPUT_ARM, min_count=1),
            DeviceRoleRequirement(role=ROLE_CONVEYOR, min_count=1),
            DeviceRoleRequirement(role=ROLE_OUTPUT_ARM, min_count=1),
        ),
        business_key_resolver=resolve_rough_sorter_business_key,
        result_classifier=classify_rough_sorter_result,
        context_model=RoughSorterContext,
        supported_events=frozenset({EVENT_SCAN_COMPLETED}),
        supported_commands=frozenset(ACTION_TARGET_ROLES),
        command_target_roles=ACTION_TARGET_ROLES,
        ng_reason_catalog=(
            _ng_reason(NG_REASON_BARCODE_INVALID, "条码无效"),
            _ng_reason(NG_REASON_BARCODE_INCOMPLETE, "条码不完整"),
            _ng_reason(NG_REASON_BARCODE_RULE_NG, "条码规则判定 NG"),
            _ng_reason(NG_REASON_MEASUREMENT_NG, "测量业务判定 NG"),
            _ng_reason(NG_REASON_WMS_REJECTED, "WMS 库存校验拒绝"),
        ),
    )

    @staticmethod
    def _scan_source_location(payload_json: dict[str, Any]) -> str:
        device_code = payload_json.get("device_code")
        return device_code if isinstance(device_code, str) and device_code else "UNKNOWN"

    @staticmethod
    def _ng_location(ctx: PluginContext) -> str:
        config = ctx.config
        ng_location = config.get("ng_location")
        if isinstance(ng_location, str) and ng_location:
            return ng_location
        return DEFAULT_NG_LOCATION

    @on_event(EVENT_SCAN_COMPLETED)
    async def handle_scan_completed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理粗分机扫码入口事件。"""

        payload_json = inbox.payload_json or {}
        six_in_one = normalize_six_in_one_payload(payload_json)
        decision = barcode_decision_service.evaluate(six_in_one)
        six_in_one_payload = {
            field_name: value
            for field_name, value in six_in_one.model_dump().items()
            if field_name in six_in_one.BUSINESS_FIELD_NAMES and value is not None
        }

        if decision.decision == BarcodeDecisionType.OK:
            context_patch = RoughSorterContext(
                six_in_one=six_in_one_payload,
                business_key=decision.business_key,
                phase=PHASE_MEASURING,
            ).model_dump(mode="json", exclude_none=True)
            return [
                RuntimeIntent.update_context(context_patch),
                RuntimeIntent.command(
                    device_role=ACTION_TARGET_ROLES[ACTION_MEASUREMENT_REEL],
                    action=ACTION_MEASUREMENT_REEL,
                    payload=build_measurement_reel_payload(six_in_one, trace_id=ctx.trace_id or None),
                ),
            ]

        reason_code = decision.reason_code or "BARCODE_INVALID"
        reason_message = decision.reason_message or "扫码业务判定 NG"
        context_patch = RoughSorterContext(
            six_in_one=six_in_one_payload,
            business_key=decision.business_key,
            ng_reason={
                "reason_code": reason_code,
                "reason_message": reason_message,
            },
            phase=PHASE_NG_MOVING,
        ).model_dump(mode="json", exclude_none=True)

        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.mark_ng(
                reason_code=reason_code,
                message=reason_message,
                payload={"six_in_one": six_in_one_payload},
            ),
            RuntimeIntent.command(
                device_role=ROLE_OUTPUT_ARM,
                action=ACTION_MOVE_TO_NG,
                payload=build_move_to_ng_payload(
                    business_key=decision.business_key,
                    source_location=self._scan_source_location(payload_json),
                    ng_location=self._ng_location(ctx),
                    reason_code=reason_code,
                ),
            ),
        ]

    @on_command(ACTION_MEASUREMENT_REEL, result="SUCCESS")
    async def handle_measurement_success(self, _ctx: PluginContext, _inbox: WorklineInbox) -> list[RuntimeIntent]:
        """Task 3 才处理测量结果；Task 2 只验证业务 action 路由。"""

        return self._measurement_handler_pending_block()

    @on_command(ACTION_MEASUREMENT_REEL, result="FAILED")
    async def handle_measurement_failed(self, _ctx: PluginContext, _inbox: WorklineInbox) -> list[RuntimeIntent]:
        """Task 3 才处理测量失败分类；Task 2 必须避免回调静默丢弃。"""

        return self._measurement_handler_pending_block()

    @staticmethod
    def _measurement_handler_pending_block() -> list[RuntimeIntent]:
        return [
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code="ROUGH_SORTER_MEASUREMENT_HANDLER_NOT_IMPLEMENTED",
                message="粗分机测量结果处理将在 Task 3 实现",
                suggested_action="完成 rough_sorter Task 3 后再启用测量结果流程",
            ),
        ]


rough_sorter_plugin = RoughSorterPlugin()

__all__ = ["RoughSorterPlugin", "rough_sorter_plugin"]
