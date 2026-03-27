"""
SMT 粗分机工作线插件

实现 SMT 产线的粗分作业流程：
- 扫码识别（OK/NG 判定）
- 机械臂抓取放置
- 流水线传输
- NG 分流

设备配置要求：
- INPUT_ARM: 进料机械臂，用于扫码后抓取和 NG 放置
- OUTPUT_ARM: 出料机械臂，用于最终出料
- CONVEYOR: 流水线，用于 OK 产品传输

设计参考: 设计文档 phase2-orchestrator
"""

from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from src.core.exceptions import BadRequestException

# 从 event_handlers 导入 LocationType
from src.workline_plugins.smt_classifier.event_handlers import LocationType
from src.workline_runtime.types import (
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


# ==================== 枚举定义 ====================


class SmtClassifierDeviceRole(str, Enum):
    """SMT 粗分机设备角色

    用于设备配置和运行时查找。
    设备需要在 Device.device_role 字段设置对应值。
    """

    INPUT_ARM = "INPUT_ARM"  # 进料机械臂 - 扫码后抓取、NG 放置
    OUTPUT_ARM = "OUTPUT_ARM"  # 出料机械臂 - 最终出料
    CONVEYOR = "CONVEYOR"  # 流水线 - OK 产品传输


class SmtClassifierCapabilities(str, Enum):
    """SMT 粗分机设备能力枚举

    定义设备支持的操作能力，用于设备能力声明和校验。
    """

    SCAN = "SCAN"  # 扫码
    SIZE_DETECT = "SIZE_DETECT"  # 尺寸检测
    THICKNESS_DETECT = "THICKNESS_DETECT"  # 测厚
    PICK = "PICK"  # 抓取
    PUT = "PUT"  # 放置
    MOVE_FORWARD = "MOVE_FORWARD"  # 前进
    MOVE_BACKWARD = "MOVE_BACKWARD"  # 后退


class SmtClassifierStage(str, Enum):
    """SMT 粗分机作业阶段

    用于 Session.context_json['stage'] 状态追踪。
    """

    IDLE = "IDLE"
    WAITING_SCAN = "WAITING_SCAN"
    SCAN_RESULT_RECEIVED = "SCAN_RESULT_RECEIVED"
    WAITING_INSPECTION = "WAITING_INSPECTION"
    INSPECTION_RESULT_RECEIVED = "INSPECTION_RESULT_RECEIVED"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    WAITING_CONVEYOR = "WAITING_CONVEYOR"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class SmtClassifierEventType(str, Enum):
    """SMT 粗分机事件类型

    用于 DeviceEventLog.payload_json['event_type']。
    """

    SCAN_COMPLETED = "SCAN_COMPLETED"
    ESTOP_PRESSED = "ESTOP_PRESSED"
    INSPECTION_COMPLETED = "INSPECTION_COMPLETED"


class SmtClassifierCommandType(str, Enum):
    """SMT 粗分机命令类型

    用于 DeviceCommand.action 字段。
    """

    PICK_AND_PUT = "PICK_AND_PUT"  # 抓取并放置
    MOVE_FORWARD = "MOVE_FORWARD"  # 流水线前进


# ==================== 设备需求声明 ====================


@dataclass
class DeviceRoleRequirement:
    """设备角色需求

    声明插件对设备角色的需求，用于：
    1. 工作线配置验证 - 保存时检查设备是否满足需求
    2. 运行时错误提示 - 设备缺失时给出明确提示
    """

    role: str  # 设备角色（对应 Device.device_role）
    min_count: int  # 最少需要数量
    description: str  # 角色用途说明


@dataclass(frozen=True)
class WorklineTopology:
    """单条工作线内部的设备拓扑快照。"""

    input_arm_id: int | None
    conveyor_id: int | None
    output_arm_id: int | None


# ==================== 辅助函数 ====================


def _resolve_int_attr(entity: Any, field: str) -> int | None:
    """安全读取整数字段，避免 MagicMock 扩散。"""

    value = getattr(entity, field, None)
    return value if isinstance(value, int) else None


def _resolve_device_role(device: Any) -> str | None:
    """安全读取设备角色。"""

    role = getattr(device, "device_role", None)
    return role if isinstance(role, str) and role else None


def _device_sort_key(device: Any) -> tuple[int, int]:
    """稳定排序设备，优先 role_index。"""

    role_index = _resolve_int_attr(device, "role_index")
    device_id = _resolve_int_attr(device, "id")
    return (role_index if role_index is not None else 10_000, device_id if device_id is not None else 10_000)


def _flatten_devices_by_role(devices_by_role: dict[str, list[Any]]) -> list[Any]:
    """将按角色分组的设备映射展开为去重列表。"""

    devices: list[Any] = []
    seen_ids: set[int] = set()
    seen_objects: set[int] = set()

    for grouped_devices in devices_by_role.values():
        for device in grouped_devices:
            device_id = _resolve_int_attr(device, "id")
            if device_id is not None:
                if device_id in seen_ids:
                    continue
                seen_ids.add(device_id)
            else:
                object_id = id(device)
                if object_id in seen_objects:
                    continue
                seen_objects.add(object_id)

            devices.append(device)

    return devices


def _find_descendant_device(
    start_device_id: int,
    target_role: str,
    device_by_id: dict[int, Any],
    downstream_by_device_id: dict[int, tuple[int, ...]],
) -> Any | None:
    """从指定设备向下游搜索首个目标角色设备。"""

    pending: deque[int] = deque(downstream_by_device_id.get(start_device_id, ()))
    visited: set[int] = set()

    while pending:
        current_device_id = pending.popleft()
        if current_device_id in visited:
            continue
        visited.add(current_device_id)

        device = device_by_id.get(current_device_id)
        if device is None:
            continue
        if _resolve_device_role(device) == target_role:
            return device

        pending.extend(downstream_by_device_id.get(current_device_id, ()))

    return None


def _build_downstream_map(device_by_id: dict[int, Any]) -> dict[int, tuple[int, ...]]:
    """基于 upstream_device_id 构建下游索引。"""

    downstream_map: dict[int, list[int]] = defaultdict(list)
    for device_id, device in device_by_id.items():
        upstream_device_id = _resolve_int_attr(device, "upstream_device_id")
        if upstream_device_id is not None and upstream_device_id in device_by_id:
            downstream_map[upstream_device_id].append(device_id)

    return {
        parent_id: tuple(sorted(children, key=lambda child_id: _device_sort_key(device_by_id[child_id])))
        for parent_id, children in downstream_map.items()
    }


def _resolve_workline_topology(
    ctx: "PluginContext",
) -> WorklineTopology:
    """根据当前工作线设备与 upstream 关系推导单线拓扑。"""

    devices = _flatten_devices_by_role(getattr(ctx, "devices_by_role", {}))
    if not devices:
        return WorklineTopology(
            input_arm_id=_get_device_id(ctx, SmtClassifierDeviceRole.INPUT_ARM.value),
            conveyor_id=_get_device_id(ctx, SmtClassifierDeviceRole.CONVEYOR.value),
            output_arm_id=_get_device_id(ctx, SmtClassifierDeviceRole.OUTPUT_ARM.value),
        )

    device_by_id = {
        device_id: device for device in devices if (device_id := _resolve_int_attr(device, "id")) is not None
    }
    if not device_by_id:
        return WorklineTopology(
            input_arm_id=_get_device_id(ctx, SmtClassifierDeviceRole.INPUT_ARM.value),
            conveyor_id=_get_device_id(ctx, SmtClassifierDeviceRole.CONVEYOR.value),
            output_arm_id=_get_device_id(ctx, SmtClassifierDeviceRole.OUTPUT_ARM.value),
        )

    downstream_by_device_id = _build_downstream_map(device_by_id)
    input_arm = next(
        iter(
            sorted(
                (
                    device
                    for device in device_by_id.values()
                    if _resolve_device_role(device) == SmtClassifierDeviceRole.INPUT_ARM.value
                ),
                key=_device_sort_key,
            )
        ),
        None,
    )
    conveyor = next(
        iter(
            sorted(
                (
                    device
                    for device in device_by_id.values()
                    if _resolve_device_role(device) == SmtClassifierDeviceRole.CONVEYOR.value
                ),
                key=_device_sort_key,
            )
        ),
        None,
    )
    output_arm = next(
        iter(
            sorted(
                (
                    device
                    for device in device_by_id.values()
                    if _resolve_device_role(device) == SmtClassifierDeviceRole.OUTPUT_ARM.value
                ),
                key=_device_sort_key,
            )
        ),
        None,
    )

    input_arm_id = _resolve_int_attr(input_arm, "id") if input_arm is not None else None
    if input_arm_id is not None:
        inferred_conveyor = _find_descendant_device(
            start_device_id=input_arm_id,
            target_role=SmtClassifierDeviceRole.CONVEYOR.value,
            device_by_id=device_by_id,
            downstream_by_device_id=downstream_by_device_id,
        )
        if inferred_conveyor is not None:
            conveyor = inferred_conveyor

    conveyor_id = _resolve_int_attr(conveyor, "id") if conveyor is not None else None
    if conveyor_id is not None:
        inferred_output_arm = _find_descendant_device(
            start_device_id=conveyor_id,
            target_role=SmtClassifierDeviceRole.OUTPUT_ARM.value,
            device_by_id=device_by_id,
            downstream_by_device_id=downstream_by_device_id,
        )
        if inferred_output_arm is not None:
            output_arm = inferred_output_arm

    return WorklineTopology(
        input_arm_id=input_arm_id,
        conveyor_id=conveyor_id,
        output_arm_id=_resolve_int_attr(output_arm, "id") if output_arm is not None else None,
    )


def _get_device_id(ctx: "PluginContext", role: str) -> int | None:
    """获取指定角色的设备 ID

    Args:
        ctx: 插件上下文
        role: 设备角色

    Returns:
        设备 ID 或 None（未找到时）
    """
    device = ctx.get_device_by_role(role)
    if device is None:
        return None
    return _resolve_int_attr(device, "id")


def _build_command(
    device_id: int,
    action: str,
    params: dict,
) -> CommandIntent:
    """构建设备命令意图

    Args:
        device_id: 目标设备 ID
        action: 命令动作
        params: 命令参数

    Returns:
        CommandIntent 实例
    """
    return CommandIntent(
        target_device_id=device_id,
        action=action,
        parameters=params,
    )


def _create_failure(domain: str, code: str, message: str) -> FailureIntent:
    """创建失败意图

    Args:
        domain: 失败域（HARDWARE / UPSTREAM / BUSINESS）
        code: 错误代码
        message: 错误消息

    Returns:
        FailureIntent 实例
    """
    return FailureIntent(domain=domain, code=code, message=message)


def _build_command_wait(wait_token: str, deadline_seconds: int) -> WaitIntent:
    """构建命令结果等待意图。"""

    return WaitIntent(
        wait_type="COMMAND_RESULT",
        wait_token=wait_token,
        deadline_seconds=deadline_seconds,
    )


def _missing_device_result(role: str) -> PluginResult:
    """构建设备缺失的统一失败结果。"""

    return PluginResult(
        failure=_create_failure(
            domain="HARDWARE",
            code="DEVICE_NOT_FOUND",
            message=f"{role} device not found",
        )
    )


def _ng_transition(reason: str) -> str:
    """将内部 NG 原因映射为状态机 transition。"""

    if reason == "SCAN_NG":
        return "scan_ng"
    if reason == "INSPECTION_NG":
        return "inspection_ng"
    return reason.lower()


# ==================== 插件实现 ====================


class SmtClassifierPlugin:
    """SMT 粗分机插件

    处理 SMT 产线的粗分作业流程：
    - 扫码识别判定 OK/NG
    - OK 流程：扫码OK → 检测OK → 流水线传输 → 出料
    - NG 流程：扫码NG/检测NG → 放入NG缓存位

    Attributes:
        plugin_key: 插件标识符
        required_device_roles: 设备角色需求声明
    """

    plugin_key = "smt_classifier"

    # ==================== 设备需求声明 ====================
    # 系统在保存工作线时验证设备配置是否满足这些需求

    required_device_roles: ClassVar[list[DeviceRoleRequirement]] = [
        DeviceRoleRequirement(
            role=SmtClassifierDeviceRole.INPUT_ARM.value,
            min_count=1,
            description="进料机械臂 - 扫码后抓取、NG 放置",
        ),
        DeviceRoleRequirement(
            role=SmtClassifierDeviceRole.OUTPUT_ARM.value,
            min_count=1,
            description="出料机械臂 - 最终出料",
        ),
        DeviceRoleRequirement(
            role=SmtClassifierDeviceRole.CONVEYOR.value,
            min_count=1,
            description="流水线 - OK 产品传输",
        ),
    ]

    # 默认超时配置（秒）
    DEFAULT_TIMEOUT_SECONDS = 300

    def _build_command_result(
        self,
        transition: str,
        context_patch: dict[str, Any],
        command: CommandIntent,
        wait_token: str,
    ) -> PluginResult:
        """构建带命令下发和等待的标准结果。"""

        return PluginResult(
            transition=transition,
            context_patch=context_patch,
            commands=[command],
            wait=_build_command_wait(wait_token, self.DEFAULT_TIMEOUT_SECONDS),
        )

    # ==================== 设备事件处理 ====================

    @classmethod
    def validate_workline_topology(cls, workline: Any, devices: Sequence[Any]) -> None:
        """校验工作线是否满足插件拓扑/设备要求。"""

        missing_roles = cls.describe_missing_device_roles(devices)
        if not missing_roles:
            return

        line_name = getattr(workline, "line_name", None)
        line_code = getattr(workline, "line_code", None)
        line_label = line_name if isinstance(line_name, str) and line_name else line_code
        workline_prefix = f"工作线[{line_label}] " if isinstance(line_label, str) and line_label else ""
        missing_text = "；".join(missing_roles)
        raise BadRequestException(message=f"{workline_prefix}不满足插件[{cls.plugin_key}]拓扑要求: {missing_text}")

    @classmethod
    def describe_missing_device_roles(cls, devices: Sequence[Any]) -> list[str]:
        """按插件声明返回缺失的设备角色描述。"""

        role_counter = Counter(
            device.device_role
            for device in devices
            if isinstance(getattr(device, "device_role", None), str) and device.device_role
        )

        missing_roles: list[str] = []
        for requirement in cls.required_device_roles:
            actual_count = role_counter.get(requirement.role, 0)
            if actual_count >= requirement.min_count:
                continue
            missing_roles.append(f"{requirement.role} 至少需要 {requirement.min_count} 台，当前 {actual_count} 台")
        return missing_roles

    async def on_device_event(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """处理设备事件

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult 包含状态转换、命令意图等
        """
        event_data = inbox.payload_json or {}
        event_type = event_data.get("event_type", "")

        # 兼容 Mock 数据格式: location 可能在 data.location
        data = event_data.get("data", {}) if isinstance(event_data.get("data"), dict) else {}
        location_id = event_data.get("location_id") or data.get("location") or ""

        ctx.logger.info(
            f"SmtClassifierPlugin received device event: {event_type}, inbox_id={inbox.id}, location={location_id}"
        )

        # 急停处理
        if event_type == SmtClassifierEventType.ESTOP_PRESSED.value:
            return self._handle_estop(ctx, event_data)

        # 扫码完成处理
        if event_type == SmtClassifierEventType.SCAN_COMPLETED.value:
            return await self._handle_scan_completed(ctx, event_data, location_id)

        # 检测完成处理
        if event_type == SmtClassifierEventType.INSPECTION_COMPLETED.value:
            return await self._handle_inspection_completed(ctx, event_data, location_id)

        ctx.logger.warning(f"Unknown event type: {event_type}")
        return PluginResult()

    async def _handle_scan_completed(
        self,
        ctx: "PluginContext",
        event_data: dict,
        location_id: str,
    ) -> PluginResult:
        """处理扫码完成事件"""
        # 兼容 Mock 服务数据格式: 优先从 data 字段获取
        data = event_data.get("data", {}) if isinstance(event_data.get("data"), dict) else {}
        barcode = data.get("barcode") or event_data.get("barcode") or ""
        scan_result = data.get("result") or event_data.get("scan_result") or "OK"

        topology = _resolve_workline_topology(ctx)

        ctx.logger.info(f"Scan completed: barcode={barcode}, result={scan_result}, location={location_id}")

        context_patch = {
            "stage": SmtClassifierStage.SCAN_RESULT_RECEIVED.value,
            "barcode": barcode,
            "last_barcode": barcode,
            "scan_result": scan_result,
            "location_id": location_id,
            "context_schema_version": "1.0",
        }

        # NG 流程：扫码 NG 直接放入 NG 缓存位
        if scan_result == "NG":
            return await self._handle_ng_flow(
                ctx=ctx,
                topology=topology,
                context_patch=context_patch,
                current_location=location_id,
                reason="SCAN_NG",
            )

        # OK 流程：继续检测
        context_patch["stage"] = SmtClassifierStage.WAITING_INSPECTION.value
        return PluginResult(transition="scan_ok", context_patch=context_patch)

    async def _handle_inspection_completed(
        self,
        ctx: "PluginContext",
        event_data: dict,
        location_id: str,
    ) -> PluginResult:
        """处理检测完成事件"""
        # 兼容 Mock 服务数据格式: 优先从 data 字段获取
        data = event_data.get("data", {}) if isinstance(event_data.get("data"), dict) else {}
        inspection_result = data.get("result") or event_data.get("inspection_result") or "OK"

        topology = _resolve_workline_topology(ctx)

        ctx.logger.info(f"Inspection completed: result={inspection_result}, location={location_id}")

        context_patch = {
            "stage": SmtClassifierStage.INSPECTION_RESULT_RECEIVED.value,
            "inspection_result": inspection_result,
        }
        return await self._process_inspection_result(
            ctx=ctx,
            inspection_result=inspection_result,
            topology=topology,
            location_id=location_id,
            context_patch=context_patch,
        )

    async def _process_inspection_result(
        self,
        ctx: "PluginContext",
        inspection_result: str,
        topology: WorklineTopology,
        location_id: str,
        context_patch: dict[str, Any],
    ) -> PluginResult:
        """统一处理检测结果，收敛 NG/OK 分支。"""

        if inspection_result == "NG":
            return await self._handle_ng_flow(
                ctx=ctx,
                topology=topology,
                context_patch=context_patch,
                current_location=location_id,
                reason="INSPECTION_NG",
            )

        return await self._start_conveyor_transfer(ctx, topology, context_patch, location_id)

    async def _handle_ng_flow(
        self,
        ctx: "PluginContext",
        topology: WorklineTopology,
        context_patch: dict,
        current_location: str,
        reason: str,
    ) -> PluginResult:
        """处理 NG 流程：放入 NG 缓存位

        Args:
            ctx: 插件上下文
            topology: 工作线内部设备拓扑
            context_patch: 上下文更新
            current_location: 当前位置
            reason: NG 原因（SCAN_NG / INSPECTION_NG）
        """
        # 获取进料机械臂
        input_arm_id = topology.input_arm_id
        if input_arm_id is None:
            return _missing_device_result(SmtClassifierDeviceRole.INPUT_ARM.value)

        # 构建抓取放置命令 - 使用逻辑位置类型而非物理位置ID
        command = _build_command(
            device_id=input_arm_id,
            action=SmtClassifierCommandType.PICK_AND_PUT.value,
            params={
                "source_type": LocationType.INPUT_PLATFORM.value,
                "target_type": LocationType.NG_PLATFORM.value,
                "reason": reason,
            },
        )

        context_patch["stage"] = SmtClassifierStage.WAITING_PICK_PLACE.value
        context_patch["ng_reason"] = reason
        context_patch["source_type"] = LocationType.INPUT_PLATFORM.value
        context_patch["target_type"] = LocationType.NG_PLATFORM.value

        return self._build_command_result(
            transition=_ng_transition(reason),
            context_patch=context_patch,
            command=command,
            wait_token=f"ng_pick_place_{ctx.session.id}",
        )

    async def _start_conveyor_transfer(
        self,
        ctx: "PluginContext",
        topology: WorklineTopology,
        context_patch: dict,
        location_id: str,
    ) -> PluginResult:
        """启动流水线传输"""
        # 获取流水线设备
        conveyor_id = topology.conveyor_id
        if conveyor_id is None:
            return _missing_device_result(SmtClassifierDeviceRole.CONVEYOR.value)

        # 构建流水线传输命令 - 使用逻辑位置类型
        command = _build_command(
            device_id=conveyor_id,
            action=SmtClassifierCommandType.MOVE_FORWARD.value,
            params={
                "source_type": LocationType.PIPELINE_PLATFORM.value,
                "target_type": LocationType.PIPELINE_PLATFORM.value,
            },
        )

        context_patch["stage"] = SmtClassifierStage.WAITING_CONVEYOR.value
        context_patch["source_type"] = LocationType.PIPELINE_PLATFORM.value
        context_patch["target_type"] = LocationType.PIPELINE_PLATFORM.value

        return self._build_command_result(
            transition="inspection_ok",
            context_patch=context_patch,
            command=command,
            wait_token=f"conveyor_transfer_{ctx.session.id}",
        )

    def _handle_estop(self, ctx: "PluginContext", event_data: dict) -> PluginResult:
        """处理急停事件"""
        ctx.logger.warning("Emergency stop pressed, pausing session")

        return PluginResult(
            transition="estop",
            context_patch={
                "stage": SmtClassifierStage.ERROR.value,
                "estop_pressed": True,
                "estop_timestamp": event_data.get("timestamp"),
            },
            failure=_create_failure(
                domain="HARDWARE",
                code="ESTOP_PRESSED",
                message="Emergency stop button pressed",
            ),
        )

    # ==================== 命令结果处理 ====================

    async def on_command_result(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """处理命令结果

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult 包含状态转换
        """
        result_data = inbox.payload_json or {}
        payload_data = (
            cast("dict[str, Any]", result_data.get("data")) if isinstance(result_data.get("data"), dict) else {}
        )
        command_type = result_data.get("command_type") or payload_data.get("command_type") or ""
        result = result_data.get("result", "SUCCESS")

        ctx.logger.info(f"SmtClassifierPlugin received command result: {command_type}, result={result}")

        # 获取当前会话上下文
        session_ctx = ctx.session.context_json or {}
        ng_reason = session_ctx.get("ng_reason", "")
        current_location = session_ctx.get("current_location")
        resolved_location_id = current_location if isinstance(current_location, str) and current_location else None
        topology = _resolve_workline_topology(ctx)

        # 命令失败处理
        if result != "SUCCESS":
            # 递增重试计数
            current_retry = session_ctx.get("retry_count", 0)
            return self._handle_command_failure(ctx, command_type, result_data, current_retry)

        # 抓取放置完成
        if command_type == SmtClassifierCommandType.PICK_AND_PUT.value:
            return self._handle_pick_place_completed(ng_reason, resolved_location_id)

        # 流水线传输完成
        if command_type == SmtClassifierCommandType.MOVE_FORWARD.value:
            return await self._handle_move_forward_completed(ctx, topology)

        ctx.logger.warning(f"Unknown command type: {command_type}")
        return PluginResult()

    def _handle_pick_place_completed(self, ng_reason: str, location_id: str | None) -> PluginResult:
        """处理抓取放置完成"""
        # NG 处理完成
        if ng_reason in ("SCAN_NG", "INSPECTION_NG"):
            return PluginResult(
                transition="ng_handled",
                context_patch={
                    "stage": SmtClassifierStage.COMPLETED.value,
                    "ng_handled": True,
                    "ng_reason": ng_reason,
                    "location_id": location_id,
                },
                complete=True,
            )

        if ng_reason == "OUTPUT":
            return PluginResult(
                transition="output_handled",
                context_patch={
                    "stage": SmtClassifierStage.COMPLETED.value,
                    "location_id": location_id,
                },
                complete=True,
            )

        # OK 流程继续
        return PluginResult(
            transition="pick_place_ok",
            context_patch={"stage": SmtClassifierStage.WAITING_INSPECTION.value},
        )

    async def _handle_move_forward_completed(self, ctx: "PluginContext", topology: WorklineTopology) -> PluginResult:
        """处理流水线传输完成。"""
        # 获取出料机械臂
        output_arm_id = topology.output_arm_id
        if output_arm_id is None:
            return _missing_device_result(SmtClassifierDeviceRole.OUTPUT_ARM.value)

        # 构建出料命令 - 使用逻辑位置类型
        command = _build_command(
            device_id=output_arm_id,
            action=SmtClassifierCommandType.PICK_AND_PUT.value,
            params={
                "source_type": LocationType.PIPELINE_PLATFORM.value,
                "target_type": LocationType.OUTPUT_PLATFORM.value,
                "reason": "OUTPUT",
            },
        )

        return self._build_command_result(
            transition="conveyor_complete",
            context_patch={
                "stage": SmtClassifierStage.WAITING_OUTPUT.value,
                "source_type": LocationType.PIPELINE_PLATFORM.value,
                "target_type": LocationType.OUTPUT_PLATFORM.value,
                "pick_place_reason": "OUTPUT",
            },
            command=command,
            wait_token=f"output_{ctx.session.id}",
        )

    def _handle_command_failure(
        self,
        ctx: "PluginContext",
        command_type: str,
        result_data: dict,
        retry_count: int = 0,
    ) -> PluginResult:
        """处理命令失败"""
        error_detail = result_data.get("error_detail", {})
        error_code = error_detail.get("code", "UNKNOWN_ERROR")
        error_message = error_detail.get("message", "Command execution failed")

        ctx.logger.error(
            f"Command failed: type={command_type}, code={error_code}, message={error_message}, retry={retry_count}"
        )

        # 递增重试计数
        new_retry_count = retry_count + 1

        # 获取错误恢复策略
        recovery_strategy = get_error_recovery_strategy(error_code)
        should_auto_retry = recovery_strategy.auto_retry if recovery_strategy else False
        max_retries = recovery_strategy.max_retries if recovery_strategy else 0

        # 如果允许自动重试且未达到最大重试次数，进入重试逻辑
        if should_auto_retry and new_retry_count <= max_retries:
            ctx.logger.info(f"Auto-retrying command: {command_type}, attempt {new_retry_count}/{max_retries}")
            return PluginResult(
                transition="retry",
                context_patch={
                    "retry_count": new_retry_count,
                    "last_retry_at": ctx.clock().isoformat(),
                },
            )

        return PluginResult(
            transition="command_failed",
            context_patch={
                "stage": SmtClassifierStage.ERROR.value,
                "retry_count": new_retry_count,
                "last_error": {
                    "command_type": command_type,
                    "code": error_code,
                    "message": error_message,
                },
            },
            failure=_create_failure(
                domain="HARDWARE",
                code=error_code,
                message=error_message,
            ),
        )

    # ==================== 超时处理 ====================

    async def on_timeout(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """处理超时

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult 包含失败归因
        """
        ctx.logger.warning(f"SmtClassifierPlugin received timeout: inbox_id={inbox.id}")

        session_ctx = ctx.session.context_json or {}
        current_stage = session_ctx.get("stage", SmtClassifierStage.IDLE.value)

        return PluginResult(
            transition="timeout",
            context_patch={
                "stage": SmtClassifierStage.ERROR.value,
                "timeout_at_stage": current_stage,
            },
            failure=_create_failure(
                domain="HARDWARE",
                code="TIMEOUT",
                message=f"Operation timeout at stage: {current_stage}",
            ),
        )

    # ==================== 外部 HTTP 回调处理 ====================

    async def on_external_http(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """处理外部 HTTP 回调

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult 包含状态转换
        """
        callback_data = inbox.payload_json or {}
        callback_type = callback_data.get("callback_type", "")

        ctx.logger.info(f"SmtClassifierPlugin received external HTTP: type={callback_type}")

        # MES 检测结果回调
        if callback_type == "MES_INSPECTION_RESULT":
            return await self._handle_mes_inspection_callback(ctx, callback_data)

        # WCS 任务状态回调
        if callback_type == "WCS_TASK_STATUS":
            return self._handle_wcs_task_callback(ctx, callback_data)

        ctx.logger.warning(f"Unknown external callback type: {callback_type}")
        return PluginResult()

    async def _handle_mes_inspection_callback(self, ctx: "PluginContext", callback_data: dict) -> PluginResult:
        """处理 MES 检测结果回调"""
        inspection_result = callback_data.get("inspection_result", "OK")
        barcode = callback_data.get("barcode", "")

        ctx.logger.info(f"MES inspection callback: barcode={barcode}, result={inspection_result}")

        session_ctx = ctx.session.context_json or {}
        location_id = session_ctx.get("location_id", "")
        topology = _resolve_workline_topology(ctx)

        context_patch = {
            "stage": SmtClassifierStage.INSPECTION_RESULT_RECEIVED.value,
            "inspection_result": inspection_result,
            "mes_callback": True,
        }
        return await self._process_inspection_result(
            ctx=ctx,
            inspection_result=inspection_result,
            topology=topology,
            location_id=location_id,
            context_patch=context_patch,
        )

    def _handle_wcs_task_callback(self, ctx: "PluginContext", callback_data: dict) -> PluginResult:
        """处理 WCS 任务状态回调"""
        task_status = callback_data.get("task_status", "")
        task_id = callback_data.get("task_id", "")

        ctx.logger.info(f"WCS task callback: task_id={task_id}, status={task_status}")

        if task_status == "COMPLETED":
            return PluginResult(
                transition="wcs_task_complete",
                context_patch={
                    "wcs_task_completed": True,
                    "wcs_task_id": task_id,
                },
            )

        if task_status == "FAILED":
            error_message = callback_data.get("error_message", "WCS task failed")
            return PluginResult(
                transition="wcs_task_failed",
                context_patch={
                    "wcs_task_failed": True,
                    "wcs_task_id": task_id,
                },
                failure=_create_failure(
                    domain="UPSTREAM",
                    code="WCS_TASK_FAILED",
                    message=error_message,
                ),
            )

        return PluginResult()

    # ==================== 人工操作处理 ====================

    async def on_manual_operation(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """处理人工操作

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult 包含状态转换
        """
        operation_data = inbox.payload_json or {}
        operation_type = operation_data.get("operation_type", "")
        reason = operation_data.get("reason", "")

        ctx.logger.info(f"SmtClassifierPlugin received manual operation: type={operation_type}, reason={reason}")

        if operation_type == "MANUAL_HOLD":
            return self._handle_manual_hold(ctx, reason)

        if operation_type == "MANUAL_RESUME":
            return self._handle_manual_resume(ctx)

        if operation_type == "MANUAL_CANCEL":
            return self._handle_manual_cancel(ctx, reason)

        ctx.logger.warning(f"Unknown manual operation type: {operation_type}")
        return PluginResult()

    def _handle_manual_hold(self, ctx: "PluginContext", reason: str) -> PluginResult:
        """处理人工暂停"""
        ctx.logger.info(f"Manual hold requested: reason={reason}")

        return PluginResult(
            transition="manual_hold",
            context_patch={
                "manual_hold": True,
                "hold_reason": reason,
                "hold_timestamp": ctx.clock().isoformat(),
            },
        )

    def _handle_manual_resume(self, ctx: "PluginContext") -> PluginResult:
        """处理人工恢复"""
        ctx.logger.info("Manual resume requested")

        return PluginResult(
            transition="manual_resume",
            context_patch={
                "manual_hold": False,
                "hold_reason": None,
                "resumed_at": ctx.clock().isoformat(),
            },
        )

    def _handle_manual_cancel(self, ctx: "PluginContext", reason: str) -> PluginResult:
        """处理人工取消"""
        ctx.logger.info(f"Manual cancel requested: reason={reason}")

        return PluginResult(
            transition="manual_cancel",
            context_patch={
                "cancelled": True,
                "cancel_reason": reason,
                "cancelled_at": ctx.clock().isoformat(),
            },
            complete=True,
        )


# ==================== 错误码映射 ====================


class ErrorCode(str, Enum):
    """SMT 粗分机错误码定义"""

    # 设备错误
    DEVICE_TIMEOUT = "DEVICE_TIMEOUT"  # 设备响应超时
    DEVICE_OFFLINE = "DEVICE_OFFLINE"  # 设备离线
    DEVICE_BUSY = "DEVICE_BUSY"  # 设备忙

    # 扫码错误
    SCAN_FAILED = "SCAN_FAILED"  # 扫码失败
    SCAN_EMPTY = "SCAN_EMPTY"  # 未扫描到条码
    SCAN_INVALID = "SCAN_INVALID"  # 条码无效

    # 检测错误
    DETECT_FAILED = "DETECT_FAILED"  # 检测失败
    SIZE_NG = "SIZE_NG"  # 尺寸不合格
    THICKNESS_NG = "THICKNESS_NG"  # 厚度不合格

    # 机械臂错误
    PICK_FAILED = "PICK_FAILED"  # 抓取失败
    PUT_FAILED = "PUT_FAILED"  # 放置失败
    ARM_BLOCKED = "ARM_BLOCKED"  # 机械臂被阻挡

    # 流水线错误
    CONVEYOR_JAM = "CONVEYOR_JAM"  # 流水线卡料
    CONVEYOR_TIMEOUT = "CONVEYOR_TIMEOUT"  # 流水线传输超时

    # 系统错误
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"  # 会话不存在
    INVALID_STATE = "INVALID_STATE"  # 无效状态
    TOPOLOGY_ERROR = "TOPOLOGY_ERROR"  # 拓扑错误


@dataclass
class ErrorRecoveryStrategy:
    """错误恢复策略"""

    code: str  # 错误码
    description: str  # 错误描述
    auto_retry: bool  # 是否自动重试
    max_retries: int  # 最大重试次数
    recovery_action: str  # 恢复动作
    manual_hold: bool  # 是否需要人工介入


# 错误码到恢复策略的映射表
ERROR_RECOVERY_MAP: dict[str, ErrorRecoveryStrategy] = {
    ErrorCode.DEVICE_TIMEOUT.value: ErrorRecoveryStrategy(
        code=ErrorCode.DEVICE_TIMEOUT.value,
        description="设备响应超时",
        auto_retry=True,
        max_retries=3,
        recovery_action="retry_command",
        manual_hold=False,
    ),
    ErrorCode.DEVICE_OFFLINE.value: ErrorRecoveryStrategy(
        code=ErrorCode.DEVICE_OFFLINE.value,
        description="设备离线",
        auto_retry=False,
        max_retries=0,
        recovery_action="wait_device_online",
        manual_hold=True,
    ),
    ErrorCode.SCAN_FAILED.value: ErrorRecoveryStrategy(
        code=ErrorCode.SCAN_FAILED.value,
        description="扫码失败",
        auto_retry=True,
        max_retries=2,
        recovery_action="retry_scan",
        manual_hold=False,
    ),
    ErrorCode.SCAN_EMPTY.value: ErrorRecoveryStrategy(
        code=ErrorCode.SCAN_EMPTY.value,
        description="未扫描到条码",
        auto_retry=False,
        max_retries=0,
        recovery_action="manual_inspection",
        manual_hold=True,
    ),
    ErrorCode.DETECT_FAILED.value: ErrorRecoveryStrategy(
        code=ErrorCode.DETECT_FAILED.value,
        description="检测失败",
        auto_retry=True,
        max_retries=2,
        recovery_action="retry_detect",
        manual_hold=False,
    ),
    ErrorCode.SIZE_NG.value: ErrorRecoveryStrategy(
        code=ErrorCode.SIZE_NG.value,
        description="尺寸不合格",
        auto_retry=False,
        max_retries=0,
        recovery_action="ng_flow",
        manual_hold=False,
    ),
    ErrorCode.THICKNESS_NG.value: ErrorRecoveryStrategy(
        code=ErrorCode.THICKNESS_NG.value,
        description="厚度不合格",
        auto_retry=False,
        max_retries=0,
        recovery_action="ng_flow",
        manual_hold=False,
    ),
    ErrorCode.PICK_FAILED.value: ErrorRecoveryStrategy(
        code=ErrorCode.PICK_FAILED.value,
        description="抓取失败",
        auto_retry=True,
        max_retries=1,
        recovery_action="retry_pick",
        manual_hold=False,
    ),
    ErrorCode.CONVEYOR_JAM.value: ErrorRecoveryStrategy(
        code=ErrorCode.CONVEYOR_JAM.value,
        description="流水线卡料",
        auto_retry=False,
        max_retries=0,
        recovery_action="clear_jam",
        manual_hold=True,
    ),
    ErrorCode.SESSION_NOT_FOUND.value: ErrorRecoveryStrategy(
        code=ErrorCode.SESSION_NOT_FOUND.value,
        description="会话不存在",
        auto_retry=False,
        max_retries=0,
        recovery_action="create_new_session",
        manual_hold=False,
    ),
}


def get_error_recovery_strategy(error_code: str) -> ErrorRecoveryStrategy | None:
    """获取错误码对应的恢复策略"""
    return ERROR_RECOVERY_MAP.get(error_code)


def determine_error_code(event_data: dict[str, Any], command_result: dict[str, Any] | None = None) -> str:
    """根据事件数据和命令结果确定错误码

    Args:
        event_data: 事件数据
        command_result: 命令结果（可选）

    Returns:
        错误码字符串
    """
    # 从命令结果解析错误码
    if command_result:
        result_code = command_result.get("result", "")
        if result_code == "FAILED":
            error_detail = command_result.get("error_detail", {})
            device_error = error_detail.get("error_code", "")
            if device_error:
                return device_error

    # 从事件数据解析错误码
    event_type = event_data.get("event_type", "")
    if event_type == "ESTOP_PRESSED":
        return ErrorCode.DEVICE_OFFLINE.value

    # 默认返回通用错误
    return ErrorCode.DEVICE_TIMEOUT.value


smt_classifier_plugin = SmtClassifierPlugin()

__all__ = [
    "ERROR_RECOVERY_MAP",
    "DeviceRoleRequirement",
    "ErrorCode",
    "ErrorRecoveryStrategy",
    "SmtClassifierCapabilities",
    "SmtClassifierCommandType",
    "SmtClassifierDeviceRole",
    "SmtClassifierEventType",
    "SmtClassifierPlugin",
    "SmtClassifierStage",
    "get_error_recovery_strategy",
    "smt_classifier_plugin",
]
