"""SMT inbound handoff recovery Celery task tests."""

from __future__ import annotations

from src.celery_app import config
from src.celery_app.app import celery_app
from src.celery_app.tasks import workline as workline_tasks


def test_smt_inbound_handoff_recovery_task_is_registered() -> None:
    task_name = "src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch"

    assert hasattr(workline_tasks, "scan_smt_inbound_handoff_demands_batch")
    assert task_name in celery_app.tasks
    assert config.beat_schedule["scan-smt-inbound-handoff-demands-batch"]["task"] == task_name
    assert config.task_routes["src.celery_app.tasks.workline.*"]["queue"] == "celery"


def test_smt_inbound_handoff_recovery_task_summary_contract() -> None:
    empty_summary = workline_tasks._empty_smt_inbound_handoff_recovery_result()

    assert empty_summary == {
        "scanned": 0,
        "claimed": 0,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }
