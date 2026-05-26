"""Handling 系统级任务。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from celery import Task  # pyright: ignore[reportMissingTypeStubs]

from src.celery_app.app import celery_app
from src.core.logger import logger
from src.database import db as db_module

if TYPE_CHECKING:
    from collections.abc import Awaitable


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


class HandlingTask(Task):
    """Handling 任务基类 - 提供数据库会话管理。"""

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
                logger.warning(f"清理 HandlingTask DB 会话失败: {exc}")
            finally:
                loop.close()
                self._db = None


__all__ = ["HandlingTask", "process_signal"]


@celery_app.task(
    name="src.celery_app.tasks.handling.process_signal",
    base=HandlingTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: HandlingTask, payload: dict[str, Any]) -> None:
    logger.info(f"handling process_signal 接收到 payload: {payload}")
