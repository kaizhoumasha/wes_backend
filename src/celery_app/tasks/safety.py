"""Safety incident 可靠排空任务。"""

from __future__ import annotations

from typing import cast

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import run_async
from src.database.db import get_db_context


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
                await db.commit()
                processed += 1
                completed += int(incident.drain_status == "COMPLETED")
        return {"processed": processed, "completed": completed}

    return cast("dict[str, int]", run_async(_drain))


__all__ = ["drain_safety_incidents_batch"]
