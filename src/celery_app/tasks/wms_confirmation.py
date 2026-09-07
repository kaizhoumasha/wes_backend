"""WMS confirmation 独立有界派发入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution.config import WMS_CONFIRMATION_BATCH_LIMIT
from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async

if TYPE_CHECKING:
    from src.app.execution.services import WmsConfirmationService


def _current_service() -> WmsConfirmationService:
    runtime = celery_async_runtime.execution_runtime
    if runtime is None:
        raise RuntimeError("Execution runtime is unavailable in the current Celery child")
    return runtime.execution.wms_confirmation_service


@celery_app.task(name="src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch")
def dispatch_wms_confirmations_batch(limit: int = WMS_CONFIRMATION_BATCH_LIMIT) -> int:
    if limit != WMS_CONFIRMATION_BATCH_LIMIT:
        raise ValueError(f"WMS confirmation batch limit must be {WMS_CONFIRMATION_BATCH_LIMIT}")

    async def _dispatch() -> int:
        return await _current_service().dispatch_batch(limit=limit)

    return run_async(_dispatch)


__all__ = ["dispatch_wms_confirmations_batch"]
