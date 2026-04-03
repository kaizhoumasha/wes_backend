"""
OrchestratorService - 编排器核心服务

负责协调 Session 的处理流程：
1. 获取分布式锁
2. 加载并调用插件
3. 处理 PluginResult
4. 触发状态迁移
5. 派发命令到 Outbox

Phase 2 默认行为：
- 无插件时使用 NullPlugin
- 无状态机时允许所有迁移

设计参考: 设计文档 phase2-orchestrator
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from src.workline_runtime.enums import FailureDomain
from src.workline_runtime.lock import LockAcquireError
from src.workline_runtime.null_plugin import NullPlugin
from src.workline_runtime.plugin_context import PluginContext, PluginContextBuilder
from src.workline_runtime.transition_validator import TransitionValidator
from src.workline_runtime.types import CommandIntent, FailureIntent, PluginResult, WaitIntent
from src.workline_plugins.contracts import resolve_contract_version

logger = logging.getLogger(__name__)
JsonDict = dict[str, Any]


def _ensure_dict(value: Any) -> JsonDict:
    return cast("JsonDict", value) if isinstance(value, dict) else {}


@dataclass
class OrchestratorResult:
    """编排器处理结果

    Attributes:
        success: 是否成功
        error: 错误信息（失败时）
        transition: 触发的状态迁移
        decisions: 待派发的外部决策
        commands: 待派发的命令列表
        wait: 等待条件
        failure: 失败归因
        complete: 是否完成
        context_patch: 上下文更新
    """

    success: bool
    error: str | None = None
    transition: str | None = None
    decisions: list[dict[str, Any]] | None = None
    commands: list[CommandIntent] | None = None
    wait: WaitIntent | None = None
    failure: FailureIntent | None = None
    complete: bool = False
    context_patch: dict[str, Any] | None = None


class OrchestratorService:
    """编排器服务

    核心职责：
    - 协调 Session 处理流程
    - 管理分布式锁
    - 调用插件并处理结果
    - 验证状态迁移

    Attributes:
        validator: 状态迁移校验器
        lock_provider: 锁提供者函数（用于依赖注入）
    """

    def __init__(
        self,
        lock_provider: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
    ):
        """初始化编排器服务

        Args:
            lock_provider: 可选的锁提供者函数，用于测试注入。
                          接收锁 key，返回异步上下文管理器。
        """
        self.validator = TransitionValidator()
        self._lock_provider = lock_provider
        self.context_builder = PluginContextBuilder()

    @staticmethod
    def _resolve_session_pk(session: Any) -> int | None:
        """提取 Session 的真实整型主键。"""
        value = getattr(session, "id", None)
        if isinstance(value, bool):
            return None
        return value if isinstance(value, int) else None

    @staticmethod
    def _resolve_transition_state(session: Any, state_machine_class: type[Any] | None) -> str:
        """为插件状态机解析当前状态。

        有插件状态机时优先使用 session.context_json['stage']；
        否则退回通用 session.status。
        """

        if state_machine_class is not None:
            stage = _ensure_dict(getattr(session, "context_json", None)).get("stage")
            if isinstance(stage, str) and stage:
                return stage
            return "IDLE"

        status = getattr(session, "status", None)
        return status if isinstance(status, str) and status else ""

    def _get_lock(self, lock_key: str) -> AbstractAsyncContextManager[None]:
        """获取锁上下文管理器

        Args:
            lock_key: 锁的 key

        Returns:
            异步上下文管理器
        """
        if self._lock_provider:
            return self._lock_provider(lock_key)

        # 默认使用 RedisDistributedLock（需要外部注入 redis_client）
        # 这里返回一个空操作的上下文管理器作为 fallback
        @asynccontextmanager
        async def noop_lock():
            yield

        logger.warning("No lock provider configured, using noop lock. This is only suitable for testing.")
        return noop_lock()

    async def process_inbox(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        correlation_id: str,
    ) -> OrchestratorResult:
        """处理 Inbox 事件

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: WorklineInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 领域服务容器
            correlation_id: 关联 ID

        Returns:
            OrchestratorResult: 处理结果
        """
        session_id = self._resolve_session_pk(session)
        if session_id is None:
            return OrchestratorResult(success=False, error="Session missing primary key")

        lock_key = f"session:{session_id}"

        try:
            async with self._get_lock(lock_key):
                return await self._process_with_lock(
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    devices_by_role=devices_by_role,
                    services=services,
                    correlation_id=correlation_id,
                )
        except LockAcquireError:
            logger.exception(f"Failed to acquire lock for session {session_id}")
            return OrchestratorResult(success=False, error="Lock acquire failed")
        except Exception as e:
            logger.exception(f"Unexpected error processing inbox {inbox.id}")
            return OrchestratorResult(success=False, error=str(e))

    async def _process_with_lock(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        correlation_id: str,
    ) -> OrchestratorResult:
        """在锁保护下处理 Inbox

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: WorklineInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 领域服务容器
            correlation_id: 关联 ID

        Returns:
            OrchestratorResult: 处理结果
        """
        # 1. 加载插件
        plugin = self._load_plugin(getattr(workline, "plugin_class", None))

        # 2. 构建上下文
        session_id = self._resolve_session_pk(session)
        ctx = self.context_builder.build(
            session=session,
            workline=workline,
            devices_by_role=devices_by_role,
            services=services,
            correlation_id=correlation_id,
            logger=logging.getLogger(f"{__name__}.{session_id or 'unknown'}"),
        )

        # 2.5. 校验契约版本兼容性
        session_contract = getattr(session, "contract_version", None)
        plugin_contract = getattr(plugin, "contract_version", None) or resolve_contract_version(
            getattr(workline, "plugin_key", None)
        )
        if (
            isinstance(session_contract, str)
            and session_contract
            and isinstance(plugin_contract, str)
            and plugin_contract
            and session_contract != plugin_contract
        ):
            return OrchestratorResult(
                success=False,
                failure=FailureIntent(
                    domain=FailureDomain.SOFTWARE.value,
                    code="CONTRACT_MISMATCH",
                    message=f"Session contract {session_contract!r} != plugin {plugin_contract!r}",
                ),
            )

        # 3. 调用插件
        try:
            result = await self._call_plugin(plugin, ctx, inbox)
        except Exception as e:
            logger.exception("Plugin execution failed")
            return OrchestratorResult(success=False, error=str(e))

        # 4. 处理结果
        return self._process_result(result, session, getattr(workline, "state_machine_class", None))

    def _load_plugin(self, plugin_class: type[Any] | None) -> Any:
        """加载插件实例

        Args:
            plugin_class: 插件类（可选）

        Returns:
            插件实例（无插件时返回 NullPlugin）
        """
        if plugin_class is None:
            return NullPlugin()
        return plugin_class()

    async def _call_plugin(
        self,
        plugin: Any,
        ctx: PluginContext,
        inbox: Any,
    ) -> PluginResult:
        """调用插件处理事件

        Args:
            plugin: 插件实例
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            PluginResult: 插件返回结果
        """
        # 根据事件类型调用对应方法
        inbox_type = self._resolve_inbox_type(inbox)
        if inbox_type == "DEVICE_EVENT":
            return await plugin.on_device_event(ctx, inbox)
        if inbox_type == "COMMAND_RESULT":
            return await plugin.on_command_result(ctx, inbox)
        if inbox_type == "EXTERNAL_HTTP":
            return await plugin.on_external_http(ctx, inbox)
        if inbox_type == "TIMEOUT":
            return await plugin.on_timeout(ctx, inbox)
        if inbox_type == "MANUAL_OPERATION":
            return await plugin.on_manual_operation(ctx, inbox)
        # 默认调用 on_device_event
        return await plugin.on_device_event(ctx, inbox)

    def _resolve_inbox_type(self, inbox: Any) -> str:
        """根据真实 Inbox 模型字段推导插件分发类型。"""
        payload = getattr(inbox, "payload_json", {}) or {}
        explicit_type = payload.get("message_type")
        if isinstance(explicit_type, str):
            return explicit_type

        inbox_kind = getattr(inbox, "kind", None)
        if inbox_kind is not None:
            kind_value = getattr(inbox_kind, "value", inbox_kind)
            if kind_value == "COMMAND_RESULT":
                return "COMMAND_RESULT"
            if kind_value == "DEVICE_EVENT":
                return "DEVICE_EVENT"
            if kind_value == "EXTERNAL_HTTP":
                return "EXTERNAL_HTTP"
            if kind_value == "TIMER_TIMEOUT":
                return "TIMEOUT"
            if kind_value in {"MANUAL_HOLD", "MANUAL_RESUME", "MANUAL_CANCEL"}:
                return "MANUAL_OPERATION"

        return "DEVICE_EVENT"

    def _process_result(
        self,
        result: PluginResult,
        session: Any,
        state_machine_class: type[Any] | None,
    ) -> OrchestratorResult:
        """处理 PluginResult

        Args:
            result: 插件返回结果
            session: Session 实体
            state_machine_class: 状态机类

        Returns:
            OrchestratorResult: 编排器结果
        """
        # 1. 检查失败归因
        current_transition_state = self._resolve_transition_state(session, state_machine_class)

        # 1. 验证状态迁移
        if result.transition:
            is_valid, error = self.validator.validate(
                current_status=current_transition_state,
                transition=result.transition,
                state_machine_class=state_machine_class,
            )
            if not is_valid:
                logger.error(f"Invalid transition: {error}")
                return OrchestratorResult(success=False, error=error)

        # 2. failure 是业务归因，不代表编排处理失败
        if result.failure:
            logger.warning(f"Plugin returned failure intent: {result.failure}")

        # 3. 返回成功结果
        return OrchestratorResult(
            success=True,
            transition=result.transition,
            decisions=result.decisions if result.decisions else None,
            commands=result.commands if result.commands else None,
            wait=result.wait,
            failure=result.failure,
            complete=result.complete,
            context_patch=result.context_patch if result.context_patch else None,
        )


__all__ = ["OrchestratorResult", "OrchestratorService"]
