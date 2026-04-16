from __future__ import annotations

import pytest

from src.celery_app.tasks.workline import _ensure_non_empty_retry_result, _map_command_task_type


def test_ensure_non_empty_retry_result_allows_empty_first_attempt() -> None:
    _ensure_non_empty_retry_result(
        "process_inbox_batch",
        {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        },
        retries=0,
    )


def test_ensure_non_empty_retry_result_allows_non_empty_retry() -> None:
    _ensure_non_empty_retry_result(
        "scan_timeouts_batch",
        {
            "scanned": 1,
            "timeouts_created": 0,
            "errors": 0,
        },
        retries=2,
    )


def test_ensure_non_empty_retry_result_rejects_empty_retry() -> None:
    with pytest.raises(RuntimeError, match="process_inbox_batch returned an empty result after 2 retries"):
        _ensure_non_empty_retry_result(
            "process_inbox_batch",
            {
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            },
            retries=2,
        )


def test_map_command_task_type_maps_measurement_reel_to_process() -> None:
    assert _map_command_task_type("MEASUREMENT_REEL") == "PROCESS"
