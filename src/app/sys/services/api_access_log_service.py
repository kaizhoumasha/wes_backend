"""独立会话创建与响应后的有界尽力诊断写入。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import cast

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.sys.models.api_access_log import APIAccessLog, APIAccessLogCreate, APIAccessLogSummary
from src.app.sys.repositories.api_access_log_repository import APIAccessLogRepository, api_access_log_repository
from src.core.base_service import BaseService
from src.core.conf import settings
from src.core.logger import logger
from src.core.query_models import QueryOptions
from src.database.db import get_db_context
from src.utils.background_tasks import get_background_tasks
from src.utils.timezone import timezone

_active_writes = 0
_log_background_tasks: ContextVar[BackgroundTasks | None] = ContextVar("api_access_log_background_tasks", default=None)
_log_write_deadline: ContextVar[float | None] = ContextVar("api_access_log_write_deadline", default=None)
API_ACCESS_LOG_CLEANUP_BATCH_LIMIT = 5000
API_ACCESS_LOG_CLEANUP_TIMEOUT_SECONDS = 5


@contextmanager
def api_access_log_background_scope() -> Iterator[BackgroundTasks]:
    """宿主负责本轮后置执行；退出释放未执行数据，隔离审计上下文。"""
    tasks = BackgroundTasks()
    token = _log_background_tasks.set(tasks)
    try:
        yield tasks
    finally:
        tasks.tasks.clear()
        _log_background_tasks.reset(token)


@contextmanager
def _api_access_log_write_budget(deadline: float) -> Iterator[None]:
    """worker 传递整批截止；唯一写入取较早预算并负责一次取消。"""
    token = _log_write_deadline.set(deadline)
    try:
        yield
    finally:
        _log_write_deadline.reset(token)


class APIAccessLogService(BaseService[APIAccessLog, APIAccessLogRepository]):
    def __init__(self, *, repository=api_access_log_repository, session_context=get_db_context) -> None:
        super().__init__(repository=repository, enable_cache=False)
        self._sessions = session_context

    async def record(self, entry: APIAccessLogCreate) -> APIAccessLog:
        async with self._sessions() as db:
            return cast("APIAccessLog", await self.create(db, entry.model_dump()))

    async def try_record(self, entry: APIAccessLogCreate) -> None:
        global _active_writes
        if _active_writes >= settings.API_ACCESS_LOG_MAX_CONCURRENT_WRITES:
            logger.warning("api_access_log.write_skipped reason=capacity")
            return
        _active_writes += 1
        try:
            deadline = asyncio.get_running_loop().time() + settings.API_ACCESS_LOG_WRITE_TIMEOUT_MS / 1000
            batch_deadline = _log_write_deadline.get()
            if batch_deadline is not None:
                deadline = min(deadline, batch_deadline)
            write = asyncio.create_task(self.record(entry))
            try:
                async with asyncio.timeout_at(deadline):
                    await asyncio.shield(write)
            except BaseException as original_error:
                # 先退出计时器再取消写入，清理期间额外取消只保留传播，不再次取消写入。
                pending_error = original_error
                if not write.cancelling():
                    _ = write.cancel()
                cleanup = asyncio.gather(write, return_exceptions=True)
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError as cancellation:
                        pending_error = cancellation
                raise pending_error from None
        except Exception as error:
            logger.warning("api_access_log.write_skipped reason=write_failed exception_type={}", type(error).__name__)
        finally:
            _active_writes -= 1

    def defer_record(self, entry: APIAccessLogCreate) -> None:
        tasks = _log_background_tasks.get() or get_background_tasks()
        if tasks is None:
            logger.warning("api_access_log.write_skipped reason=no_host_scope")
            return
        tasks.add_task(self.try_record, entry.model_copy(deep=True))

    async def query_summary(self, db: AsyncSession, options: QueryOptions) -> tuple[int, list[APIAccessLogSummary]]:
        total, rows = await self.repo.query_summary(db, options)
        return total, [APIAccessLogSummary.model_validate(row) for row in rows]

    async def cleanup(self) -> int:
        cutoff = timezone.now_for_db() - timedelta(days=settings.API_ACCESS_LOG_RETENTION_DAYS)
        async with asyncio.timeout(API_ACCESS_LOG_CLEANUP_TIMEOUT_SECONDS):
            async with self._sessions() as db:
                try:
                    count = await self.repo.delete_expired_batch(
                        db, cutoff=cutoff, limit=API_ACCESS_LOG_CLEANUP_BATCH_LIMIT
                    )
                    await db.commit()
                    return count
                except BaseException:
                    await db.rollback()
                    raise


api_access_log_service = APIAccessLogService()

__all__ = ["APIAccessLogService", "api_access_log_background_scope", "api_access_log_service"]
