"""Handling 系统级任务。"""

from __future__ import annotations

from typing import Any

from src.celery_app.app import celery_app
from src.core.logger import logger

__all__ = ["process_signal"]


@celery_app.task(
    name="src.celery_app.tasks.handling.process_signal",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: Any, payload: dict[str, Any]) -> None:
    logger.info(f"handling process_signal 接收到 payload: {payload}")
