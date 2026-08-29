"""
作业线编排 Celery 任务

本文件提供 Workline 核心流程的 Celery 任务入口。
核心业务逻辑（如 Inbox 批量处理、Orchestrator 写回、出站下发等）
已抽离至 `src/app/workline/services/` 目录下。
设计参考: runtime-orchestration 设计文档
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import run_async
from src.core.conf import settings
from src.core.logger import logger
from src.database.db import get_db_context


# ============================================
# 类型定义
# ============================================
@celery_app.task(
    name="src.celery_app.tasks.workline.check_wms_effect_status",
    base=celery_app.Task,
)
def check_wms_effect_status(dispatch_key: str) -> dict[str, object]:
    """按 dispatch key 执行一次 lease-fenced WMS EFFECT 状态确认。"""

    async def _check() -> dict[str, object]:
        from src.app.runtime.orchestration.services.wms_effect_status_service import (
            wms_effect_status_service,
        )

        async with get_db_context() as db:
            result = await wms_effect_status_service.check_dispatch(db, dispatch_key=dispatch_key)
            return asdict(result)

    return cast("dict[str, object]", run_async(_check))


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_wms_effect_status_batch",
    base=celery_app.Task,
    soft_time_limit=settings.WES_EFFECT_STATUS_TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.WES_EFFECT_STATUS_TASK_HARD_TIME_LIMIT_SECONDS,
)
def scan_wms_effect_status_batch() -> list[dict[str, object]]:
    """小批量有界并发确认到期 WMS EFFECT，Beat 只负责兜底扫描。"""

    async def _scan() -> list[dict[str, object]]:
        from src.app.runtime.orchestration.services.wms_effect_status_service import (
            wms_effect_status_service,
        )

        async with get_db_context() as db:
            results = await wms_effect_status_service.check_due_batch(db)
            return [asdict(item) for item in results]

    return cast("list[dict[str, object]]", run_async(_scan))


@celery_app.task(
    name="src.celery_app.tasks.workline.drain_safety_incidents_batch",
    base=celery_app.Task,
)
def drain_safety_incidents_batch(limit: int = 10, command_limit: int = 100) -> dict[str, int]:
    """按 incident 重试有界排空；每条 incident 使用独立事务。"""

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ValueError("safety incident batch limit must be an integer between 1 and 10")
    if not isinstance(command_limit, int) or isinstance(command_limit, bool) or not 1 <= command_limit <= 100:
        raise ValueError("safety command batch limit must be an integer between 1 and 100")

    async def _drain() -> dict[str, int]:
        from src.app.workline.services.safety_service import workline_safety_service

        processed = 0
        completed = 0
        for _ in range(limit):
            async with get_db_context() as db:
                incident = await workline_safety_service.drain_one(db, command_limit=command_limit)
                if incident is None:
                    break
                processed += 1
                completed += int(incident.drain_status == "COMPLETED")
        return {"processed": processed, "completed": completed}

    return cast("dict[str, int]", run_async(_drain))


# ============================================
# 导出
# ============================================
__all__ = [
    # Celery 任务入口（公共 API）
    "check_wms_effect_status",
    "drain_safety_incidents_batch",
    "process_signal",
    "scan_wms_effect_status_batch",
]


@celery_app.task(
    name="src.celery_app.tasks.workline.process_signal",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: Any, payload: dict[str, Any]) -> None:
    logger.info(f"workline process_signal 接收到 payload: {payload}")
