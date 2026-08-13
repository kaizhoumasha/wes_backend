"""DeviceCommand 可靠对象的三个有界数据库扫描任务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.device.composition import DeviceCommandRuntime

_DEVICE_COMMAND_BATCH_LIMIT = 100


def _current_runtime() -> DeviceCommandRuntime:
    runtime = celery_async_runtime.device_command_runtime
    if runtime is None:
        raise RuntimeError("DeviceCommand runtime is unavailable in the current Celery child")
    return runtime


def _require_fixed_batch(limit: int) -> None:
    if limit != _DEVICE_COMMAND_BATCH_LIMIT:
        raise ValueError(f"DeviceCommand batch limit must be {_DEVICE_COMMAND_BATCH_LIMIT}")


@celery_app.task(name="src.celery_app.tasks.device_command.dispatch_device_commands_batch")
def dispatch_device_commands_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _dispatch() -> int:
        runtime = _current_runtime()
        processed = 0
        for _ in range(limit):
            if not await runtime.dispatch_service.dispatch_one(now=timezone.now_for_db()):
                break
            processed += 1
        return processed

    return run_async(_dispatch)


@celery_app.task(name="src.celery_app.tasks.device_command.process_device_evidence_batch")
def process_device_evidence_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _process() -> int:
        runtime = _current_runtime()
        processed = 0
        for _ in range(limit):
            if not await runtime.evidence_service.process_one():
                break
            processed += 1
        return processed

    return run_async(_process)


@celery_app.task(name="src.celery_app.tasks.device_command.reconcile_device_commands_batch")
def reconcile_device_commands_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _reconcile() -> int:
        runtime = _current_runtime()
        processed = 0
        for _ in range(limit):
            if not await runtime.command_service.reconcile_one(now=timezone.now_for_db()):
                break
            processed += 1
        return processed

    return run_async(_reconcile)


__all__ = [
    "dispatch_device_commands_batch",
    "process_device_evidence_batch",
    "reconcile_device_commands_batch",
]
