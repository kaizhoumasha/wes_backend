"""
SMT 粗分机工作线事件处理器

处理 SMT 粗分机工作线的各类事件和命令结果，实现业务流程编排。

事件类型：
- SCAN_COMPLETED: 扫码完成事件
- ESTOP_PRESSED: 急停按下事件
- COMMAND_RESULT: 设备命令执行结果

命令类型：
- PICK_AND_PUT: 搬运命令（抓取并放置）
- MOVE_FORWARD: 流水线前进命令

参考文档: docs/hardware/SMT粗分机接口调用说明书20260321-v1.md
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from src.workline_plugins.smt_classifier.contract import (
    RESULT_FIELD_ALIASES,
    STEP_CODE_KEY,
    SmtClassifierCommandType,
    SmtClassifierEventType,
    SmtClassifierResultType,
    SmtClassifierStepCode,
    infer_step_from_command,
    resolve_first_str,
    resolve_step_from_context,
)
from src.workline_runtime.enums import DecisionType, FailureDomain
from src.workline_runtime.types import (
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

logger = logging.getLogger(__name__)
JsonDict = dict[str, Any]


# ==================== 枚举定义 ====================


EventType = SmtClassifierEventType
TaskType = SmtClassifierCommandType
CommandResult = SmtClassifierResultType


class LocationType(str, Enum):
    """位置类型"""

    INPUT_PLATFORM = "INPUT_PLATFORM"
    NG_PLATFORM = "NG_PLATFORM"
    PIPELINE_PLATFORM = "PIPELINE_PLATFORM"
    OUTPUT_PLATFORM = "OUTPUT_PLATFORM"
    BIN = "BIN"


# ==================== 数据模型 ====================


class LocationInfo(BaseModel):
    """位置信息"""

    location_id: str = Field(..., description="位置 ID")
    location_type: LocationType = Field(..., description="位置类型")
    rack_id: str | None = Field(default=None, description="料架编号")
    bin_id: str | None = Field(default=None, description="箱体编号")
    bin_type: str | None = Field(default=None, description="料箱类型")
    bin_cell_location: str | None = Field(default=None, description="料箱格子位置")
    reel_layer: str | None = Field(default=None, description="料盘层数")
    reel_thickness: str | None = Field(default=None, description="料盘厚度 (mm)")
    reel_diameter: str | None = Field(default=None, description="料盘直径")


class ScanEventData(BaseModel):
    """扫码事件数据"""

    location: str = Field(..., description="扫码位置")
    barcode1: str | None = Field(default=None, description="条码 1")
    barcode2: str | None = Field(default=None, description="条码 2")
    barcode3: str | None = Field(default=None, description="条码 3")
    barcode4: str | None = Field(default=None, description="条码 4")
    barcode5: str | None = Field(default=None, description="条码 5")
    barcode6: str | None = Field(default=None, description="条码 6")

    def get_barcodes(self) -> list[str]:
        """获取所有非空条码"""
        barcodes: list[str] = []
        for i in range(1, 7):
            barcode = getattr(self, f"barcode{i}", None)
            if barcode:
                barcodes.append(barcode)
        return barcodes

    def get_primary_barcode(self) -> str | None:
        """获取主条码（第一个非空条码）"""
        barcodes = self.get_barcodes()
        return barcodes[0] if barcodes else None


class CommandResultData(BaseModel):
    """命令结果数据"""

    actual_qty: int = Field(default=1, description="实际搬运数量")
    location: str | None = Field(default=None, description="实际搬运位置")
    barcode1: str | None = Field(default=None, description="条码 1")
    barcode2: str | None = Field(default=None, description="条码 2")
    barcode3: str | None = Field(default=None, description="条码 3")
    barcode4: str | None = Field(default=None, description="条码 4")
    barcode5: str | None = Field(default=None, description="条码 5")
    barcode6: str | None = Field(default=None, description="条码 6")
    reel_diameter: str | None = Field(default=None, description="料盘直径")
    reel_thickness: str | None = Field(default=None, description="料盘厚度 (mm)")
    pick_and_put_result: str | None = Field(default=None, description="搬运结果")


class ErrorDetail(BaseModel):
    """错误详情"""

    error_code: str = Field(..., description="错误码")
    error_message: str | None = Field(default=None, description="错误信息")


def _ensure_dict(value: Any) -> JsonDict:
    """将动态输入收敛为字典，避免 Unknown 类型扩散。"""
    return cast("JsonDict", value) if isinstance(value, dict) else {}


# ==================== 命令生成函数 ====================


def generate_pick_and_put_command(
    device_id: int,
    source: str | None = None,
    target: str | None = None,
    source_type: LocationType = LocationType.INPUT_PLATFORM,
    target_type: LocationType = LocationType.OUTPUT_PLATFORM,
    target_info: dict[str, Any] | None = None,
    command_id: str | None = None,
    priority: int = 1,
    timeout: int = 30000,
) -> CommandIntent:
    """生成抓取放置命令

    Args:
        device_id: 设备 ID
        source_type: 源位置类型（逻辑类型）
        target_type: 目标位置类型（逻辑类型）
        target_info: 目标位置附加信息（料箱信息等）
        command_id: 命令 ID（可选）
        priority: 优先级 (1-10)
        timeout: 超时时间（毫秒）

    Returns:
        CommandIntent: 设备命令意图
    """
    import time

    params: dict[str, Any] = {
        "source": {
            "location_type": source_type.value,
        },
        "target": {
            "location_type": target_type.value,
        },
    }
    if source:
        params["source"]["location_id"] = source
    if target:
        params["target"]["location_id"] = target

    # 合并目标位置附加信息
    if target_info:
        params["target"].update(target_info)

    return CommandIntent(
        target_device_id=device_id,
        action=TaskType.PICK_AND_PUT.value,
        parameters={
            "command_id": command_id or f"CMD-{int(time.time() * 1000)}",
            "task_type": TaskType.PICK_AND_PUT.value,
            "priority": priority,
            "timeout": timeout,
            "params": params,
        },
    )


def generate_move_forward_command(
    device_id: int,
    source: str | None = None,
    target: str | None = None,
    command_id: str | None = None,
    priority: int = 1,
    timeout: int = 30000,
) -> CommandIntent:
    """生成流水线前进命令

    Args:
        device_id: 设备 ID
        command_id: 命令 ID（可选）
        priority: 优先级 (1-10)
        timeout: 超时时间（毫秒）

    Returns:
        CommandIntent: 设备命令意图
    """
    import time

    location_payload: JsonDict = {}
    if source:
        location_payload["source"] = {"location_id": source}
    if target:
        location_payload["target"] = {"location_id": target}

    return CommandIntent(
        target_device_id=device_id,
        action=TaskType.MOVE_FORWARD.value,
        parameters={
            "command_id": command_id or f"CMD-{int(time.time() * 1000)}",
            "task_type": TaskType.MOVE_FORWARD.value,
            "priority": priority,
            "timeout": timeout,
            **location_payload,
            "params": {
                "source": {
                    "location_type": LocationType.PIPELINE_PLATFORM.value,
                    **({"location_id": source} if source else {}),
                },
                "target": {
                    "location_type": LocationType.PIPELINE_PLATFORM.value,
                    **({"location_id": target} if target else {}),
                },
            },
        },
    )


def _is_pipeline_input_location(location_id: str) -> bool:
    return "PIPELINE" in location_id and "INPUT" in location_id


def _is_pipeline_output_location(location_id: str) -> bool:
    return "PIPELINE" in location_id and "OUTPUT" in location_id


def _is_terminal_location(location_id: str) -> bool:
    return ("OUTPUT" in location_id and "PIPELINE" not in location_id) or "NG_PLATFORM" in location_id


# ==================== 条码验证逻辑 ====================


def validate_barcode(barcode: str) -> tuple[bool, str]:
    """验证条码有效性

    Args:
        barcode: 条码字符串

    Returns:
        tuple[bool, str]: (是否有效, 原因)
    """
    if not barcode:
        return False, "条码为空"

    # 基本格式验证
    if len(barcode) < 3:
        return False, "条码长度不足"

    if len(barcode) > 100:
        return False, "条码长度超限"

    # 这里可以添加更多业务验证逻辑
    # 例如：检查前缀、校验位等

    return True, "OK"


def determine_scan_result(barcodes: list[str]) -> tuple[bool, str]:
    """判断扫码结果 OK/NG

    根据条码列表判断是否合格。

    Args:
        barcodes: 条码列表

    Returns:
        tuple[bool, str]: (是否OK, 原因)
    """
    if not barcodes:
        return False, "无有效条码"

    # 验证所有条码
    for barcode in barcodes:
        is_valid, reason = validate_barcode(barcode)
        if not is_valid:
            return False, f"条码 {barcode} 验证失败: {reason}"

    # 所有条码验证通过
    return True, "OK"


# ==================== 事件处理器 ====================


class SmtClassifierEventHandler:
    """SMT 粗分机事件处理器

    处理 SMT 粗分机工作线的各类事件：
    - 扫码完成事件
    - 急停事件
    - 命令执行结果

    状态机流程：
    NEW/RUNNING -> SCAN_COMPLETED -> PICK_AND_PUT (INPUT->PIPELINE_INPUT/NG_PLATFORM)
    -> COMMAND_RESULT -> MOVE_FORWARD -> COMMAND_RESULT -> PICK_AND_PUT -> COMPLETED
    """

    plugin_key = "smt_classifier"

    async def on_device_event(
        self,
        ctx: PluginContext,
        inbox: WorklineInbox,
    ) -> PluginResult:
        """处理设备事件

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult: 处理结果
        """
        payload = _ensure_dict(inbox.payload_json)
        event_type = resolve_first_str(payload, ("event_type",), default="")

        ctx.logger.info(f"SmtClassifier received device event: {event_type}")

        if event_type == EventType.SCAN_COMPLETED.value:
            return await self._handle_scan_completed(ctx, payload)
        if event_type == EventType.ESTOP_PRESSED.value:
            return await self._handle_estop_pressed(ctx, payload)

        # 未知事件类型
        ctx.logger.warning(f"Unknown event type: {event_type}")
        return PluginResult()

    async def on_command_result(
        self,
        ctx: PluginContext,
        inbox: WorklineInbox,
    ) -> PluginResult:
        """处理命令结果

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult: 处理结果
        """
        payload = _ensure_dict(inbox.payload_json)
        result = resolve_first_str(payload, RESULT_FIELD_ALIASES["result"]["aliases"], default="")
        device_id = str(payload.get("device_id", ""))

        ctx.logger.info(f"SmtClassifier received command result: device={device_id}, result={result}")

        if result == CommandResult.SUCCESS.value:
            return await self._handle_command_success(ctx, payload)
        if result == CommandResult.FAILED.value:
            return await self._handle_command_failed(ctx, payload)

        return PluginResult()

    async def on_timeout(
        self,
        ctx: PluginContext,
        inbox: WorklineInbox,
    ) -> PluginResult:
        """处理超时事件

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult: 处理结果
        """
        ctx.logger.warning(f"SmtClassifier received timeout: {inbox.id}")

        return PluginResult(
            context_patch={STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value},
            failure=FailureIntent(
                domain=FailureDomain.TIMEOUT.value,
                code="DEVICE_TIMEOUT",
                message="设备响应超时",
            ),
        )

    async def on_external_http(
        self,
        ctx: PluginContext,
        inbox: WorklineInbox,
    ) -> PluginResult:
        """处理外部 HTTP 回调

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult: 处理结果
        """
        ctx.logger.info(f"SmtClassifier received external HTTP: {inbox.id}")
        return PluginResult()

    async def on_manual_operation(
        self,
        ctx: PluginContext,
        inbox: WorklineInbox,
    ) -> PluginResult:
        """处理人工操作

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult: 处理结果
        """
        ctx.logger.info(f"SmtClassifier received manual operation: {inbox.id}")
        return PluginResult()

    # ==================== 内部方法 ====================

    async def _handle_scan_completed(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
    ) -> PluginResult:
        """处理扫码完成事件

        根据扫码结果决定下一步：
        - OK: 生成 PICK_AND_PUT 命令 (INPUT -> PIPELINE_INPUT)
        - NG: 生成 PICK_AND_PUT 命令 (INPUT -> NG_PLATFORM)

        Args:
            ctx: 插件上下文
            payload: 事件数据

        Returns:
            PluginResult: 处理结果
        """
        data = _ensure_dict(payload.get("data"))
        device_id = str(payload.get("device_id", ""))

        # 解析扫码数据
        scan_data = ScanEventData(**data) if data else ScanEventData(location="")
        barcodes = scan_data.get_barcodes()
        primary_barcode = scan_data.get_primary_barcode()

        ctx.logger.info(f"Scan completed: barcodes={barcodes}, device={device_id}")

        # 判断扫码结果
        is_ok, reason = determine_scan_result(barcodes)

        # 记录决策
        decision: JsonDict = {
            "type": DecisionType.BARCODE_VALIDATION.value,
            "input": {"barcodes": barcodes, "device_id": device_id},
            "result": "OK" if is_ok else "NG",
            "reason": reason,
        }

        # 更新上下文
        context_patch: JsonDict = {
            "barcode": primary_barcode,
            "scan_result": "OK" if is_ok else "NG",
            "barcodes": barcodes,
            "scan_location": scan_data.location,
            "current_location": scan_data.location,
            "source_type": LocationType.INPUT_PLATFORM.value,
            "target_type": LocationType.PIPELINE_PLATFORM.value if is_ok else LocationType.NG_PLATFORM.value,
            "retry_count": 0,
            STEP_CODE_KEY: (
                SmtClassifierStepCode.INPUT_PICK_PLACE.value if is_ok else SmtClassifierStepCode.NG_PICK_PLACE.value
            ),
        }

        if is_ok:
            # OK: INPUT -> PIPELINE_INPUT
            ctx.logger.info(f"Scan OK: {primary_barcode}, routing to pipeline input")

            # 获取进料机械臂设备
            arm_device = ctx.get_device_by_role("INPUT_ARM")
            if not arm_device:
                return PluginResult(
                    failure=FailureIntent(
                        domain=FailureDomain.CONFIG.value,
                        code="DEVICE_NOT_FOUND",
                        message="未找到进料机械臂设备",
                    )
                )

            command = generate_pick_and_put_command(
                device_id=getattr(arm_device, "id", 0),
                source_type=LocationType.INPUT_PLATFORM,
                target_type=LocationType.PIPELINE_PLATFORM,
            )

            return PluginResult(
                transition="scan_ok",
                context_patch=context_patch,
                decisions=[decision],
                commands=[command],
                wait=WaitIntent(
                    wait_type="COMMAND_RESULT",
                    wait_token=f"pick_and_put_{primary_barcode}",
                    deadline_seconds=60,
                ),
            )
        # NG: INPUT -> NG_PLATFORM
        ctx.logger.warning(f"Scan NG: {primary_barcode}, reason={reason}")

        # 获取进料机械臂设备
        arm_device = ctx.get_device_by_role("INPUT_ARM")
        if not arm_device:
            return PluginResult(
                failure=FailureIntent(
                    domain=FailureDomain.CONFIG.value,
                    code="DEVICE_NOT_FOUND",
                    message="未找到进料机械臂设备",
                )
            )

        command = generate_pick_and_put_command(
            device_id=getattr(arm_device, "id", 0),
            source_type=LocationType.INPUT_PLATFORM,
            target_type=LocationType.NG_PLATFORM,
        )

        return PluginResult(
            transition="scan_ng",
            context_patch=context_patch,
            decisions=[decision],
            commands=[command],
            failure=FailureIntent(
                domain=FailureDomain.DATA.value,
                code="BARCODE_NG",
                message=f"扫码不合格: {reason}",
            ),
        )

    async def _handle_estop_pressed(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
    ) -> PluginResult:
        """处理急停按下事件

        进入 MANUAL_HOLD 状态，等待人工介入。

        Args:
            ctx: 插件上下文
            payload: 事件数据

        Returns:
            PluginResult: 处理结果
        """
        device_id = str(payload.get("device_id", ""))
        timestamp_raw = payload.get("timestamp", 0)
        timestamp = timestamp_raw if isinstance(timestamp_raw, int) else 0

        ctx.logger.warning(f"E-STOP pressed: device={device_id}, timestamp={timestamp}")

        return PluginResult(
            transition="estop",
            context_patch={
                "estop_device": device_id,
                "estop_timestamp": timestamp,
                "estop_handled": False,
                STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
            },
        )

    async def _handle_command_success(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
    ) -> PluginResult:
        """处理命令执行成功

        根据当前状态和命令类型决定下一步操作。

        Args:
            ctx: 插件上下文
            payload: 命令结果数据

        Returns:
            PluginResult: 处理结果
        """
        data = _ensure_dict(payload.get("data"))
        command_type = resolve_first_str(payload, RESULT_FIELD_ALIASES["command_type"]["aliases"])
        device_id = str(payload.get("device_id", ""))
        session = ctx.session
        context = _ensure_dict(getattr(session, "context_json", None)) if session else {}

        result_data = CommandResultData(**data) if data else CommandResultData(location="")
        context_patch: JsonDict = {}
        if result_data.reel_diameter:
            context_patch["reel_diameter"] = result_data.reel_diameter
        if result_data.reel_thickness:
            context_patch["thickness"] = result_data.reel_thickness
        if result_data.location:
            context_patch["current_location"] = result_data.location

        current_location = result_data.location or str(context.get("current_location", ""))
        current_step = resolve_step_from_context(context) or infer_step_from_command(command_type, context)

        # 向后兼容：无 step_code 的旧记录继续允许位置字符串兜底推断
        if current_step is None:
            if _is_pipeline_input_location(current_location):
                current_step = SmtClassifierStepCode.INPUT_PICK_PLACE
            elif _is_pipeline_output_location(current_location):
                current_step = SmtClassifierStepCode.PIPELINE_MOVE_FORWARD
            elif _is_terminal_location(current_location):
                target_type = str(context.get("target_type", ""))
                if target_type == LocationType.NG_PLATFORM.value:
                    current_step = SmtClassifierStepCode.NG_PICK_PLACE
                else:
                    current_step = SmtClassifierStepCode.OUTPUT_PICK_PLACE

        ctx.logger.info(
            "Command success: device=%s, command_type=%s, step=%s, location=%s",
            device_id,
            command_type,
            current_step.value if current_step is not None else "",
            current_location,
        )

        if current_step == SmtClassifierStepCode.INPUT_PICK_PLACE:
            pipeline_device = ctx.get_device_by_role("PIPELINE")
            if not pipeline_device:
                return PluginResult(
                    failure=FailureIntent(
                        domain=FailureDomain.CONFIG.value,
                        code="DEVICE_NOT_FOUND",
                        message="未找到流水线设备",
                    )
                )

            command = generate_move_forward_command(device_id=getattr(pipeline_device, "id", 0))
            return PluginResult(
                transition="move_ok",
                context_patch={
                    **context_patch,
                    "source_type": LocationType.PIPELINE_PLATFORM.value,
                    "target_type": LocationType.PIPELINE_PLATFORM.value,
                    STEP_CODE_KEY: SmtClassifierStepCode.PIPELINE_MOVE_FORWARD.value,
                },
                commands=[command],
                wait=WaitIntent(
                    wait_type="COMMAND_RESULT",
                    wait_token=f"move_forward_{context.get('barcode', '')!s}",
                    deadline_seconds=60,
                ),
            )

        if current_step == SmtClassifierStepCode.PIPELINE_MOVE_FORWARD:
            output_arm = ctx.get_device_by_role("OUTPUT_ARM")
            if not output_arm:
                return PluginResult(
                    failure=FailureIntent(
                        domain=FailureDomain.CONFIG.value,
                        code="DEVICE_NOT_FOUND",
                        message="未找到出料机械臂设备",
                    )
                )

            target_bin = _ensure_dict(context.get("target_bin")) or {
                "rack_id": "RACK_001",
                "bin_id": "BIN_001",
                "bin_cell": "A1",
            }
            command = generate_pick_and_put_command(
                device_id=getattr(output_arm, "id", 0),
                source_type=LocationType.PIPELINE_PLATFORM,
                target_type=LocationType.BIN,
                target_info={
                    "rack_id": target_bin.get("rack_id", "RACK_001"),
                    "bin_id": target_bin.get("bin_id", "BIN_001"),
                    "bin_cell": target_bin.get("bin_cell", "A1"),
                    "bin_type": "三格箱",
                    "bin_cell_location": target_bin.get("bin_cell", "A1"),
                    "reel_layer": "1",
                    "reel_thickness": str(context.get("thickness", "20")),
                    "reel_diameter": str(context.get("reel_diameter", "15inch")),
                },
            )
            return PluginResult(
                transition="put_ok",
                context_patch={
                    **context_patch,
                    "target_type": LocationType.BIN.value,
                    "target_bin": {
                        "rack_id": target_bin.get("rack_id", "RACK_001"),
                        "bin_id": target_bin.get("bin_id", "BIN_001"),
                        "bin_cell": target_bin.get("bin_cell", "A1"),
                    },
                    STEP_CODE_KEY: SmtClassifierStepCode.OUTPUT_PICK_PLACE.value,
                },
                commands=[command],
                wait=WaitIntent(
                    wait_type="COMMAND_RESULT",
                    wait_token=f"pick_and_put_output_{context.get('barcode', '')!s}_{target_bin.get('bin_cell', 'A1')}",
                    deadline_seconds=60,
                ),
            )

        if current_step in {SmtClassifierStepCode.OUTPUT_PICK_PLACE, SmtClassifierStepCode.NG_PICK_PLACE}:
            target_type = str(context.get("target_type", ""))
            final_context_patch: JsonDict = {
                **context_patch,
                "target_type": target_type,
                "target_bin_id": (
                    context.get("target_bin", {}).get("bin_id") if isinstance(context.get("target_bin"), dict) else None
                ),
                "target_bin_cell": (
                    context.get("target_bin", {}).get("bin_cell")
                    if isinstance(context.get("target_bin"), dict)
                    else None
                ),
                "completed_at": ctx.clock().isoformat(),
                STEP_CODE_KEY: SmtClassifierStepCode.COMPLETED.value,
            }
            if current_step == SmtClassifierStepCode.NG_PICK_PLACE:
                final_context_patch["ng_handled"] = True
            return PluginResult(
                transition="complete",
                context_patch=final_context_patch,
                complete=True,
            )

        return PluginResult(context_patch=context_patch)

    async def _handle_command_failed(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
    ) -> PluginResult:
        """处理命令执行失败

        根据错误码进行错误归因，决定重试或失败。

        Args:
            ctx: 插件上下文
            payload: 命令结果数据

        Returns:
            PluginResult: 处理结果
        """
        device_id = str(payload.get("device_id", ""))
        session = ctx.session
        context = _ensure_dict(getattr(session, "context_json", None)) if session else {}

        error_code = resolve_first_str(
            payload,
            RESULT_FIELD_ALIASES["error_code"]["aliases"],
            default="9999",
        )
        error_message = resolve_first_str(
            payload,
            RESULT_FIELD_ALIASES["error_message"]["aliases"],
            default="未知错误",
        )
        current_step = resolve_step_from_context(context)

        ctx.logger.error(f"Command failed: device={device_id}, error_code={error_code}, message={error_message}")

        # 根据错误码判断错误域
        failure_domain = self._determine_failure_domain(error_code)

        # 检查重试次数
        retry_value = context.get("retry_count", 0)
        retry_count = retry_value if isinstance(retry_value, int) else 0
        max_retries = 3

        if retry_count < max_retries and self._is_retryable_error(error_code):
            # 可重试错误
            ctx.logger.info(f"Retrying command: retry_count={retry_count + 1}")

            return PluginResult(
                transition="retry",
                context_patch={
                    "retry_count": retry_count + 1,
                    **({STEP_CODE_KEY: current_step.value} if current_step is not None else {}),
                },
                # 这里应该重新生成命令，但需要更多信息
            )

        # 不可重试或超过最大重试次数
        return PluginResult(
            transition="failed",
            context_patch={STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value},
            failure=FailureIntent(
                domain=failure_domain,
                code=error_code,
                message=error_message,
            ),
        )

    def _determine_failure_domain(self, error_code: str) -> str:
        """根据错误码判断错误域

        Args:
            error_code: 错误码

        Returns:
            str: 错误域
        """
        # 设备故障错误码
        if error_code in ("1001", "1002", "2001", "2002"):
            return FailureDomain.HARDWARE.value

        # 算法/配置错误码
        if error_code == "2003":
            return FailureDomain.ALGORITHM.value

        # 其他错误
        return FailureDomain.SOFTWARE.value

    def _is_retryable_error(self, error_code: str) -> bool:
        """判断错误是否可重试

        Args:
            error_code: 错误码

        Returns:
            bool: 是否可重试
        """
        # 设备忙、网络错误等可重试
        retryable_codes = {"503", "1001", "1002", "2001", "2002"}
        return error_code in retryable_codes


# 单例导出
smt_classifier_event_handler = SmtClassifierEventHandler()

__all__ = [
    "CommandResult",
    "CommandResultData",
    "ErrorDetail",
    "EventType",
    "LocationInfo",
    "LocationType",
    "SmtClassifierEventHandler",
    "TaskType",
    "generate_move_forward_command",
    "generate_pick_and_put_command",
    "smt_classifier_event_handler",
]
