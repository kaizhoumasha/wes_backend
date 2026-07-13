"""系统级异步任务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import run_async
from src.core.logger import logger
from src.database.db import get_db_context

if TYPE_CHECKING:
    from src.app.sys.services.outbox_engine import DispatchResult


@celery_app.task(
    name="src.celery_app.tasks.sys.dispatch_system_outbox_batch",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def dispatch_system_outbox_batch(self: Any, limit: int = 50) -> DispatchResult:
    """批量派发 SystemOutbox 消息。"""

    async def _dispatch() -> DispatchResult:
        from src.app.sys.services import system_outbox_engine

        async with get_db_context() as db:
            return await system_outbox_engine.dispatch(db, limit=limit)

    try:
        result = run_async(_dispatch)
        if result.get("dispatched", 0) > 0:
            logger.info(f"SystemOutbox 派发完成: {result}")
        return result
    except Exception as exc:
        logger.error(f"SystemOutbox 派发失败: {exc}")
        countdown = 10 * (2**self.request.retries)
        raise self.retry(exc=exc, countdown=countdown) from None


__all__ = ["dispatch_system_outbox_batch", "process_signal"]


@celery_app.task(
    name="src.celery_app.tasks.sys.process_signal",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: Any, payload: dict[str, Any]) -> None:
    logger.info(f"sys process_signal 接收到 payload: {payload}")
