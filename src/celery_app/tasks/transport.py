"""Transport 可靠对象的有界后台驱动。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async

if TYPE_CHECKING:
    from src.app.transport.contracts import TransportOutcomePublisher
    from src.app.transport.service import TransportService

_TRANSPORT_BATCH_LIMIT = 100


def _current_transport_service() -> TransportService:
    runtime = celery_async_runtime.transport_runtime
    if runtime is None:
        raise RuntimeError("Transport runtime is unavailable in the current Celery child")
    return runtime.service


def _current_outcome_publisher() -> TransportOutcomePublisher:
    runtime = celery_async_runtime.execution_runtime
    if runtime is None:
        raise RuntimeError("Rough sorter deployment runtime is unavailable in the current Celery child")
    return runtime.transport_outcome_publisher


def _require_fixed_batch(limit: int) -> None:
    if limit != _TRANSPORT_BATCH_LIMIT:
        raise ValueError(f"Transport batch limit must be {_TRANSPORT_BATCH_LIMIT}")


@celery_app.task(name="src.celery_app.tasks.transport.submit_transport_tasks_batch")
def submit_transport_tasks_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _submit() -> int:
        return await _current_transport_service().submit_pending_tasks(limit)

    return run_async(_submit)


@celery_app.task(name="src.celery_app.tasks.transport.advance_transport_debug_runs_batch")
def advance_transport_debug_runs_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _advance() -> int:
        runtime = celery_async_runtime.transport_runtime
        if runtime is None:
            raise RuntimeError("Transport runtime is unavailable in the current Celery child")
        return await runtime.debug_run_service.advance_active_runs(limit)

    return run_async(_advance)


@celery_app.task(name="src.celery_app.tasks.transport.process_transport_evidence_batch")
def process_transport_evidence_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _process() -> int:
        return await _current_transport_service().process_pending_evidence(limit)

    return run_async(_process)


@celery_app.task(name="src.celery_app.tasks.transport.reconcile_transport_tasks_batch")
def reconcile_transport_tasks_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _reconcile() -> int:
        return await _current_transport_service().reconcile_overdue_tasks(limit)

    return run_async(_reconcile)


@celery_app.task(name="src.celery_app.tasks.transport.publish_transport_outcomes_batch")
def publish_transport_outcomes_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _publish() -> int:
        return await _current_transport_service().publish_pending_outcomes(limit, _current_outcome_publisher())

    return run_async(_publish)


__all__ = [
    "advance_transport_debug_runs_batch",
    "process_transport_evidence_batch",
    "publish_transport_outcomes_batch",
    "reconcile_transport_tasks_batch",
    "submit_transport_tasks_batch",
]
