"""类型化 Fact 的有界后台处理入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async

if TYPE_CHECKING:
    from src.app.execution.services import FactProcessor

_EXECUTION_BATCH_LIMIT = 100


def _current_processor() -> FactProcessor:
    runtime = celery_async_runtime.execution_runtime
    if runtime is None:
        raise RuntimeError("Execution runtime is unavailable in the current Celery child")
    return runtime.fact_processor


@celery_app.task(name="src.celery_app.tasks.execution.process_execution_facts_batch")
def process_execution_facts_batch(limit: int = 100) -> int:
    if limit != _EXECUTION_BATCH_LIMIT:
        raise ValueError(f"Execution batch limit must be {_EXECUTION_BATCH_LIMIT}")

    async def _process() -> int:
        return await _current_processor().process_batch(limit)

    return run_async(_process)


__all__ = ["process_execution_facts_batch"]
