from __future__ import annotations

import pytest

from src.celery_app import config
from src.celery_app.tasks import workline as workline_tasks
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
            "ack_timeouts_reconciled": 0,
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


@pytest.mark.parametrize("task_type", ["MEASUREMENT_REEL", "MOVE_FORWARD", "PICK_AND_PUT"])
def test_map_command_task_type_preserves_plugin_task_type(task_type: str) -> None:
    assert _map_command_task_type(task_type) == task_type


def test_smt_full_box_exchange_candidate_scan_task_is_registered() -> None:
    assert hasattr(workline_tasks, "scan_smt_full_box_exchange_candidates_batch")
    assert config.beat_schedule["scan-smt-full-box-exchange-candidates-batch"] == {
        "task": "src.celery_app.tasks.workline.scan_smt_full_box_exchange_candidates_batch",
        "schedule": 60.0,
    }
