"""入库料箱称重复核插件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    build_payload_invalid_failure,
    on_command,
    on_event,
    resolve_normalized_command_envelope,
    resolve_normalized_command_failure,
    step,
)
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult  # noqa: TC001
from src.workline_runtime.types import CommandTargetScope

from .context import InboundToteQcContext, parse_inbound_tote_qc_context
from .contract import (
    ToteArrivedPayload,
    WeighCompletedData,
    build_divert_tote_params,
    build_weigh_tote_params,
    classify_inbound_tote_result,
    resolve_tote_business_key,
)
from .state_machine import InboundToteQcState, InboundToteQcStateMachine

if TYPE_CHECKING:
    from src.workline_runtime.plugin_context import PluginContext


def _is_weight_in_tolerance(*, expected: float, actual: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


class InboundToteQcPlugin(WorklinePlugin):
    """入库料箱称重复核插件 spike。"""

    plugin_key = "inbound_tote_qc"
    contract_version = "spike-2026.04"
    manifest = WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version=contract_version,
        required_device_roles=(
            DeviceRoleRequirement("ENTRY_SCANNER", min_count=1, max_count=1, capabilities=frozenset({"scan_tote"})),
            DeviceRoleRequirement("WEIGH_SCALE", min_count=1, max_count=1, capabilities=frozenset({"measure_weight"})),
            DeviceRoleRequirement(
                "DIVERT_CONVEYOR",
                min_count=1,
                max_count=1,
                capabilities=frozenset({"divert_lane"}),
            ),
        ),
        business_key_resolver=resolve_tote_business_key,
        result_classifier=classify_inbound_tote_result,
        state_machine_class=InboundToteQcStateMachine,
        context_model=InboundToteQcContext,
        event_source_roles={"TOTE_ARRIVED": "ENTRY_SCANNER"},
        command_target_roles={"WEIGH_TOTE": "WEIGH_SCALE", "DIVERT_TOTE": "DIVERT_CONVEYOR"},
        supported_events=frozenset({"TOTE_ARRIVED"}),
        supported_commands=frozenset({"WEIGH_TOTE", "DIVERT_TOTE"}),
    )

    @on_event("TOTE_ARRIVED")
    async def handle_tote_arrived(self, ctx: PluginContext, event: ToteArrivedPayload):
        """料箱到位后下发称重命令。"""

        if event.data is None:
            return build_payload_invalid_failure(ctx, "TOTE_ARRIVED 缺少 data 字段")

        return (
            PluginResultBuilder(ctx)
            .transition("tote_arrived")
            .command(
                command_type="WEIGH_TOTE",
                target_scope=CommandTargetScope.DOWNSTREAM,
                device_role="WEIGH_SCALE",
                parameters=build_weigh_tote_params(
                    tote_id=event.data.tote_id,
                    station_code=event.data.station_code,
                ),
            )
            .wait(event_type="WEIGH_TOTE", timeout_seconds=120)
            .context(
                InboundToteQcContext(
                    plugin_state=InboundToteQcState.WAITING_WEIGH,
                    tote_id=event.data.tote_id,
                    station_code=event.data.station_code,
                    expected_weight_kg=event.data.expected_weight_kg,
                    tolerance_kg=event.data.tolerance_kg,
                ).to_patch()
            )
            .build()
        )

    @on_command("WEIGH_TOTE", result="SUCCESS")
    @step(InboundToteQcState.WAITING_WEIGH)
    async def handle_weigh_success(self, ctx: PluginContext, result: NormalizedCommandResult):
        """称重成功后按重量判定放行或分流。"""

        if resolve_normalized_command_envelope(result) is None:
            return build_payload_invalid_failure(ctx, "WEIGH_TOTE 成功回调缺少 command_code 或 device_code")

        try:
            weigh_data = WeighCompletedData.model_validate(result.data)
        except Exception:
            return build_payload_invalid_failure(ctx, "WEIGH_TOTE 成功回调 data 非法")

        tote_ctx = parse_inbound_tote_qc_context(ctx)
        if tote_ctx.expected_weight_kg is None or tote_ctx.tolerance_kg is None:
            return build_payload_invalid_failure(ctx, "缺少料箱称重上下文")

        is_ok = _is_weight_in_tolerance(
            expected=tote_ctx.expected_weight_kg,
            actual=weigh_data.actual_weight_kg,
            tolerance=tote_ctx.tolerance_kg,
        )
        destination_lane = "PASS_LANE" if is_ok else "HOLD_LANE"
        reason_code = "WEIGHT_OK" if is_ok else "WEIGHT_OUT_OF_TOLERANCE"
        builder = (
            PluginResultBuilder(ctx)
            .transition("weight_ok" if is_ok else "weight_ng")
            .command(
                command_type="DIVERT_TOTE",
                target_scope=CommandTargetScope.DOWNSTREAM,
                device_role="DIVERT_CONVEYOR",
                parameters=build_divert_tote_params(
                    tote_id=weigh_data.tote_id,
                    destination_lane=destination_lane,
                    reason_code=reason_code,
                ),
            )
            .wait(event_type="DIVERT_TOTE", timeout_seconds=120)
            .context(
                InboundToteQcContext(
                    plugin_state=InboundToteQcState.WAITING_DIVERT,
                    tote_id=weigh_data.tote_id,
                    actual_weight_kg=weigh_data.actual_weight_kg,
                    destination_lane=destination_lane,
                    reason_code=reason_code,
                ).to_patch()
            )
        )
        if not is_ok:
            builder.business_decision(
                reason_code=reason_code,
                message="料箱重量超出允差，分流到异常线",
                business_key=weigh_data.tote_id,
                evidence={
                    "expected_weight_kg": tote_ctx.expected_weight_kg,
                    "actual_weight_kg": weigh_data.actual_weight_kg,
                    "tolerance_kg": tote_ctx.tolerance_kg,
                },
            )
        return builder.build()

    @on_command("WEIGH_TOTE", result="FAILED")
    @step(InboundToteQcState.WAITING_WEIGH)
    async def handle_weigh_failed(self, ctx: PluginContext, result: NormalizedCommandResult):
        """称重设备失败属于系统/硬件异常。"""

        if resolve_normalized_command_envelope(result) is None:
            return build_payload_invalid_failure(ctx, "WEIGH_TOTE 失败回调缺少 command_code 或 device_code")
        error_code, error_message = resolve_normalized_command_failure(
            result,
            default_code="WEIGH_SCALE_FAILED",
            default_message="称重设备执行失败",
        )
        return (
            PluginResultBuilder(ctx)
            .transition("fail")
            .failure(domain="HARDWARE", code=error_code, message=error_message)
            .build()
        )

    @on_command("DIVERT_TOTE", result="SUCCESS")
    @step(InboundToteQcState.WAITING_DIVERT)
    async def handle_divert_success(self, ctx: PluginContext, result: NormalizedCommandResult):
        """分流成功后完成 Session。"""

        if resolve_normalized_command_envelope(result) is None:
            return build_payload_invalid_failure(ctx, "DIVERT_TOTE 成功回调缺少 command_code 或 device_code")
        return (
            PluginResultBuilder(ctx)
            .transition("divert_ok")
            .context(InboundToteQcContext(plugin_state=InboundToteQcState.COMPLETED).to_patch())
            .complete()
            .build()
        )

    @on_command("DIVERT_TOTE", result="FAILED")
    @step(InboundToteQcState.WAITING_DIVERT)
    async def handle_divert_failed(self, ctx: PluginContext, result: NormalizedCommandResult):
        """分流设备失败进入人工介入。"""

        error_code, error_message = resolve_normalized_command_failure(
            result,
            default_code="DIVERT_FAILED",
            default_message="料箱分流失败",
        )
        return (
            PluginResultBuilder(ctx)
            .transition("manual_hold")
            .context(
                InboundToteQcContext(
                    plugin_state=InboundToteQcState.MANUAL_HOLD,
                    reason_code=error_code,
                ).to_patch()
            )
            .failure(domain="HARDWARE", code=error_code, message=error_message)
            .build()
        )


inbound_tote_qc_plugin = InboundToteQcPlugin()


__all__ = ["InboundToteQcPlugin", "inbound_tote_qc_plugin"]
