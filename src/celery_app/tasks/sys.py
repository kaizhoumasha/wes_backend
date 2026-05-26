"""系统级异步任务。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from celery import Task  # pyright: ignore[reportMissingTypeStubs]

from src.celery_app.app import celery_app
from src.core.logger import logger
from src.database import db as db_module

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from src.app.sys.services.outbox_engine import DispatchResult


def _run_async(coro: Awaitable[Any]) -> Any:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        raise RuntimeError("Cannot run async task while event loop is already running")
    return loop.run_until_complete(coro)


def _lazy_init_db() -> None:
    if db_module.AsyncSessionLocal is None:
        _run_async(db_module.init_db())


class SystemTask(Task):
    """系统级任务基类 - 提供数据库会话管理。"""

    def __init__(self) -> None:
        super().__init__()
        self._db: Any | None = None

    @property
    def db(self) -> Any:
        if self._db is None:
            _lazy_init_db()
            session_local = db_module.AsyncSessionLocal
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        if self._db:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._db.close())
            except Exception as exc:
                logger.warning(f"清理 SystemTask DB 会话失败: {exc}")
            finally:
                loop.close()
                self._db = None


@celery_app.task(
    name="src.celery_app.tasks.sys.dispatch_system_outbox_batch",
    base=SystemTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def dispatch_system_outbox_batch(self: SystemTask, limit: int = 50) -> DispatchResult:
    """批量派发 SystemOutbox 消息。"""

    async def _dispatch() -> DispatchResult:
        from src.app.sys.services import system_outbox_engine

        async with self.db as db:
            return await system_outbox_engine.dispatch(db, limit=limit)

    try:
        result = _run_async(_dispatch())
        if result.get("dispatched", 0) > 0:
            logger.info(f"SystemOutbox 派发完成: {result}")
        return result
    except Exception as exc:
        logger.error(f"SystemOutbox 派发失败: {exc}")
        countdown = 10 * (2**self.request.retries)
        raise self.retry(exc=exc, countdown=countdown) from None


__all__ = ["SystemTask", "dispatch_system_outbox_batch", "process_signal"]


@celery_app.task(
    name="src.celery_app.tasks.sys.process_signal",
    base=SystemTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: SystemTask, payload: dict[str, Any]) -> None:
    logger.info(f"sys process_signal 接收到 payload: {payload}")
