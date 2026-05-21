"""入库料箱称重复核插件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.workline_runtime.plugin_base import (
    WorklinePlugin,
    build_payload_invalid_block,
    on_command,
    on_event,
    payload_invalid_block_if_missing_envelope,
    resolve_normalized_command_failure,
)
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult  # noqa: TC001
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

from .context import InboundToteQcContext, parse_inbound_tote_qc_context
from .contract import (
    ToteArrivedPayload,
    WeighCompletedData,
    build_divert_tote_params,
    build_weigh_tote_params,
    classify_inbound_tote_result,
    resolve_tote_business_key,
)

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
        context_model=InboundToteQcContext,
        event_source_roles={"TOTE_ARRIVED": "ENTRY_SCANNER"},
        command_target_roles={"WEIGH_TOTE": "WEIGH_SCALE", "DIVERT_TOTE": "DIVERT_CONVEYOR"},
        supported_events=frozenset({"TOTE_ARRIVED"}),
        supported_commands=frozenset({"WEIGH_TOTE", "DIVERT_TOTE"}),
    )

    @on_event("TOTE_ARRIVED")
    async def handle_tote_arrived(self, ctx: PluginContext, inbox: Any) -> RuntimeIntent | list[RuntimeIntent]:
        """料箱到位后下发称重命令。"""

        payload: Any = getattr(inbox, "payload_json", None) or {}
        try:
            event = ToteArrivedPayload.model_validate(payload)
        except Exception:
            return build_payload_invalid_block("TOTE_ARRIVED data 非法")

        if event.data is None:
            return build_payload_invalid_block("TOTE_ARRIVED 缺少 data 字段")

        return [
            ctx.next.update_context(
                InboundToteQcContext(
                    tote_id=event.data.tote_id,
                    station_code=event.data.station_code,
                    expected_weight_kg=event.data.expected_weight_kg,
                    tolerance_kg=event.data.tolerance_kg,
                ).to_patch()
            ),
            ctx.next.command(
                device_role="WEIGH_SCALE",
                action="WEIGH_TOTE",
                payload=build_weigh_tote_params(
                    tote_id=event.data.tote_id,
                    station_code=event.data.station_code,
                ),
                destination_role="WEIGH_SCALE",
                timeout_seconds=120,
            ),
        ]

    @on_command("WEIGH_TOTE", result="SUCCESS")
    async def handle_weigh_success(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """称重成功后按重量判定放行或分流。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "WEIGH_TOTE 成功回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid

        try:
            weigh_data = WeighCompletedData.model_validate(result.data)
        except Exception:
            return build_payload_invalid_block("WEIGH_TOTE 成功回调 data 非法")

        tote_ctx = parse_inbound_tote_qc_context(ctx)
        if tote_ctx.expected_weight_kg is None or tote_ctx.tolerance_kg is None:
            return build_payload_invalid_block("缺少料箱称重上下文")

        is_ok = _is_weight_in_tolerance(
            expected=tote_ctx.expected_weight_kg,
            actual=weigh_data.actual_weight_kg,
            tolerance=tote_ctx.tolerance_kg,
        )
        destination_lane = "PASS_LANE" if is_ok else "HOLD_LANE"
        reason_code = "WEIGHT_OK" if is_ok else "WEIGHT_OUT_OF_TOLERANCE"

        intents: list[RuntimeIntent] = []
        if not is_ok:
            intents.append(
                ctx.next.mark_ng(
                    reason_code=reason_code,
                    message="料箱重量超出允差，分流到异常线",
                    payload={
                        "expected_weight_kg": tote_ctx.expected_weight_kg,
                        "actual_weight_kg": weigh_data.actual_weight_kg,
                        "tolerance_kg": tote_ctx.tolerance_kg,
                        "tote_id": weigh_data.tote_id,
                    },
                )
            )

        intents.extend(
            [
                ctx.next.update_context(
                    InboundToteQcContext(
                        tote_id=weigh_data.tote_id,
                        actual_weight_kg=weigh_data.actual_weight_kg,
                        destination_lane=destination_lane,
                        reason_code=reason_code,
                    ).to_patch()
                ),
                ctx.next.command(
                    device_role="DIVERT_CONVEYOR",
                    action="DIVERT_TOTE",
                    payload=build_divert_tote_params(
                        tote_id=weigh_data.tote_id,
                        destination_lane=destination_lane,
                        reason_code=reason_code,
                    ),
                    destination_role="DIVERT_CONVEYOR",
                    timeout_seconds=120,
                ),
            ]
        )
        return intents

    @on_command("WEIGH_TOTE", result="FAILED")
    async def handle_weigh_failed(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """称重设备失败阻塞当前命令。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "WEIGH_TOTE 失败回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid
        error_code, error_message = resolve_normalized_command_failure(
            result,
            default_code="WEIGH_SCALE_FAILED",
            default_message="称重设备执行失败",
        )
        return ctx.next.block(
            scope=BlockScope.COMMAND,
            reason_code=error_code,
            message=error_message,
        )

    @on_command("DIVERT_TOTE", result="SUCCESS")
    async def handle_divert_success(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """分流成功后完成 Session。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "DIVERT_TOTE 成功回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid
        return ctx.next.complete()

    @on_command("DIVERT_TOTE", result="FAILED")
    async def handle_divert_failed(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """分流设备失败后阻塞当前料箱。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "DIVERT_TOTE 失败回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid
        error_code, error_message = resolve_normalized_command_failure(
            result,
            default_code="DIVERT_FAILED",
            default_message="料箱分流失败",
        )
        return ctx.next.block(
            scope=BlockScope.MATERIAL,
            reason_code=error_code,
            message=error_message,
        )


inbound_tote_qc_plugin = InboundToteQcPlugin()


__all__ = ["InboundToteQcPlugin", "inbound_tote_qc_plugin"]
