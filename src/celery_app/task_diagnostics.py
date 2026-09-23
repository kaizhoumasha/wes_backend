"""Structured diagnostics shared by bounded Celery task entrypoints."""

from __future__ import annotations

import logging
import os
from typing import Any

from celery import current_task

logger = logging.getLogger(__name__)


def task_context(task_name: str) -> dict[str, Any]:
    try:
        request = current_task.request
    except AttributeError:
        request = None
    delivery = (request.delivery_info or {}) if request is not None else {}
    key_prefix = os.getenv("TRANSPORT_BROKER_KEY_PREFIX", "")
    return {
        "task_name": task_name,
        "queue": delivery.get("routing_key"),
        "worker_hostname": request.hostname if request is not None else None,
        "run_id": key_prefix.removeprefix("it:transport:").rstrip(":"),
        "key_prefix": key_prefix,
    }


def task_started(task_name: str, *, limit: int) -> dict[str, Any]:
    context = task_context(task_name)
    logger.info("task.body.start", extra={"event": "task.body.start", **context, "limit": limit})
    return context


def task_finished(context: dict[str, Any], *, processed: int) -> None:
    logger.info(
        "task.body.done",
        extra={"event": "task.body.done", **context, "processed": processed},
    )


def task_failed(context: dict[str, Any], error: BaseException) -> None:
    logger.exception(
        "task.body.failed",
        extra={"event": "task.body.failed", **context, "error_type": type(error).__name__},
    )
