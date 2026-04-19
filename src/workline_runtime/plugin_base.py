"""
插件开发框架 - 简化插件开发的核心基类和装饰器

提供装饰器驱动的声明式插件开发模式，减少样板代码 70%+。

核心特性：
1. 自动路由：根据事件类型/命令类型自动路由到处理方法
2. 自动解析：Pydantic Model 自动验证 payload
3. 状态机集成：声明式状态迁移
4. 响应构建器：链式调用简化 PluginResult 构建

示例：
    class MyPlugin(WorklinePlugin):
        plugin_key = "my_plugin"
        contract_version = "1.0"

        @on_event("SCAN_COMPLETED")
        @step("IDLE", "WAITING_INSPECTION")
        async def handle_scan(
            self, ctx: PluginContext, event: ScanEventPayload
        ) -> PluginResult:
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ok")
                .command(device_role="ARM", command_type="PICK")
                .build()
            )
"""

from __future__ import annotations

import inspect
import typing
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.workline_runtime.types import (
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)
from src.workline_runtime.utils import ensure_dict

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


# ==================== 类型别名 ====================

# 插件处理方法签名类型
# 使用字符串注解避免前向引用问题
AsyncEventHandler = Callable[..., Any]
"""插件事件处理方法类型"""

AsyncCommandHandler = Callable[..., Any]
"""插件命令处理方法类型"""

AsyncTimeoutHandler = Callable[..., Any]
"""插件超时处理方法类型"""


# ==================== Payload 基类 ====================


class EventPayload(BaseModel):
    """事件 Payload 基类"""

    device_code: str


class CommandResultPayload(BaseModel):
    """命令结果 Payload 基类"""

    command_code: str
    result: str


# ==================== 标准化输入辅助 ====================


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _resolve_event_route_type(ctx: Any, payload: dict[str, Any]) -> str | None:
    """优先使用标准化 canonical_event_type，再回退原始 event_type。"""

    normalized_input = getattr(ctx, "normalized_input", None)
    canonical_event_type = _non_empty_str(getattr(normalized_input, "canonical_event_type", None))
    if canonical_event_type:
        return canonical_event_type
    return _non_empty_str(payload.get("event_type")) or _non_empty_str(payload.get("message_type"))


def _command_result_route_keys(ctx: Any, payload: dict[str, Any]) -> list[tuple[str, str | None]]:
    """生成命令结果分发候选键。

    顺序体现兼容策略：
    1. 原始供应商结果（保持现有插件行为）
    2. 标准化结果（便于新插件直接按统一语义编写）
    3. 标准化失败语义回落到 legacy FAILED（兼容旧插件）
    4. 无 result 限定兜底
    """

    normalized_input = getattr(ctx, "normalized_input", None)
    command_type = (
        _non_empty_str(getattr(normalized_input, "command_type", None))
        or _non_empty_str(payload.get("command_type"))
        or _non_empty_str(payload.get("task_type"))
    )
    if not command_type:
        return []

    source_result = _non_empty_str(getattr(normalized_input, "source_result", None)) or _non_empty_str(
        payload.get("result")
    )
    normalized_result = _non_empty_str(getattr(normalized_input, "normalized_result", None))

    keys: list[tuple[str, str | None]] = []

    def _append(result_value: str | None) -> None:
        key = (command_type, result_value)
        if key not in keys:
            keys.append(key)

    _append(source_result)
    _append(normalized_result)

    if normalized_result in {"TERMINAL_FAILURE", "RETRYABLE_FAILURE"}:
        _append("FAILED")

    _append(None)
    return keys


# 下面两个 helper 属于“标准化 runtime 结构规则”，适合放在 plugin_base 复用：
# - 它们只解析 NormalizedCommandResult 的公共结构，不携带任何插件业务语义
# - 只有这类跨插件稳定成立的规则才应该上移到基类
# - 具体业务错误文案、状态机分支、data 解析仍应留在各自插件内部


def resolve_normalized_command_envelope(result: Any) -> tuple[str, str] | None:
    """解析标准化命令结果的最小包络字段。"""

    command_code = _non_empty_str(getattr(result, "command_code", None))
    device_code = _non_empty_str(getattr(result, "device_code", None))
    if command_code and device_code:
        return command_code, device_code
    return None


def resolve_normalized_command_failure(
    result: Any,
    *,
    default_code: str,
    default_message: str,
) -> tuple[str, str]:
    """从标准化命令结果中提取失败错误码与错误信息。"""

    payload = ensure_dict(getattr(result, "payload", None))
    error_detail = ensure_dict(getattr(result, "error_detail", None))
    error_code = _non_empty_str(error_detail.get("error_code")) or _non_empty_str(payload.get("error_code"))
    error_message = _non_empty_str(error_detail.get("error_message")) or _non_empty_str(payload.get("error_message"))
    return error_code or default_code, error_message or default_message


def _merge_handler_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """合并 typed handler 常用的顶层与嵌套 payload 结构。"""

    merged_payload: dict[str, Any] = dict(payload)
    for nested_key in ("data", "error_detail"):
        nested_payload = ensure_dict(payload.get(nested_key))
        if nested_payload:
            merged_payload.update(nested_payload)
    return merged_payload


def _resolve_handler_param_type(
    handler: Callable[..., Any],
    *,
    param_name: str,
    annotation: Any,
) -> Any:
    """解析 handler 参数类型，兼容字符串前向引用。"""

    if not isinstance(annotation, str):
        return annotation

    try:
        handler_module = inspect.getmodule(handler)
        local_ns = getattr(handler_module, "__dict__", {}) if handler_module else {}
        type_hints = typing.get_type_hints(handler, localns=local_ns)
        return type_hints.get(param_name, annotation)
    except Exception:
        # get_type_hints 失败（通常是 TYPE_CHECKING 块内的类型）
        # 尝试从 handler 的全局命名空间直接查找
        return handler.__globals__.get(annotation, annotation)


def _resolve_handler_model_arg(
    ctx: Any,
    inbox: Any,
    payload: dict[str, Any],
    param_type: type[BaseModel],
) -> BaseModel:
    """优先将标准化输入注入给 typed handler，其次回退原始 payload 解析。

    这样可以让现有装饰器插件逐步迁移到标准化输入模型，而不需要一次性重写整套插件框架。
    即使测试里没有显式构造 `ctx.normalized_input`，也尽量按 inbox 自动补一份标准化输入，
    保持 typed handler 的调用行为稳定。
    """

    normalized_input = getattr(ctx, "normalized_input", None)
    if isinstance(normalized_input, param_type):
        return normalized_input

    try:
        from src.workline_runtime.plugin_sdk import normalize_inbox_input

        normalized_candidate = normalize_inbox_input(
            inbox,
            correlation_id=_non_empty_str(getattr(ctx, "correlation_id", None)) or "",
        )
        if isinstance(normalized_candidate, param_type):
            return normalized_candidate
    except Exception:
        # 标准化输入构建失败时，继续回退到原始 payload 解析。
        pass

    return param_type.model_validate(_merge_handler_payload(payload))


# ==================== 装饰器 ====================


def on_event(event_type: str) -> Callable[..., Any]:
    """
    标记方法处理特定事件类型

    Args:
        event_type: 事件类型（如 "SCAN_COMPLETED"）

    Example:
        @on_event("SCAN_COMPLETED")
        async def handle_scan(self, ctx, event: ScanEventPayload):
            ...
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        method._event_type = event_type  # type: ignore[attr-defined]
        return method

    return decorator


def on_command(command_type: str, result: str | None = None) -> Callable[..., Any]:
    """
    标记方法处理特定命令结果

    Args:
        command_type: 命令类型（如 "PICK_AND_PUT"）
        result: 命令结果过滤（如 "SUCCESS", "FAILED"）

    Example:
        @on_command("PICK_AND_PUT", result="SUCCESS")
        async def handle_pick_success(self, ctx, result: CommandResultPayload):
            ...
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        method._command_type = command_type  # type: ignore[attr-defined]
        method._command_result = result  # type: ignore[attr-defined]
        return method

    return decorator


def on_timeout() -> Callable[..., Any]:
    """
    标记方法处理超时事件

    Example:
        @on_timeout()
        async def handle_timeout(self, ctx, inbox):
            ...
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        method._is_timeout_handler = True  # type: ignore[attr-defined]
        return method

    return decorator


def step(expected: str | None = None, target: str | None = None) -> Callable[..., Any]:
    """
    声明状态迁移

    Args:
        expected: 期望的前置状态（None 表示任意状态）
        target: 目标状态（None 表示保持当前状态）

    Example:
        @step("IDLE", "WAITING_INSPECTION")
        async def handle_scan(self, ctx, event):
            ...
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        method._expected_step = expected  # type: ignore[attr-defined]
        method._target_step = target  # type: ignore[attr-defined]
        return method

    return decorator


# ==================== 响应构建器 ====================


@dataclass
class PluginResultBuilder:
    """
    插件结果构建器 - 链式调用简化 PluginResult 构建

    Example:
        result = (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(device_role="ARM", command_type="PICK")
            .wait(event_type="INSPECTION_COMPLETED", timeout_seconds=300)
            .failure(domain="HARDWARE", code="TIMEOUT", message="超时")
            .context({"last_scan": "ABC123"})
            .build()
        )
    """

    ctx: PluginContext

    def __post_init__(self):
        self._transition: str | None = None
        self._commands: list[CommandIntent] = []
        self._wait: WaitIntent | None = None
        self._failure: FailureIntent | None = None
        self._complete: bool = False
        self._context_patch: dict[str, Any] = {}

    def transition(self, name: str) -> PluginResultBuilder:
        """设置状态迁移"""
        self._transition = name
        return self

    def command(
        self,
        device_role: str,
        command_type: str,
        parameters: dict[str, Any] | None = None,
    ) -> PluginResultBuilder:
        """添加命令

        Args:
            device_role: 设备角色（如 "INPUT_ARM"），框架自动解析为设备ID
            command_type: 命令类型（如 "PICK_AND_PUT"）
            parameters: 命令参数
        """
        # 从 ctx.devices_by_role 解析设备ID
        devices_by_role = getattr(self.ctx, "devices_by_role", {})
        devices = devices_by_role.get(device_role, [])

        if not devices:
            raise ValueError(
                f"Device role '{device_role}' not found in devices_by_role. "
                f"Available roles: {list(devices_by_role.keys())}"
            )

        # 取第一个设备（可通过 role_index 排序选择）
        device = devices[0]
        target_device_id = getattr(device, "id", None)

        if target_device_id is None:
            raise ValueError(f"Device {device} has no 'id' attribute")

        self._commands.append(
            CommandIntent(
                target_device_id=target_device_id,
                action=command_type,
                parameters=parameters or {},
            )
        )
        return self

    def wait(
        self,
        event_type: str | None = None,
        timeout_seconds: int | None = None,
    ) -> PluginResultBuilder:
        """设置等待条件

        Args:
            event_type: 等待的事件类型（用于生成 wait_token）
            timeout_seconds: 超时秒数
        """
        # 生成 wait_token（用于回调匹配）
        session_id = getattr(self.ctx.session, "id", "unknown")
        wait_token = f"{session_id}-{event_type}-{uuid.uuid4().hex[:8]}"

        self._wait = WaitIntent(
            wait_type="COMMAND_RESULT",  # 固定为命令结果等待
            wait_token=wait_token,
            deadline_seconds=timeout_seconds or 300,  # 默认5分钟
        )
        return self

    def failure(
        self,
        domain: str,
        code: str,
        message: str,
    ) -> PluginResultBuilder:
        """设置失败归因"""
        self._failure = FailureIntent(
            domain=domain,
            code=code,
            message=message,
        )
        return self

    def complete(self) -> PluginResultBuilder:
        """标记完成"""
        self._complete = True
        return self

    def context(self, patch: dict[str, Any]) -> PluginResultBuilder:
        """更新上下文"""
        self._context_patch.update(patch)
        return self

    def build(self) -> PluginResult:
        """构建结果"""
        result = PluginResult()
        result.transition = self._transition
        result.commands = self._commands
        result.wait = self._wait
        result.failure = self._failure
        result.complete = self._complete
        result.context_patch = self._context_patch
        return result


# ==================== 插件基类 ====================


class WorklinePlugin:
    """
    插件基类 - 提供装饰器驱动的声明式开发

    子类只需要：
    1. 定义 plugin_key 和 contract_version
    2. 用 @on_event / @on_command 标记处理方法
    3. 用 @step 声明状态迁移
    4. 用 PluginResultBuilder 构建响应

    框架自动处理：
    - 事件路由（根据 payload 类型分发）
    - Payload 解析（Pydantic 自动验证）
    - 状态迁移校验（前置状态检查）
    - 默认实现（on_timeout, on_manual_operation 等）
    """

    plugin_key: str = "base"
    contract_version: str = "1.0"

    def __init_subclass__(cls) -> None:
        """子类初始化时建立路由表"""
        cls._event_handlers: dict[str, Callable[..., Any]] = {}
        cls._command_handlers: dict[tuple[str, str | None], Callable[..., Any]] = {}
        cls._timeout_handler: Callable[..., Any] | None = None

        for _, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if hasattr(method, "_event_type"):
                cls._event_handlers[method._event_type] = method  # type: ignore[attr-defined]
            if hasattr(method, "_command_type"):
                key = (method._command_type, method._command_result)  # type: ignore[attr-defined]
                cls._command_handlers[key] = method
            if getattr(method, "_is_timeout_handler", False):
                cls._timeout_handler = method

    async def on_device_event(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        """设备事件处理 - 优先按标准化 canonical_event_type 路由。"""
        payload = inbox.payload_json or {}
        event_type = _resolve_event_route_type(ctx, payload)

        if event_type and event_type in self._event_handlers:
            handler = self._event_handlers[event_type]
            return await self._invoke_handler(handler, ctx, inbox, payload)

        ctx.logger.warning(f"No handler for event_type={event_type}")
        return PluginResult()

    async def on_command_result(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        """命令结果处理 - 兼容原始结果与标准化结果语义。"""
        payload = inbox.payload_json or {}
        route_keys = _command_result_route_keys(ctx, payload)

        if not route_keys:
            ctx.logger.warning("No command_type in payload")
            return PluginResult()

        for key in route_keys:
            if key in self._command_handlers:
                handler = self._command_handlers[key]
                return await self._invoke_handler(handler, ctx, inbox, payload)

        command_type = route_keys[0][0]
        result = _non_empty_str(payload.get("result"))
        ctx.logger.warning(f"No handler for command_type={command_type}, result={result}")
        return PluginResult()

    async def on_timeout(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        """超时处理 - 调用标记的方法或返回默认"""
        handler = type(self)._timeout_handler
        if handler:
            return await self._invoke_handler(handler, ctx, inbox, inbox.payload_json or {})
        return PluginResult()

    async def on_external_http(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        """外部 HTTP 回调 - 默认空实现"""
        ctx.logger.info(f"Received external HTTP: {inbox.id}")
        return PluginResult()

    async def on_manual_operation(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        """人工操作 - 默认空实现"""
        ctx.logger.info(f"Received manual operation: {inbox.id}")
        return PluginResult()

    async def _invoke_handler(
        self,
        handler: Callable[..., Any],
        ctx: PluginContext,
        inbox: WorklineInbox,
        payload: dict[str, Any],
    ) -> PluginResult:
        """调用处理方法（支持 Pydantic 自动解析 + 状态校验）"""
        # ========== 前置：状态校验 ==========
        expected_step = getattr(handler, "_expected_step", None)
        if expected_step:
            current_step = ctx.session.context_json.get("step_code")
            if current_step != expected_step:
                ctx.logger.error(f"State mismatch: expected {expected_step}, got {current_step}")
                return PluginResult(
                    failure=FailureIntent(
                        domain="SOFTWARE",
                        code="STATE_MISMATCH",
                        message=f"Expected state {expected_step}, current is {current_step}",
                    )
                )

        # ========== 参数解析 ==========
        sig = inspect.signature(handler)
        params = list(sig.parameters.values())

        # 参数注入：self, ctx, payload/event
        args: list[Any] = [self, ctx]

        if len(params) >= 3:
            param = params[2]
            param_type = _resolve_handler_param_type(handler, param_name=param.name, annotation=param.annotation)
            if param_type and inspect.isclass(param_type) and issubclass(param_type, BaseModel):
                try:
                    args.append(_resolve_handler_model_arg(ctx, inbox, payload, param_type))
                except Exception as e:
                    ctx.logger.exception("Payload validation failed")
                    return PluginResult(
                        failure=FailureIntent(
                            domain="DATA",
                            code="PAYLOAD_INVALID",
                            message=f"Payload validation error: {e}",
                        )
                    )
            else:
                args.append(inbox)

        # ========== 调用业务逻辑 ==========
        result = await handler(*args)
        if not isinstance(result, PluginResult):
            result = PluginResult()

        # ========== 后置：目标状态设置 ==========
        target_step = getattr(handler, "_target_step", None)
        if target_step:
            # 自动添加 step_code 到 context_patch
            result.context_patch["step_code"] = target_step
            # 如果没有显式设置 transition，则用 target_step 作为 transition
            if not result.transition:
                result.transition = target_step

        return result


__all__ = [
    "CommandResultPayload",
    "EventPayload",
    "PluginResultBuilder",
    "WorklinePlugin",
    "on_command",
    "on_event",
    "on_timeout",
    "resolve_normalized_command_envelope",
    "resolve_normalized_command_failure",
    "step",
]
