"""未绑定迁移会话的通用编排锁与写回边界。

带 immutable binding 的会话不会进入本模块，始终由 generated plugin
dispatcher 执行。迁移期业务映射位于 Workline Plugin 兼容边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.diagnostics import ErrorCode, error_domain_for
from src.app.runtime.orchestration.lock_bridge import LockAcquireError
from src.app.workline.trace_context import TraceContext
from src.core.logger import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from src.app.runtime.orchestration.models.session import WorklineSession
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.workline.models import WorkLine
    from src.app.workline.runtime_services import WorklineRuntimeServices


_ALLOW_NULL_PLUGIN = False
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        "awaiting_device_command_code",
        "current_device_id",
        "current_device_role",
        "current_wait_type",
        "deadline_at",
        "failure_code",
        "failure_domain",
        "status",
    }
)


def set_allow_null_plugin(allow: bool) -> None:
    """保留测试调用兼容；Null Plugin 已无运行时语义。"""

    global _ALLOW_NULL_PLUGIN
    _ALLOW_NULL_PLUGIN = allow


def _ensure_non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _context_patch_has_reserved_key(context_patch: dict[str, Any] | None) -> bool:
    if not context_patch:
        return False
    return any(key in _RESERVED_CONTEXT_KEYS for key in context_patch)


def _system_error_result(message: str) -> OrchestratorResult:
    return _error_result(ErrorCode.UNKNOWN, message)


def _error_result(
    error_code: ErrorCode,
    message: str,
) -> OrchestratorResult:
    return OrchestratorResult(
        success=False,
        error=message,
        error_code=error_code.value,
        error_domain=error_domain_for(error_code).value,
    )


@dataclass
class OrchestratorResult:
    """编排器处理结果

    Attributes:
        success: 是否成功
        error: 错误信息（失败时）
        intents: RuntimeIntent 输出列表
    """

    success: bool
    error: str | None = None
    error_code: str | None = None
    error_domain: str | None = None
    intents: list[RuntimeIntent] | None = None


class OrchestratorService:
    """仅承载未绑定迁移会话的兼容意图转换与锁内写回。"""

    def __init__(
        self,
        lock_provider: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
        intent_producer: Callable[[Any, Any, str], list[RuntimeIntent]] | None = None,
        **legacy_dependencies: Any,
    ) -> None:
        """初始化编排器服务

        Args:
            lock_provider: 可选的锁提供者函数，用于测试注入。
                          接收锁 key，返回异步上下文管理器。
        """
        self._lock_provider = lock_provider
        self._intent_producer = intent_producer
        if any(value is not None for value in legacy_dependencies.values()):
            raise TypeError("legacy runtime catalog dependencies are no longer supported")

    @staticmethod
    def _resolve_session_pk(session: Any) -> int | None:
        """提取 Session 的真实整型主键。"""
        value = getattr(session, "id", None)
        if isinstance(value, bool):
            return None
        return value if isinstance(value, int) else None

    def _get_lock(self, lock_key: str) -> AbstractAsyncContextManager[None]:
        """获取锁上下文管理器。

        单阶段锁，不再区分 READ/WRITE。

        Args:
            lock_key: 锁的 key

        Returns:
            异步上下文管理器
        """
        if self._lock_provider:
            return self._lock_provider(lock_key)

        logger.error(
            "No lock provider configured for OrchestratorService; "
            "production paths must inject a real lock provider explicitly"
        )
        raise LockAcquireError("No lock provider configured for OrchestratorService")

    async def process_inbox(
        self,
        session: WorklineSession | None,
        workline: WorkLine | None,
        inbox: RuntimeInbox | None,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str,
        write_callback: Callable[[OrchestratorResult], Awaitable[None]] | None = None,
    ) -> OrchestratorResult:
        """处理 Inbox 事件（单阶段互斥锁）

        两阶段锁合并为单阶段。
        stale-session guard 由 Celery worker 保留（workline.py:1646-1660）。

        注意:session 锁确保同一 session 的消息串行处理。
        真实的 session 刷新和 stale 防护在 worker callback 中完成。

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: RuntimeInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 运行时领域服务容器
            trace_id: Trace ID
            write_callback: 可选的写入回调。若提供，则在锁临界区内执行，
                由 Celery worker 负责完成真实持久化写入
                （session / command / outbox / timeline / inbox）。

        Returns:
            OrchestratorResult: 处理结果
        """
        session_id = self._resolve_session_pk(session)
        if session_id is None:
            return _error_result(ErrorCode.SESSION_CONTEXT_MISSING, "Session missing primary key")

        lock_key = f"session:{session_id}"
        inbox_id_for_log = getattr(inbox, "id", "unknown") if inbox else "unknown"

        # 单阶段锁:包含插件调用和结果处理
        try:
            async with self._get_lock(lock_key):
                # 加载插件、构建上下文、调用插件、处理结果
                result = await self._process_read_phase(
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    devices_by_role=devices_by_role,
                    services=services,
                    trace_id=trace_id,
                )

                # 如果处理失败，直接返回
                if not result.success:
                    return result

                # 如果提供了 write_callback，执行持久化写入
                if write_callback is not None and result.success:
                    await write_callback(result)

                return result

        except LockAcquireError:
            logger.exception(f"Failed to acquire lock for session {session_id}")
            return _system_error_result("Lock acquire failed")
        except Exception as e:
            logger.exception(f"Unexpected error processing inbox {inbox_id_for_log}")
            return _system_error_result(str(e))

    async def _process_read_phase(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str,
    ) -> OrchestratorResult:
        """READ pass - 读取阶段（当前非共享读）

        执行:
        - 读取 RuntimeCapabilityDispatcher 写入的 RuntimeIntent
        - 校验 intent 不修改 runtime-owned context key

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: RuntimeInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 运行时领域服务容器
            trace_id: Trace ID

        Returns:
            OrchestratorResult: 处理结果
        """
        trace = TraceContext.from_runtime(
            session=session,
            workline=workline,
            inbox=inbox,
            trace_id=trace_id,
        )
        _ = devices_by_role, services, trace

        session_contract = _ensure_non_empty_str(getattr(session, "contract_version", None))
        workline_contract = _ensure_non_empty_str(getattr(workline, "contract_version", None))
        if session_contract and workline_contract and session_contract != workline_contract:
            return _error_result(
                ErrorCode.CONTRACT_MISMATCH,
                f"Session contract {session_contract!r} != workline {workline_contract!r}",
            )

        try:
            if self._intent_producer is None:
                raise RuntimeError("OrchestratorService requires an explicit intent producer")
            result = self._intent_producer(inbox, workline, trace.trace_id or trace_id)
        except Exception as e:
            logger.exception("Runtime capability intent extraction failed")
            return _error_result(ErrorCode.PLUGIN_EXECUTION_FAILED, str(e))

        return self._process_intents(result, session)

    async def _process_write_phase(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str,
        read_result: OrchestratorResult,
    ) -> OrchestratorResult:
        """WRITE pass - 写入阶段（独占）

        执行:
        - 状态迁移验证
        - 结果返回（供 Celery 任务使用）

        注意:实际的状态修改默认仍由 Celery 任务的 `_apply_orchestrator_effects` 完成；
        当 `process_inbox(..., write_callback=...)` 提供写回调时，worker 会在同一 WRITE 锁临界区内
        执行真实持久化写入，从而避免"锁住编排结果、放开真实写入"的并发窗口。

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: RuntimeInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 运行时领域服务容器
            trace_id: Trace ID
            read_result: 读取阶段的结果

        Returns:
            OrchestratorResult: 处理结果
        """
        session_id = self._resolve_session_pk(session)
        logger.debug(f"WRITE 阶段开始 for session {session_id}")

        # 当前实现:直接返回读取阶段的结果
        # 状态修改在 Celery 任务 _apply_orchestrator_effects 中完成（不在锁保护下）
        # 占位参数避免 IDE/ruff 警告
        _ = session, workline, inbox, devices_by_role, services, trace_id

        logger.debug(f"WRITE 阶段完成 for session {session_id}")
        return read_result

    def _process_intents(self, intents: list[RuntimeIntent], session: Any) -> OrchestratorResult:
        _ = session
        for intent in intents:
            if intent.context_patch and _context_patch_has_reserved_key(intent.context_patch):
                logger.warning("Plugin attempted to write reserved runtime state")
                return _error_result(
                    ErrorCode.PLUGIN_TRANSITION_INVALID,
                    "context patch contains runtime-owned key",
                )

        return OrchestratorResult(success=True, intents=intents)


__all__ = ["OrchestratorResult", "OrchestratorService"]
