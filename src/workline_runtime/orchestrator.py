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

from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from src.workline_runtime.diagnostics import ErrorCode, ErrorDomain
from src.workline_runtime.enums import FailureCode, FailureDomain
from src.workline_runtime.lock import LockAcquireError
from src.workline_runtime.null_plugin import NullPlugin
from src.workline_runtime.plugin_context import PluginContext, PluginContextBuilder
from src.workline_runtime.transition_validator import TransitionValidator
from src.workline_runtime.types import CommandIntent, FailureIntent, PluginResult, WaitIntent
from src.workline_runtime.utils import ensure_dict

# 类型注解用（运行时需要这些类型作为函数签名）
if TYPE_CHECKING:
    from collections.abc import Callable

    from src.app.workline.models import WorkLine
    from src.app.workline.models.inbox import WorklineInbox
    from src.app.workline.models.session import WorklineSession


class LockStage(str, Enum):
    """锁阶段枚举

    用于支持细粒度锁策略：
    - READ: 读取阶段，多个读取可并发
    - WRITE: 写入阶段，独占锁
    """

    READ = "read"
    WRITE = "write"


logger = logging.getLogger(__name__)

# 插件实例缓存：避免每次处理都新建实例
# key: plugin_class, value: plugin_instance
_plugin_instance_cache: dict[type, Any] = {}

_INBOX_KIND_TO_PLUGIN_TYPE = {
    "COMMAND_RESULT": "COMMAND_RESULT",
    "DEVICE_EVENT": "DEVICE_EVENT",
    "EXTERNAL_HTTP": "EXTERNAL_HTTP",
    "TIMER_TIMEOUT": "TIMEOUT",
    "MANUAL_HOLD": "MANUAL_OPERATION",
    "MANUAL_RESUME": "MANUAL_OPERATION",
    "MANUAL_CANCEL": "MANUAL_OPERATION",
}


def _ensure_non_empty_str(value: Any) -> str | None:
    """Return value if it's a non-empty string, otherwise None."""
    return value if isinstance(value, str) and value else None


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
    error_code: str | None = None
    error_domain: str | None = None
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
            stage = ensure_dict(getattr(session, "context_json", None)).get("stage")
            if isinstance(stage, str) and stage:
                return stage
            return "IDLE"

        status = getattr(session, "status", None)
        return status if isinstance(status, str) and status else ""

    def _get_lock(self, lock_key: str, stage: LockStage = LockStage.WRITE) -> AbstractAsyncContextManager[None]:
        """获取锁上下文管理器

        Args:
            lock_key: 锁的 key
            stage: 锁阶段（READ 或 WRITE）

        Returns:
            异步上下文管理器
        """
        # 组合锁 key 和阶段
        full_lock_key = f"{lock_key}:{stage.value}"

        if self._lock_provider:
            return self._lock_provider(full_lock_key)

        # 默认使用 RedisDistributedLock（需要外部注入 redis_client）
        # 这里返回一个空操作的上下文管理器作为 fallback
        @asynccontextmanager
        async def noop_lock():
            yield

        logger.warning("No lock provider configured, using noop lock. This is only suitable for testing.")
        return noop_lock()

    async def process_inbox(
        self,
        session: WorklineSession | None,
        workline: WorkLine | None,
        inbox: WorklineInbox | None,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        correlation_id: str,
    ) -> OrchestratorResult:
        """处理 Inbox 事件（两阶段锁）

        阶段 1 (READ): 加载插件、构建上下文、契约检测
        阶段 2 (WRITE): 调用插件、处理结果、状态迁移

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
            return OrchestratorResult(
                success=False,
                error="Session missing primary key",
                error_code=ErrorCode.SESSION_CONTEXT_MISSING.value,
                error_domain=ErrorDomain.WORKFLOW.value,
            )

        lock_key = f"session:{session_id}"

        # 阶段 1: READ（可并发）
        try:
            async with self._get_lock(lock_key, LockStage.READ):
                # 读取阶段：加载插件、构建上下文、契约检测
                result = await self._process_read_phase(
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    devices_by_role=devices_by_role,
                    services=services,
                    correlation_id=correlation_id,
                )

                # 如果读取阶段失败，直接返回
                if not result.success:
                    return result

            # 阶段 2: WRITE（独占）
            # 注意：当前实现中，实际的状态写入在 Celery 任务的 _apply_orchestrator_effects 中完成
            # 该任务不在编排器锁保护下。这是简化设计，代价是并发写入冲突时可能需要重试
            async with self._get_lock(lock_key, LockStage.WRITE):
                logger.debug(f"Session {session_id} 获得 WRITE 锁")
                return await self._process_write_phase(
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    devices_by_role=devices_by_role,
                    services=services,
                    correlation_id=correlation_id,
                    read_result=result,
                )

        except LockAcquireError:
            logger.exception(f"Failed to acquire lock for session {session_id}")
            return OrchestratorResult(
                success=False,
                error="Lock acquire failed",
                error_code=ErrorCode.UNKNOWN.value,
                error_domain=ErrorDomain.SYSTEM.value,
            )
        except Exception as e:
            inbox_id = getattr(inbox, "id", "unknown") if inbox else "unknown"
            logger.exception(f"Unexpected error processing inbox {inbox_id}")
            return OrchestratorResult(
                success=False,
                error=str(e),
                error_code=ErrorCode.UNKNOWN.value,
                error_domain=ErrorDomain.SYSTEM.value,
            )

    async def _process_read_phase(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        correlation_id: str,
    ) -> OrchestratorResult:
        """阶段 1: READ - 读取阶段（可并发）

        执行：
        - 加载插件
        - 构建上下文
        - 契约版本检测

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
        plugin = self._load_plugin(getattr(workline, "plugin_class", None))

        session_id = self._resolve_session_pk(session)
        ctx = self.context_builder.build(
            session=session,
            workline=workline,
            devices_by_role=devices_by_role,
            services=services,
            correlation_id=correlation_id,
            logger=logging.getLogger(f"{__name__}.{session_id or 'unknown'}"),
            inbox=inbox,
        )

        session_contract = _ensure_non_empty_str(getattr(session, "contract_version", None))
        plugin_contract = _ensure_non_empty_str(getattr(plugin, "contract_version", None))
        if session_contract and plugin_contract and session_contract != plugin_contract:
            return OrchestratorResult(
                success=False,
                error=f"Session contract {session_contract!r} != plugin {plugin_contract!r}",
                error_code=ErrorCode.CONTRACT_MISMATCH.value,
                error_domain=ErrorDomain.CONFIG.value,
                failure=FailureIntent(
                    domain=FailureDomain.SOFTWARE.value,
                    code=FailureCode.CONTRACT_MISMATCH,
                    message=f"Session contract {session_contract!r} != plugin {plugin_contract!r}",
                ),
            )

        try:
            result = await self._call_plugin(plugin, ctx, inbox)
        except Exception as e:
            logger.exception("Plugin execution failed")
            return OrchestratorResult(
                success=False,
                error=str(e),
                error_code=ErrorCode.PLUGIN_EXECUTION_FAILED.value,
                error_domain=ErrorDomain.PLUGIN.value,
            )

        return self._process_result(result, session, getattr(workline, "state_machine_class", None))

    async def _process_write_phase(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        correlation_id: str,
        read_result: OrchestratorResult,
    ) -> OrchestratorResult:
        """阶段 2: WRITE - 写入阶段（独占）

        执行：
        - 状态迁移验证
        - 结果返回（供 Celery 任务使用）

        注意：实际的状态修改在 Celery 任务的 _apply_orchestrator_effects 中完成，
        不在编排器锁保护下。这是简化设计，依赖 Celery 重试机制处理并发冲突。

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: WorklineInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 领域服务容器
            correlation_id: 关联 ID
            read_result: 读取阶段的结果

        Returns:
            OrchestratorResult: 处理结果
        """
        session_id = self._resolve_session_pk(session)
        logger.debug(f"WRITE 阶段开始 for session {session_id}, transition={read_result.transition}")

        # 当前实现：直接返回读取阶段的结果
        # 状态修改在 Celery 任务 _apply_orchestrator_effects 中完成（不在锁保护下）
        # 占位参数避免 IDE/ruff 警告
        _ = session, workline, inbox, devices_by_role, services, correlation_id

        logger.debug(f"WRITE 阶段完成 for session {session_id}")
        return read_result

    def _load_plugin(self, plugin_class: type[Any] | None) -> Any:
        """加载插件实例（带缓存）

        优先使用缓存的实例，避免每次处理都新建实例。
        NullPlugin 是单例，无需缓存。

        Args:
            plugin_class: 插件类（可选）

        Returns:
            插件实例（无插件时返回 NullPlugin 单例）
        """
        if plugin_class is None:
            return NullPlugin()

        # 使用缓存的实例
        if plugin_class not in _plugin_instance_cache:
            _plugin_instance_cache[plugin_class] = plugin_class()
            logger.debug(f"插件实例已缓存: {plugin_class.__name__}")

        return _plugin_instance_cache[plugin_class]

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
        payload = ensure_dict(getattr(inbox, "payload_json", None))
        explicit_type = payload.get("message_type")
        if isinstance(explicit_type, str):
            return explicit_type

        inbox_kind = getattr(inbox, "kind", None)
        if inbox_kind is not None:
            kind_value = getattr(inbox_kind, "value", inbox_kind)
            plugin_type = _INBOX_KIND_TO_PLUGIN_TYPE.get(kind_value)
            if plugin_type:
                return plugin_type

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
        current_transition_state = self._resolve_transition_state(session, state_machine_class)

        if result.transition:
            is_valid, error = self.validator.validate(
                current_status=current_transition_state,
                transition=result.transition,
                state_machine_class=state_machine_class,
            )
            if not is_valid:
                logger.error(f"Invalid transition: {error}")
                return OrchestratorResult(
                    success=False,
                    error=error,
                    error_code=ErrorCode.PLUGIN_TRANSITION_INVALID.value,
                    error_domain=ErrorDomain.PLUGIN.value,
                )

        if result.failure:
            logger.warning(f"Plugin returned failure intent: {result.failure}")

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
