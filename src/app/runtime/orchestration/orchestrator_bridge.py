"""通用 Orchestrator 锁与 RuntimeIntent 结果边界。

Workline Plugin 决策由 RuntimeInboxProcessorBridge 直接交给 generated
WorklinePluginDispatcher；本模块不识别任何插件 key、事件、动作或超时路由。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.diagnostics import ErrorCode, error_domain_for
from src.app.runtime.orchestration.lock_bridge import LockAcquireError
from src.core.logger import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent


def set_allow_null_plugin(allow: bool) -> None:
    """保留测试调用兼容；Null Plugin 已无运行时语义。"""

    _ = allow


@dataclass
class OrchestratorResult:
    """通用写回层消费的编排结果包络。"""

    success: bool
    error: str | None = None
    error_code: str | None = None
    error_domain: str | None = None
    intents: list[RuntimeIntent] | None = None


def _error_result(error_code: ErrorCode, message: str) -> OrchestratorResult:
    return OrchestratorResult(
        success=False,
        error=message,
        error_code=error_code.value,
        error_domain=error_domain_for(error_code).value,
    )


class OrchestratorService:
    """仅保留锁边界；插件决策必须从 generated dispatcher 注入。"""

    def __init__(
        self,
        lock_provider: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
        **legacy_dependencies: Any,
    ) -> None:
        self._lock_provider = lock_provider
        if any(value is not None for value in legacy_dependencies.values()):
            raise TypeError("legacy runtime dispatcher dependencies are no longer supported")

    def _get_lock(self, lock_key: str) -> AbstractAsyncContextManager[None]:
        if self._lock_provider is None:
            raise LockAcquireError("No lock provider configured for OrchestratorService")
        return self._lock_provider(lock_key)

    async def process_inbox(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        trace_id: str,
        write_callback: Callable[[OrchestratorResult], Awaitable[None]] | None = None,
    ) -> OrchestratorResult:
        """拒绝绕过 generated dispatcher 的调用。"""

        _ = workline, inbox, devices_by_role, services, trace_id, write_callback
        session_id = getattr(session, "id", None)
        if isinstance(session_id, bool) or not isinstance(session_id, int):
            return _error_result(ErrorCode.SESSION_CONTEXT_MISSING, "Session missing primary key")
        try:
            async with self._get_lock(f"session:{session_id}"):
                return _error_result(
                    ErrorCode.PLUGIN_EXECUTION_FAILED,
                    "generated Workline Plugin dispatcher is required",
                )
        except LockAcquireError:
            logger.exception(f"Failed to acquire lock for session {session_id}")
            return _error_result(ErrorCode.UNKNOWN, "Lock acquire failed")


__all__ = ["OrchestratorResult", "OrchestratorService", "set_allow_null_plugin"]
