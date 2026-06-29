# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_base 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。
# 自引用 src.workline_runtime.{runtime_events, runtime_intent, utils}
# 已重定向到 C5a events_bridge + C5a runtime_intent + C2 src.app.workline.utils。

"""
插件开发框架 - 简化插件开发的核心基类和装饰器。

核心特性：
1. 自动路由：根据事件类型/命令类型自动路由到处理方法
2. 自动解析：Pydantic Model 自动验证 payload
3. handler 返回 RuntimeIntent 列表，由 Runtime 统一落地状态与副作用

示例：
    class MyPlugin(WorklinePlugin):
        plugin_key = "my_plugin"
        contract_version = "1.0"

        @on_event("SCAN_COMPLETED")
        async def handle_scan(
            self, ctx: PluginContext, event: ScanEventPayload
        ) -> list[RuntimeIntent]:
            return [ctx.next.command(action="PICK", destination_role="ARM")]
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from src.app.runtime.orchestration.events_bridge import assert_not_reserved_runtime_event
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.workline.utils import ensure_dict, non_empty_str
from src.core.logger import logger

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.app.workline.plugins.plugin_context import PluginContext


# ==================== 类型别名 ====================

# 插件处理方法签名类型
# 使用字符串注解避免前向引用问题
AsyncEventHandler = Callable[..., Any]
"""插件事件处理方法类型"""

AsyncCommandHandler = Callable[..., Any]
"""插件命令处理方法类型"""

AsyncTimeoutHandler = Callable[..., Any]
"""插件超时处理方法类型"""


ParsedModelT = TypeVar("ParsedModelT", bound=BaseModel)

_MANUAL_OPERATION_TRANSITION = {
    "HOLD": "manual_hold",
    "RESUME": "manual_resume",
    "CANCEL": "manual_cancel",
}
_MANUAL_KIND_OPERATION = {
    "MANUAL_HOLD": "HOLD",
    "MANUAL_RESUME": "RESUME",
    "MANUAL_CANCEL": "CANCEL",
}


def _inbox_kind_value(inbox: Any) -> str | None:
    """Extract kind.value from inbox object if present."""
    kind = getattr(inbox, "kind", None)
    value = getattr(kind, "value", kind)
    return value if isinstance(value, str) and value else None


def _resolve_manual_operation(inbox: Any) -> tuple[str | None, dict[str, Any]]:
    payload = ensure_dict(getattr(inbox, "payload_json", None))
    operation = non_empty_str(payload.get("operation"))
    if operation:
        return operation.upper(), payload

    kind_operation = _MANUAL_KIND_OPERATION.get(_inbox_kind_value(inbox) or "")
    return kind_operation, payload


# ==================== Payload 基类 ====================


class EventPayload(BaseModel):
    """事件 Payload 基类"""

    device_code: str


class CommandResultPayload(BaseModel):
    """命令结果 Payload 基类"""

    command_code: str
    result: str


# ==================== 标准化输入辅助 ====================


def _resolve_event_route_type(ctx: Any, payload: dict[str, Any]) -> str | None:
    """优先使用标准化 canonical_event_type，再回退原始 event_type。"""

    normalized_input = getattr(ctx, "normalized_input", None)
    canonical_event_type = non_empty_str(getattr(normalized_input, "canonical_event_type", None))
    if canonical_event_type:
        return canonical_event_type
    return non_empty_str(payload.get("event_type")) or non_empty_str(payload.get("message_type"))


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
        non_empty_str(getattr(normalized_input, "command_type", None))
        or non_empty_str(payload.get("command_type"))
        or non_empty_str(payload.get("task_type"))
    )
    if not command_type:
        return []

    source_result = non_empty_str(getattr(normalized_input, "source_result", None)) or non_empty_str(
        payload.get("result")
    )
    normalized_result = non_empty_str(getattr(normalized_input, "normalized_result", None))

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

    command_code = non_empty_str(getattr(result, "command_code", None))
    device_code = non_empty_str(getattr(result, "device_code", None))
    if command_code and device_code:
        return command_code, device_code
    return None


def resolve_normalized_command_failure(
    result: Any,
    *,
    default_code: str,
    default_message: str,
) -> tuple[str, str]:
    """从标准化命令结果中提取失败错误码与错误信息。

    这里只消费标准化后的规范字段 `error_detail.error_code` / `error_detail.error_message`。
    外部协议兼容（如白皮书 `code` / `msg`）应在标准化入口完成，不应继续扩散到插件运行时层。
    """

    error_detail = ensure_dict(getattr(result, "error_detail", None))
    error_code = non_empty_str(error_detail.get("error_code"))
    error_message = non_empty_str(error_detail.get("error_message"))
    return error_code or default_code, error_message or default_message


def try_parse_normalized_result_data(result: Any, model: type[ParsedModelT]) -> ParsedModelT | None:
    """尝试将标准化命令结果中的 `data` 解析为指定模型。"""

    data = getattr(result, "data", None)
    if not isinstance(data, dict) or not data:
        return None

    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def build_payload_invalid_block(message: str) -> RuntimeIntent:
    """构造标准 payload 缺失/非法时的 MATERIAL 阻塞意图。"""

    return RuntimeIntent.block(
        scope=BlockScope.MATERIAL,
        reason_code="PAYLOAD_INVALID",
        message=message,
        suggested_action="检查设备回调 payload",
    )


def payload_invalid_block_if_missing_envelope(result: Any, message: str) -> RuntimeIntent | None:
    """标准化命令结果缺少最小包络字段时，返回统一 payload 非法阻塞意图。"""

    if resolve_normalized_command_envelope(result) is None:
        return build_payload_invalid_block(message)
    return None


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
        from src.app.workline.plugins.plugin_sdk import normalize_inbox_input

        normalized_candidate = normalize_inbox_input(
            inbox,
            trace_id=non_empty_str(getattr(ctx, "trace_id", None)) or "",
        )
        if isinstance(normalized_candidate, param_type):
            return normalized_candidate
    except Exception as exc:
        # 标准化输入构建失败时，继续回退到原始 payload 解析。
        logger.debug(f"标准化 Inbox 输入失败，回退到原始 payload 解析: {exc}")

    return param_type.model_validate(_merge_handler_payload(payload))


def _normalize_handler_result(result: Any) -> list[RuntimeIntent]:
    """归一化插件 handler 返回值。"""

    if result is None:
        return []
    if isinstance(result, RuntimeIntent):
        return [result]
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        result_sequence = cast("Sequence[Any]", result)
        if all(isinstance(intent, RuntimeIntent) for intent in result_sequence):
            return list(cast("Sequence[RuntimeIntent]", result_sequence))
    raise TypeError("Plugin handler must return RuntimeIntent, list[RuntimeIntent], or None")


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
    assert_not_reserved_runtime_event(event_type, owner="@on_event", declaration_surface="@on_event")

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


# ==================== 插件基类 ====================


class WorklinePlugin:
    """
    插件基类 - 提供装饰器驱动的声明式开发

    子类只需要：
    1. 定义 plugin_key 和 contract_version
    2. 用 @on_event / @on_command 标记处理方法
    3. 返回 RuntimeIntent 或 RuntimeIntent 列表

    框架自动处理：
    - 事件路由（根据 payload 类型分发）
    - Payload 解析（Pydantic 自动验证）
    - 默认实现（on_manual_operation 等）
    """

    plugin_key: str = "base"
    contract_version: str = "1.0"

    def __init_subclass__(cls) -> None:
        """子类初始化时建立路由表"""
        cls._event_handlers: dict[str, Callable[..., Any]] = {}
        cls._command_handlers: dict[tuple[str, str | None], Callable[..., Any]] = {}

        for _, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if hasattr(method, "_event_type"):
                assert_not_reserved_runtime_event(
                    method._event_type,  # type: ignore[attr-defined]
                    owner=f"{cls.__name__}.{method.__name__}",
                    declaration_surface="@on_event",
                )
                cls._event_handlers[method._event_type] = method  # type: ignore[attr-defined]
            if hasattr(method, "_command_type"):
                key = (method._command_type, method._command_result)  # type: ignore[attr-defined]
                cls._command_handlers[key] = method

    async def on_device_event(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """设备事件处理 - 优先按标准化 canonical_event_type 路由。"""
        payload = inbox.payload_json or {}
        event_type = _resolve_event_route_type(ctx, payload)

        if event_type and event_type in self._event_handlers:
            handler = self._event_handlers[event_type]
            return await self._invoke_handler(handler, ctx, inbox, payload)

        ctx.logger.warning(f"No handler for event_type={event_type}")
        return []

    async def on_command_result(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """命令结果处理 - 兼容原始结果与标准化结果语义。"""
        payload = inbox.payload_json or {}
        route_keys = _command_result_route_keys(ctx, payload)

        if not route_keys:
            ctx.logger.warning("No command_type in payload")
            return []

        for key in route_keys:
            if key in self._command_handlers:
                handler = self._command_handlers[key]
                return await self._invoke_handler(handler, ctx, inbox, payload)

        command_type = route_keys[0][0]
        result = non_empty_str(payload.get("result"))
        ctx.logger.warning(f"No handler for command_type={command_type}, result={result}")
        return []

    async def on_external_http(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """外部 HTTP 回调 - 默认空实现"""
        ctx.logger.info(f"Received external HTTP: {inbox.id}")
        return []

    async def on_manual_operation(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """人工操作由 Runtime 服务处理，插件默认不产生业务意图。"""
        ctx.logger.info(f"Received manual operation: {inbox.id}")
        operation, _payload = _resolve_manual_operation(inbox)
        if operation not in _MANUAL_OPERATION_TRANSITION:
            ctx.logger.warning(f"Unsupported manual operation: {operation}")
        return []

    async def _invoke_handler(
        self,
        handler: Callable[..., Any],
        ctx: PluginContext,
        inbox: WorklineInbox,
        payload: dict[str, Any],
    ) -> list[RuntimeIntent]:
        """调用处理方法（支持 Pydantic 自动解析）。"""
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
                    return [build_payload_invalid_block(f"Payload validation error: {e}")]
            else:
                args.append(inbox)

        # ========== 调用业务逻辑 ==========
        result = await handler(*args)
        return _normalize_handler_result(result)


__all__ = [
    "CommandResultPayload",
    "EventPayload",
    "WorklinePlugin",
    "build_payload_invalid_block",
    "on_command",
    "on_event",
    "payload_invalid_block_if_missing_envelope",
    "resolve_normalized_command_envelope",
    "resolve_normalized_command_failure",
    "try_parse_normalized_result_data",
]
