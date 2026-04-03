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

import asyncio
import os
from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from src.core.exceptions import BadRequestException
from src.workline_plugins.smt_classifier.contract import (
    CONTRACT_VERSION,
    EVENT_FIELD_ALIASES,
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


JsonDict = dict[str, Any]
_SYNC_HTTP_TIMEOUT_SECONDS = 10.0
_SYNC_HTTP_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_DEFAULT_AGV_CALLBACK_TYPE = "AGV_TASK_RESULT"
_EXTERNAL_HTTP_DECISION_TYPE = "EXTERNAL_HTTP_REQUEST"
_REQUIRED_TARGET_BIN_FIELDS = (
    "station_location_id",
    "rack_id",
    "bin_id",
    "bin_type",
    "bin_cell_location",
    "reel_layer",
    "reel_thickness",
    "reel_diameter",
    "reel_totalthickness",
)


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
    WAITING_AGV_DELIVERY = "WAITING_AGV_DELIVERY"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


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


def _ensure_dict(value: Any) -> JsonDict:
    """将动态输入收敛为字典，避免 Unknown 类型扩散。"""

    return cast("JsonDict", value) if isinstance(value, dict) else {}


def _ensure_str(value: Any, default: str = "") -> str:
    """将动态输入收敛为字符串。"""

    return value if isinstance(value, str) else default


def _has_embedded_inspection_payload(payload: JsonDict) -> bool:
    """判断命令结果是否已携带检测/测厚结果。"""

    return any(
        resolve_first_str(payload, aliases)
        for aliases in (
            ("inspection_result", "data.inspection_result", "data.result"),
            ("reel_diameter", "data.reel_diameter"),
            ("reel_thickness", "data.reel_thickness"),
        )
    )


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
    params: JsonDict,
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
        parameters={
            **params,
            "params": params,
        },
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


def _resolve_env_or_config(
    ctx: "PluginContext",
    *,
    config_key: str,
    nested_key: str | None = None,
    env_key: str,
) -> str:
    config_value = ctx.config.get(config_key)
    if nested_key and isinstance(config_value, dict):
        nested_value = config_value.get(nested_key)
        if isinstance(nested_value, str) and nested_value:
            return nested_value
    if isinstance(config_value, str) and config_value:
        return config_value

    env_value = os.getenv(env_key, "")
    return env_value if env_value else ""


def _resolve_timeout_seconds(ctx: "PluginContext", *, config_key: str, env_key: str) -> float:
    config_value = ctx.config.get(config_key)
    if isinstance(config_value, int) and config_value > 0:
        return float(config_value)
    if isinstance(config_value, float) and config_value > 0:
        return config_value

    env_value = os.getenv(env_key, "")
    try:
        timeout_value = float(env_value)
    except ValueError:
        timeout_value = 0.0
    return timeout_value if timeout_value > 0 else _SYNC_HTTP_TIMEOUT_SECONDS


def _normalize_identifier(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "-" for ch in value.strip())
    compact = normalized.strip("-")
    return compact or "unknown"


def _session_business_key(ctx: "PluginContext", context: JsonDict) -> str:
    session_business_key = getattr(ctx.session, "business_key", None)
    if isinstance(session_business_key, str) and session_business_key:
        return session_business_key
    barcode = context.get("barcode")
    if isinstance(barcode, str) and barcode:
        return barcode
    return ctx.correlation_id or f"session-{getattr(ctx.session, 'id', 'unknown')}"


def _next_request_code(
    ctx: "PluginContext",
    context: JsonDict,
    *,
    prefix: str,
    counter_key: str,
) -> tuple[str, int]:
    raw_counter = context.get(counter_key, 0)
    current_counter = raw_counter if isinstance(raw_counter, int) and raw_counter >= 0 else 0
    next_counter = current_counter + 1
    business_key = _normalize_identifier(_session_business_key(ctx, context))
    return f"{prefix}-{business_key}-{next_counter:02d}", next_counter


def _current_workline_code(ctx: "PluginContext") -> str:
    workline_code = getattr(ctx.workline, "line_code", None)
    if isinstance(workline_code, str) and workline_code:
        return workline_code
    workline_name = getattr(ctx.workline, "line_name", None)
    if isinstance(workline_name, str) and workline_name:
        return workline_name
    return f"workline-{getattr(ctx.workline, 'id', 'unknown')}"


def _current_source_location(context: JsonDict) -> str:
    location_value = context.get("current_location")
    if isinstance(location_value, str) and location_value:
        return location_value
    location_id = context.get("location_id")
    if isinstance(location_id, str) and location_id:
        return location_id
    return "PIPELINE_OUTPUT"


def _normalize_target_bin(context: JsonDict, raw_target_bin: JsonDict) -> tuple[JsonDict | None, list[str]]:
    target_bin: JsonDict = {
        "station_location_id": _ensure_str(
            raw_target_bin.get("station_location_id") or raw_target_bin.get("station_location")
        ),
        "rack_id": _ensure_str(raw_target_bin.get("rack_id")),
        "bin_id": _ensure_str(raw_target_bin.get("bin_id")),
        "bin_type": _ensure_str(raw_target_bin.get("bin_type")),
        "bin_cell_location": _ensure_str(raw_target_bin.get("bin_cell_location") or raw_target_bin.get("bin_cell")),
        "reel_layer": _ensure_str(raw_target_bin.get("reel_layer")),
        "reel_thickness": _ensure_str(raw_target_bin.get("reel_thickness") or context.get("thickness")),
        "reel_diameter": _ensure_str(raw_target_bin.get("reel_diameter") or context.get("reel_diameter")),
        "reel_totalthickness": _ensure_str(raw_target_bin.get("reel_totalthickness")),
    }
    target_bin["bin_cell"] = target_bin["bin_cell_location"]

    missing_fields = [field for field in _REQUIRED_TARGET_BIN_FIELDS if not _ensure_str(target_bin.get(field))]
    if missing_fields:
        return None, missing_fields
    return target_bin, []


async def _post_json_with_retry(
    *,
    url: str,
    payload: JsonDict,
    timeout_seconds: float,
) -> JsonDict:
    import httpx

    last_error: Exception | None = None

    for attempt_index in range(len(_SYNC_HTTP_RETRY_BACKOFF_SECONDS) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(url, json=payload)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise TypeError("response body must be an object")
            return cast("JsonDict", response_payload)
        except Exception as exc:
            last_error = exc
            if attempt_index >= len(_SYNC_HTTP_RETRY_BACKOFF_SECONDS):
                break
            await asyncio.sleep(_SYNC_HTTP_RETRY_BACKOFF_SECONDS[attempt_index])

    raise RuntimeError(f"request failed: {last_error}")


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
    contract_version = CONTRACT_VERSION

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

    async def _request_bin_allocation(
        self,
        ctx: "PluginContext",
        context: JsonDict,
        *,
        source_location: str,
    ) -> tuple[JsonDict, str, int]:
        allocation_url = _resolve_env_or_config(
            ctx,
            config_key="bin_allocation",
            nested_key="url",
            env_key="SMT_CLASSIFIER_BIN_ALLOCATION_URL",
        )
        if not allocation_url:
            raise RuntimeError("bin allocation url is not configured")

        request_code, allocation_attempt = _next_request_code(
            ctx,
            context,
            prefix="ALLOC",
            counter_key="allocation_request_count",
        )

        from src.utils.timezone import timezone

        payload: JsonDict = {
            "request_code": request_code,
            "workline_code": _current_workline_code(ctx),
            "business_key": _session_business_key(ctx, context),
            "barcode": _ensure_str(context.get("barcode")),
            "reel_diameter": _ensure_str(context.get("reel_diameter")),
            "reel_thickness": _ensure_str(context.get("thickness")),
            "inspection_result": _ensure_str(context.get("inspection_result"), "OK"),
            "source_location": source_location,
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
        }
        response_payload = await _post_json_with_retry(
            url=allocation_url,
            payload=payload,
            timeout_seconds=_resolve_timeout_seconds(
                ctx,
                config_key="bin_allocation_timeout_seconds",
                env_key="SMT_CLASSIFIER_BIN_ALLOCATION_TIMEOUT_SECONDS",
            ),
        )
        return response_payload, request_code, allocation_attempt

    def _build_output_command(
        self,
        output_arm_id: int,
        target_bin: JsonDict,
    ) -> CommandIntent:
        return _build_command(
            device_id=output_arm_id,
            action=SmtClassifierCommandType.PICK_AND_PUT.value,
            params={
                "source_type": LocationType.PIPELINE_PLATFORM.value,
                "target_type": LocationType.BIN.value,
                "reason": "OUTPUT",
                "target_info": {
                    "station_location_id": target_bin["station_location_id"],
                    "rack_id": target_bin["rack_id"],
                    "bin_id": target_bin["bin_id"],
                    "bin_type": target_bin["bin_type"],
                    "bin_cell_location": target_bin["bin_cell_location"],
                    "bin_cell": target_bin["bin_cell"],
                    "reel_layer": target_bin["reel_layer"],
                    "reel_thickness": target_bin["reel_thickness"],
                    "reel_diameter": target_bin["reel_diameter"],
                    "reel_totalthickness": target_bin["reel_totalthickness"],
                },
            },
        )

    def _build_output_command_result(
        self,
        *,
        ctx: "PluginContext",
        output_arm_id: int,
        target_bin: JsonDict,
        transition: str,
        extra_context_patch: JsonDict | None = None,
    ) -> PluginResult:
        context_patch: JsonDict = {
            "stage": SmtClassifierStage.WAITING_OUTPUT.value,
            "source_type": LocationType.PIPELINE_PLATFORM.value,
            "target_type": LocationType.BIN.value,
            "pick_place_reason": "OUTPUT",
            "target_bin": target_bin,
            "target_bin_id": target_bin["bin_id"],
            "target_bin_cell": target_bin["bin_cell"],
            STEP_CODE_KEY: SmtClassifierStepCode.OUTPUT_PICK_PLACE.value,
        }
        if extra_context_patch:
            context_patch.update(extra_context_patch)

        return self._build_command_result(
            transition=transition,
            context_patch=context_patch,
            command=self._build_output_command(output_arm_id, target_bin),
            wait_token=f"output_{ctx.session.id}",
        )

    def _build_agv_request_decision(
        self,
        ctx: "PluginContext",
        context: JsonDict,
        *,
        agv_request: JsonDict,
        agv_request_code: str,
    ) -> dict[str, Any]:
        agv_url = _resolve_env_or_config(
            ctx,
            config_key="agv_dispatch",
            nested_key="url",
            env_key="SMT_CLASSIFIER_AGV_DISPATCH_URL",
        )
        if not agv_url:
            raise RuntimeError("agv dispatch url is not configured")

        callback_url = _resolve_env_or_config(
            ctx,
            config_key="external_callback",
            nested_key="url",
            env_key="WES_EXTERNAL_CALLBACK_URL",
        )
        callback_type = (
            _resolve_env_or_config(
                ctx,
                config_key="external_callback_type",
                nested_key=None,
                env_key="SMT_CLASSIFIER_AGV_CALLBACK_TYPE",
            )
            or _DEFAULT_AGV_CALLBACK_TYPE
        )

        from src.utils.timezone import timezone

        payload: JsonDict = {
            "command_code": agv_request_code,
            "task_type": "MOVE_RACK",
            "priority": 5,
            "timeout": 300000,
            "correlation_id": ctx.correlation_id,
            "callback_type": callback_type,
            "params": {
                "workline_code": _current_workline_code(ctx),
                "business_key": _session_business_key(ctx, context),
                **agv_request,
            },
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
        }
        if callback_url:
            payload["callback_url"] = callback_url

        return {
            "decision_type": _EXTERNAL_HTTP_DECISION_TYPE,
            "dispatch_key": f"external-http:{agv_request_code}",
            "target_code": agv_url,
            "payload": payload,
            "source_system": "AGV",
        }

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
        event_data = _ensure_dict(inbox.payload_json)
        event_type = resolve_first_str(event_data, EVENT_FIELD_ALIASES["event_type"]["aliases"])
        location_id = resolve_first_str(event_data, EVENT_FIELD_ALIASES["location_id"]["aliases"])

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
        event_data: JsonDict,
        location_id: str,
    ) -> PluginResult:
        """处理扫码完成事件"""
        barcode = resolve_first_str(event_data, EVENT_FIELD_ALIASES["barcode"]["aliases"])
        scan_result = resolve_first_str(
            event_data,
            EVENT_FIELD_ALIASES["scan_result"]["aliases"],
            default="OK",
        )

        topology = _resolve_workline_topology(ctx)

        ctx.logger.info(f"Scan completed: barcode={barcode}, result={scan_result}, location={location_id}")

        # 检查当前 Session 状态，避免重复处理
        current_stage = _ensure_str(_ensure_dict(getattr(ctx.session, "context_json", None)).get("stage"))
        if current_stage and current_stage != SmtClassifierStage.IDLE.value:
            ctx.logger.warning(f"Session already in stage '{current_stage}', ignoring duplicate scan event")
            # 返回空结果，不进行状态转换
            return PluginResult()

        context_patch: JsonDict = {
            "stage": SmtClassifierStage.SCAN_RESULT_RECEIVED.value,
            "barcode": barcode,
            "last_barcode": barcode,
            "scan_result": scan_result,
            "location_id": location_id,
            "context_schema_version": "1.0",
            STEP_CODE_KEY: SmtClassifierStepCode.WAITING_SCAN_EVENT.value,
        }

        # NG 流程：扫码 NG 直接放入 NG 缓存位
        if scan_result == "NG":
            return await self._handle_ng_flow(
                ctx=ctx,
                topology=topology,
                context_patch=context_patch,
                _current_location=location_id,
                reason="SCAN_NG",
            )

        # OK 流程：生成 PICK_AND_PUT 命令，将货物从扫码位抓取到检测位
        input_arm_id = topology.input_arm_id
        if input_arm_id is None:
            return _missing_device_result(SmtClassifierDeviceRole.INPUT_ARM.value)

        command = _build_command(
            device_id=input_arm_id,
            action=SmtClassifierCommandType.PICK_AND_PUT.value,
            params={
                "source_type": LocationType.INPUT_PLATFORM.value,
                "target_type": LocationType.PIPELINE_PLATFORM.value,
            },
        )

        context_patch["stage"] = SmtClassifierStage.WAITING_INSPECTION.value
        context_patch["source_type"] = LocationType.INPUT_PLATFORM.value
        context_patch["target_type"] = LocationType.PIPELINE_PLATFORM.value
        context_patch[STEP_CODE_KEY] = SmtClassifierStepCode.INPUT_PICK_PLACE.value

        return self._build_command_result(
            transition="scan_ok",
            context_patch=context_patch,
            command=command,
            wait_token=f"scan_pick_place_{ctx.session.id}",
        )

    async def _handle_inspection_completed(
        self,
        ctx: "PluginContext",
        event_data: JsonDict,
        location_id: str,
    ) -> PluginResult:
        """处理检测完成事件"""
        inspection_result = resolve_first_str(
            event_data,
            EVENT_FIELD_ALIASES["inspection_result"]["aliases"],
            default="OK",
        )

        topology = _resolve_workline_topology(ctx)

        ctx.logger.info(f"Inspection completed: result={inspection_result}, location={location_id}")

        context_patch: JsonDict = {
            "stage": SmtClassifierStage.INSPECTION_RESULT_RECEIVED.value,
            "inspection_result": inspection_result,
            STEP_CODE_KEY: SmtClassifierStepCode.WAITING_INSPECTION_EVENT.value,
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
                _current_location=location_id,
                reason="INSPECTION_NG",
            )

        return await self._start_conveyor_transfer(ctx, topology, context_patch, location_id)

    async def _handle_ng_flow(
        self,
        ctx: "PluginContext",
        topology: WorklineTopology,
        context_patch: JsonDict,
        _current_location: str,
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
        context_patch["pick_place_reason"] = reason
        context_patch["source_type"] = LocationType.INPUT_PLATFORM.value
        context_patch["target_type"] = LocationType.NG_PLATFORM.value
        context_patch[STEP_CODE_KEY] = SmtClassifierStepCode.NG_PICK_PLACE.value

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
        context_patch: JsonDict,
        _location_id: str,
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
        context_patch[STEP_CODE_KEY] = SmtClassifierStepCode.PIPELINE_MOVE_FORWARD.value

        return self._build_command_result(
            transition="inspection_ok",
            context_patch=context_patch,
            command=command,
            wait_token=f"conveyor_transfer_{ctx.session.id}",
        )

    def _handle_estop(self, ctx: "PluginContext", event_data: JsonDict) -> PluginResult:
        """处理急停事件"""
        ctx.logger.warning("Emergency stop pressed, pausing session")

        return PluginResult(
            transition="estop",
            context_patch={
                "stage": SmtClassifierStage.ERROR.value,
                "estop_pressed": True,
                "estop_timestamp": event_data.get("timestamp"),
                STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
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
        result_data = _ensure_dict(inbox.payload_json)
        command_type = resolve_first_str(result_data, RESULT_FIELD_ALIASES["command_type"]["aliases"])
        result = resolve_first_str(
            result_data,
            RESULT_FIELD_ALIASES["result"]["aliases"],
            default=SmtClassifierResultType.SUCCESS.value,
        )

        ctx.logger.info(f"SmtClassifierPlugin received command result: {command_type}, result={result}")

        # 获取当前会话上下文
        session_ctx = _ensure_dict(getattr(ctx.session, "context_json", None))
        current_step = resolve_step_from_context(session_ctx) or infer_step_from_command(command_type, session_ctx)
        ng_reason = _ensure_str(session_ctx.get("ng_reason"))
        current_location = session_ctx.get("current_location")
        session_location_id = current_location if isinstance(current_location, str) and current_location else None
        resolved_location_id = (
            resolve_first_str(result_data, EVENT_FIELD_ALIASES["location_id"]["aliases"]) or session_location_id
        )
        topology = _resolve_workline_topology(ctx)

        # 命令失败处理
        if result != SmtClassifierResultType.SUCCESS.value:
            # 递增重试计数
            retry_value = session_ctx.get("retry_count", 0)
            current_retry = retry_value if isinstance(retry_value, int) else 0
            return self._handle_command_failure(ctx, command_type, result_data, current_retry, current_step)

        # Step 驱动主流程（P0-6）
        if current_step == SmtClassifierStepCode.INPUT_PICK_PLACE and _has_embedded_inspection_payload(result_data):
            inspection_result = resolve_first_str(
                result_data,
                EVENT_FIELD_ALIASES["inspection_result"]["aliases"],
                default="OK",
            )
            reel_diameter = resolve_first_str(result_data, ("reel_diameter", "data.reel_diameter"))
            reel_thickness = resolve_first_str(result_data, ("reel_thickness", "data.reel_thickness"))
            return await self._process_inspection_result(
                ctx=ctx,
                inspection_result=inspection_result,
                topology=topology,
                location_id=resolved_location_id or "",
                context_patch={
                    "stage": SmtClassifierStage.INSPECTION_RESULT_RECEIVED.value,
                    "inspection_result": inspection_result,
                    "location_id": resolved_location_id,
                    **({"reel_diameter": reel_diameter} if reel_diameter else {}),
                    **({"thickness": reel_thickness} if reel_thickness else {}),
                    STEP_CODE_KEY: SmtClassifierStepCode.WAITING_INSPECTION_EVENT.value,
                },
            )

        if current_step is not None and current_step in {
            SmtClassifierStepCode.INPUT_PICK_PLACE,
            SmtClassifierStepCode.NG_PICK_PLACE,
            SmtClassifierStepCode.OUTPUT_PICK_PLACE,
        }:
            return self._handle_pick_place_completed(current_step, ng_reason, resolved_location_id)

        if current_step == SmtClassifierStepCode.PIPELINE_MOVE_FORWARD:
            return await self._handle_move_forward_completed(ctx, topology)

        # 兼容旧上下文字段（step_code 缺失时）
        if command_type == SmtClassifierCommandType.PICK_AND_PUT.value:
            inferred_step = infer_step_from_command(command_type, session_ctx)
            if inferred_step is not None:
                return self._handle_pick_place_completed(inferred_step, ng_reason, resolved_location_id)
            return self._handle_pick_place_completed(
                SmtClassifierStepCode.INPUT_PICK_PLACE, ng_reason, resolved_location_id
            )

        if command_type == SmtClassifierCommandType.MOVE_FORWARD.value:
            return await self._handle_move_forward_completed(ctx, topology)

        ctx.logger.warning(f"Unknown command type: {command_type}")
        return PluginResult()

    def _handle_pick_place_completed(
        self,
        current_step: SmtClassifierStepCode,
        ng_reason: str,
        location_id: str | None,
    ) -> PluginResult:
        """处理抓取放置完成"""
        if current_step == SmtClassifierStepCode.NG_PICK_PLACE:
            return PluginResult(
                transition="ng_handled",
                context_patch={
                    "stage": SmtClassifierStage.COMPLETED.value,
                    "ng_handled": True,
                    "ng_reason": ng_reason,
                    "location_id": location_id,
                    STEP_CODE_KEY: SmtClassifierStepCode.COMPLETED.value,
                },
                complete=True,
            )

        if current_step == SmtClassifierStepCode.OUTPUT_PICK_PLACE:
            return PluginResult(
                transition="output_handled",
                context_patch={
                    "stage": SmtClassifierStage.COMPLETED.value,
                    "location_id": location_id,
                    STEP_CODE_KEY: SmtClassifierStepCode.COMPLETED.value,
                },
                complete=True,
            )

        # 输入机械臂搬运完成后等待检测/后续事件
        return PluginResult(
            transition="pick_place_ok",
            context_patch={
                "stage": SmtClassifierStage.WAITING_INSPECTION.value,
                STEP_CODE_KEY: SmtClassifierStepCode.WAITING_INSPECTION_EVENT.value,
            },
        )

    async def _handle_move_forward_completed(self, ctx: "PluginContext", topology: WorklineTopology) -> PluginResult:
        """处理流水线传输完成。"""
        session_context = _ensure_dict(getattr(ctx.session, "context_json", None))
        output_arm_id = topology.output_arm_id
        if output_arm_id is None:
            return _missing_device_result(SmtClassifierDeviceRole.OUTPUT_ARM.value)

        source_location = _current_source_location(session_context)

        try:
            allocation_response, allocation_request_code, allocation_attempt = await self._request_bin_allocation(
                ctx,
                session_context,
                source_location=source_location,
            )
        except Exception as exc:
            ctx.logger.exception("Bin allocation request failed")
            return PluginResult(
                transition="command_failed",
                context_patch={
                    "stage": SmtClassifierStage.ERROR.value,
                    "allocation_error": str(exc),
                    STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                },
                failure=_create_failure(
                    domain="UPSTREAM",
                    code="BIN_ALLOCATION_REQUEST_FAILED",
                    message=str(exc),
                ),
            )

        allocation_message = _ensure_str(allocation_response.get("message")).upper()
        allocation_data = _ensure_dict(allocation_response.get("data"))
        allocation_status = _ensure_str(allocation_data.get("allocation_status"), allocation_message).upper()

        base_context_patch: JsonDict = {
            "current_location": source_location,
            "allocation_request_code": allocation_request_code,
            "allocation_request_count": allocation_attempt,
            "allocation_status": allocation_status,
        }

        if allocation_status == "ALLOCATED":
            target_bin, missing_fields = _normalize_target_bin(
                session_context,
                _ensure_dict(allocation_data.get("target_bin")),
            )
            if target_bin is None:
                return PluginResult(
                    transition="command_failed",
                    context_patch={
                        **base_context_patch,
                        "stage": SmtClassifierStage.ERROR.value,
                        "target_bin_missing_fields": missing_fields,
                        STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                    },
                    failure=_create_failure(
                        domain="UPSTREAM",
                        code="TARGET_BIN_INVALID",
                        message=f"target_bin missing required fields: {', '.join(missing_fields)}",
                    ),
                )

            return self._build_output_command_result(
                ctx=ctx,
                output_arm_id=output_arm_id,
                target_bin=target_bin,
                transition="conveyor_complete",
                extra_context_patch=base_context_patch,
            )

        if allocation_status == "AGV_REQUIRED":
            raw_agv_request = _ensure_dict(allocation_data.get("agv_request"))
            agv_request_code = _ensure_str(raw_agv_request.get("request_code"))
            raw_agv_request_count = session_context.get("agv_request_count", 0)
            agv_request_count = (
                raw_agv_request_count if isinstance(raw_agv_request_count, int) and raw_agv_request_count >= 0 else 0
            )
            if not agv_request_code:
                agv_request_code, agv_request_count = _next_request_code(
                    ctx,
                    session_context,
                    prefix="AGV",
                    counter_key="agv_request_count",
                )
                raw_agv_request["request_code"] = agv_request_code
            else:
                agv_request_count += 1

            try:
                agv_decision = self._build_agv_request_decision(
                    ctx,
                    session_context,
                    agv_request=raw_agv_request,
                    agv_request_code=agv_request_code,
                )
            except Exception as exc:
                return PluginResult(
                    transition="command_failed",
                    context_patch={
                        **base_context_patch,
                        "stage": SmtClassifierStage.ERROR.value,
                        "agv_request_error": str(exc),
                        STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                    },
                    failure=_create_failure(
                        domain="UPSTREAM",
                        code="AGV_REQUEST_PREPARE_FAILED",
                        message=str(exc),
                    ),
                )

            return PluginResult(
                transition="agv_requested",
                context_patch={
                    **base_context_patch,
                    "stage": SmtClassifierStage.WAITING_AGV_DELIVERY.value,
                    "agv_request_code": agv_request_code,
                    "agv_request_count": agv_request_count,
                    "agv_request": raw_agv_request,
                    STEP_CODE_KEY: SmtClassifierStepCode.WAITING_AGV_DELIVERY.value,
                },
                decisions=[agv_decision],
                wait=WaitIntent(
                    wait_type="EXTERNAL_HTTP",
                    wait_token=agv_request_code,
                    deadline_seconds=self.DEFAULT_TIMEOUT_SECONDS,
                ),
            )

        return PluginResult(
            transition="command_failed",
            context_patch={
                **base_context_patch,
                "stage": SmtClassifierStage.ERROR.value,
                STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
            },
            failure=_create_failure(
                domain="UPSTREAM",
                code="BIN_ALLOCATION_UNSUPPORTED_STATUS",
                message=f"unsupported allocation status: {allocation_status or allocation_message or 'UNKNOWN'}",
            ),
        )

    def _handle_command_failure(
        self,
        ctx: "PluginContext",
        command_type: str,
        result_data: JsonDict,
        retry_count: int = 0,
        current_step: SmtClassifierStepCode | None = None,
    ) -> PluginResult:
        """处理命令失败"""
        error_code = resolve_first_str(
            result_data,
            RESULT_FIELD_ALIASES["error_code"]["aliases"],
            default="UNKNOWN_ERROR",
        )
        error_message = resolve_first_str(
            result_data,
            RESULT_FIELD_ALIASES["error_message"]["aliases"],
            default="Command execution failed",
        )

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
                    **({STEP_CODE_KEY: current_step.value} if current_step is not None else {}),
                },
            )

        return PluginResult(
            transition="command_failed",
            context_patch={
                "stage": SmtClassifierStage.ERROR.value,
                "retry_count": new_retry_count,
                STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
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

        session_ctx = _ensure_dict(getattr(ctx.session, "context_json", None))
        current_stage = _ensure_str(session_ctx.get("stage"), SmtClassifierStage.IDLE.value)

        return PluginResult(
            transition="timeout",
            context_patch={
                "stage": SmtClassifierStage.ERROR.value,
                "timeout_at_stage": current_stage,
                STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
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
        callback_data = _ensure_dict(inbox.payload_json)
        callback_type = _ensure_str(callback_data.get("callback_type"))

        ctx.logger.info(f"SmtClassifierPlugin received external HTTP: type={callback_type}")

        # MES 检测结果回调
        if callback_type == "MES_INSPECTION_RESULT":
            return await self._handle_mes_inspection_callback(ctx, callback_data)

        if callback_type == _DEFAULT_AGV_CALLBACK_TYPE:
            return await self._handle_agv_task_result(ctx, callback_data)

        # WCS 任务状态回调
        if callback_type == "WCS_TASK_STATUS":
            return self._handle_wcs_task_callback(ctx, callback_data)

        ctx.logger.warning(f"Unknown external callback type: {callback_type}")
        return PluginResult()

    async def _handle_agv_task_result(self, ctx: "PluginContext", callback_data: JsonDict) -> PluginResult:
        """处理 AGV 搬运完成回调。"""
        session_context = _ensure_dict(getattr(ctx.session, "context_json", None))
        topology = _resolve_workline_topology(ctx)
        output_arm_id = topology.output_arm_id
        if output_arm_id is None:
            return _missing_device_result(SmtClassifierDeviceRole.OUTPUT_ARM.value)

        agv_result = _ensure_str(callback_data.get("result"), "FAILED").upper()
        callback_data_payload = _ensure_dict(callback_data.get("data"))
        command_id = _ensure_str(callback_data.get("command_code") or callback_data.get("command_id"))
        source_location = _ensure_str(callback_data_payload.get("to_location")) or _current_source_location(
            session_context
        )

        if agv_result != SmtClassifierResultType.SUCCESS.value:
            error_message = _ensure_str(
                callback_data_payload.get("message") or callback_data.get("message"),
                "AGV task failed",
            )
            return PluginResult(
                transition="command_failed",
                context_patch={
                    "stage": SmtClassifierStage.ERROR.value,
                    "agv_request_code": command_id or _ensure_str(session_context.get("agv_request_code")),
                    "agv_result": agv_result,
                    STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                },
                failure=_create_failure(
                    domain="UPSTREAM",
                    code="AGV_TASK_FAILED",
                    message=error_message,
                ),
            )

        try:
            allocation_response, allocation_request_code, allocation_attempt = await self._request_bin_allocation(
                ctx,
                session_context,
                source_location=source_location,
            )
        except Exception as exc:
            return PluginResult(
                transition="command_failed",
                context_patch={
                    "stage": SmtClassifierStage.ERROR.value,
                    "agv_request_code": command_id or _ensure_str(session_context.get("agv_request_code")),
                    "allocation_error": str(exc),
                    STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                },
                failure=_create_failure(
                    domain="UPSTREAM",
                    code="BIN_ALLOCATION_REQUEST_FAILED",
                    message=str(exc),
                ),
            )

        allocation_message = _ensure_str(allocation_response.get("message")).upper()
        allocation_data = _ensure_dict(allocation_response.get("data"))
        allocation_status = _ensure_str(allocation_data.get("allocation_status"), allocation_message).upper()
        if allocation_status != "ALLOCATED":
            return PluginResult(
                transition="command_failed",
                context_patch={
                    "stage": SmtClassifierStage.ERROR.value,
                    "agv_request_code": command_id or _ensure_str(session_context.get("agv_request_code")),
                    "allocation_request_code": allocation_request_code,
                    "allocation_request_count": allocation_attempt,
                    "allocation_status": allocation_status,
                    STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                },
                failure=_create_failure(
                    domain="UPSTREAM",
                    code="BIN_ALLOCATION_UNAVAILABLE_AFTER_AGV",
                    message=f"allocation status after AGV is {allocation_status or allocation_message or 'UNKNOWN'}",
                ),
            )

        target_bin, missing_fields = _normalize_target_bin(
            session_context, _ensure_dict(allocation_data.get("target_bin"))
        )
        if target_bin is None:
            return PluginResult(
                transition="command_failed",
                context_patch={
                    "stage": SmtClassifierStage.ERROR.value,
                    "target_bin_missing_fields": missing_fields,
                    "allocation_request_code": allocation_request_code,
                    "allocation_request_count": allocation_attempt,
                    STEP_CODE_KEY: SmtClassifierStepCode.ERROR.value,
                },
                failure=_create_failure(
                    domain="UPSTREAM",
                    code="TARGET_BIN_INVALID",
                    message=f"target_bin missing required fields: {', '.join(missing_fields)}",
                ),
            )

        return self._build_output_command_result(
            ctx=ctx,
            output_arm_id=output_arm_id,
            target_bin=target_bin,
            transition="agv_completed",
            extra_context_patch={
                "current_location": source_location,
                "agv_request_code": command_id or _ensure_str(session_context.get("agv_request_code")),
                "agv_result": agv_result,
                "allocation_request_code": allocation_request_code,
                "allocation_request_count": allocation_attempt,
                "allocation_status": allocation_status,
            },
        )

    async def _handle_mes_inspection_callback(self, ctx: "PluginContext", callback_data: JsonDict) -> PluginResult:
        """处理 MES 检测结果回调"""
        inspection_result = _ensure_str(callback_data.get("inspection_result"), "OK")
        barcode = _ensure_str(callback_data.get("barcode"))

        ctx.logger.info(f"MES inspection callback: barcode={barcode}, result={inspection_result}")

        session_ctx = _ensure_dict(getattr(ctx.session, "context_json", None))
        location_id = _ensure_str(session_ctx.get("location_id"))
        topology = _resolve_workline_topology(ctx)

        context_patch: JsonDict = {
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

    def _handle_wcs_task_callback(self, ctx: "PluginContext", callback_data: JsonDict) -> PluginResult:
        """处理 WCS 任务状态回调"""
        task_status = _ensure_str(callback_data.get("task_status"))
        task_id = _ensure_str(callback_data.get("task_id"))

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
            error_message = _ensure_str(callback_data.get("error_message"), "WCS task failed")
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
                STEP_CODE_KEY: SmtClassifierStepCode.COMPLETED.value,
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
    "SmtClassifierResultType",
    "SmtClassifierStage",
    "SmtClassifierStepCode",
    "get_error_recovery_strategy",
    "smt_classifier_plugin",
]
