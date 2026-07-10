"""RuntimeInbox orchestrator delegate (Task 5 三阶段 Processor 拆分).

Pure delegate 到 OrchestratorService.process_inbox.

负责:
- 构造 OrchestratorService (with lock_provider)
- 调用 process_inbox (带 asyncio.wait_for 单条 timeout 保护)
- 透传 OrchestratorResult 给调用方

不写终态, 不做 SCAN/ESTOP/TIMER 前置 gate, 不做 write-back 锁回调.
这些职责由 Validation/Write-back 阶段和 Composition 层处理.

注: 本模块的 `RuntimeInboxOrchestratorDelegate` 是三阶段 Processor 的 Stage 2
实现细节, 三阶段组合在 `runtime_inbox_orchestrator_bridge.py` 的
`RuntimeInboxProcessorService` 里.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from loguru import logger
from sqlalchemy import text

from src.app.runtime.orchestration.lock_bridge import RedisDistributedLock
from src.app.runtime.orchestration.orchestrator_bridge import (
    OrchestratorResult,
    OrchestratorService,
)
from src.app.workline.constants import INBOX_PROCESS_TIMEOUT_SECONDS
from src.database.redis_client import get_redis

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession


def _build_orchestrator_lock_provider(db: Any) -> Callable[[str], AbstractAsyncContextManager[None]]:
    """为 OrchestratorService 构建生产锁提供者.

    优先使用 Redis 分布式锁; Redis 不可用时回退到 PostgreSQL advisory lock,
    但绝不退化为无锁. (等价于 InboxBatchProcessor._build_orchestrator_lock_provider)
    """
    redis_client = get_redis()
    if redis_client is not None:
        lock = RedisDistributedLock(redis_client=cast("Any", redis_client), key_prefix="workline:orchestrator:")

        def _redis_lock(lock_key: str) -> AbstractAsyncContextManager[None]:
            return lock.acquire(lock_key, db=db)

        return _redis_lock

    logger.warning("Redis not available for orchestrator lock, falling back to PostgreSQL advisory xact lock")

    def _resource_id(resource: str) -> int:
        digest = hashlib.blake2b(resource.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False) % (2**63)

    @asynccontextmanager
    async def _pg_lock(lock_key: str):  # type: ignore[no-untyped-def]
        resource_id = _resource_id(lock_key)
        # 使用事务级 advisory lock, 随 commit/rollback 自动释放,
        # 避免锁内 commit 后再依赖另一连接手动 unlock 造成悬挂锁.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:resource_id)"),
            {"resource_id": resource_id},
        )
        yield

    return _pg_lock


class RuntimeInboxOrchestratorDelegate:
    """Pure delegate to OrchestratorService.process_inbox (Stage 2).

    单一职责: 把 RuntimeInbox + Session/Workline/services 喂给 OrchestratorService,
    暴露带 timeout 保护的 process_inbox 调用, 让 Composition 层在回调中执行
    write-back 和终态更新.
    """

    def __init__(
        self,
        *,
        orchestrator_factory: Callable[..., OrchestratorService] | None = None,
        timeout_seconds: float = INBOX_PROCESS_TIMEOUT_SECONDS,
    ) -> None:
        self._orchestrator_factory = orchestrator_factory or OrchestratorService
        self._timeout_seconds = float(timeout_seconds)

    async def process(
        self,
        db: AsyncSession | Any,
        *,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        trace_id: str,
        write_callback: Callable[[OrchestratorResult], Any] | None = None,
    ) -> OrchestratorResult:
        """带 timeout 的 process_inbox 调用.

        Args:
            db: 数据库会话 (用于构建 lock provider).
            session: WorklineSession 实体.
            workline: WorkLine 实体.
            inbox: RuntimeInbox 实体 (主链路收束后).
            devices_by_role: 按角色分组的设备映射.
            services: 运行时领域服务容器.
            trace_id: Trace ID.
            write_callback: 锁内 write-back 回调 (由 Composition 层注入).

        Returns:
            OrchestratorResult.
        """
        orchestrator = self._orchestrator_factory(lock_provider=_build_orchestrator_lock_provider(db))
        return await asyncio.wait_for(
            orchestrator.process_inbox(
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role=devices_by_role,
                services=services,
                trace_id=trace_id,
                write_callback=write_callback,
            ),
            timeout=self._timeout_seconds,
        )


__all__ = [
    "RuntimeInboxOrchestratorDelegate",
    "_build_orchestrator_lock_provider",
]
