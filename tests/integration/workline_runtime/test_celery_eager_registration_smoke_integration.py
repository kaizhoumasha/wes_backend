from __future__ import annotations

import asyncio

import pytest

from src.celery_app.app import celery_app
from src.celery_app.tasks.workline import process_inbox_batch, scan_timeouts_batch


@pytest.mark.asyncio
async def test_workline_tasks_registered_and_eager_callable(
    eager_celery: None,
) -> None:
    assert "src.celery_app.tasks.workline.process_inbox_batch" in celery_app.tasks
    assert "src.celery_app.tasks.workline.scan_timeouts_batch" in celery_app.tasks

    process_result = await asyncio.to_thread(process_inbox_batch, 0)
    timeout_result = await asyncio.to_thread(scan_timeouts_batch, 0)

    assert process_result == {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 0,
    }
    assert timeout_result == {
        "scanned": 0,
        "timeouts_created": 0,
        "ack_timeouts_reconciled": 0,
        "errors": 0,
    }
