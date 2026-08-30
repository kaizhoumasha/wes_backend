from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.celery_app.app import celery_app
from src.celery_app.config import beat_schedule, task_routes
from src.celery_app.tasks import wms_confirmation

TASK_NAME = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"


def test_wms_fulfillment_queue_contains_only_target_transport_and_confirmation_tasks() -> None:
    assert {task_name for task_name, route in task_routes.items() if route == {"queue": "wms-fulfillment"}} == {
        "src.celery_app.tasks.transport.process_transport_evidence_batch",
        "src.celery_app.tasks.transport.publish_transport_outcomes_batch",
        "src.celery_app.tasks.transport.reconcile_transport_tasks_batch",
        "src.celery_app.tasks.transport.submit_transport_tasks_batch",
        "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch",
    }


def test_wms_confirmation_dispatcher_has_dedicated_route_and_ten_second_beat() -> None:
    assert wms_confirmation.dispatch_wms_confirmations_batch.name == TASK_NAME
    assert "src.celery_app.tasks.wms_confirmation" in celery_app.conf.include
    assert task_routes[TASK_NAME] == {"queue": "wms-fulfillment"}
    assert beat_schedule["dispatch-wms-confirmations-batch"] == {
        "task": TASK_NAME,
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    }


@pytest.mark.parametrize("missing_owner", ["schedule", "route"])
def test_wms_confirmation_dispatcher_is_required_by_deployment_attestation(missing_owner: str) -> None:
    schedules = dict(beat_schedule)
    routes = dict(task_routes)
    if missing_owner == "schedule":
        schedules.pop("dispatch-wms-confirmations-batch")
    else:
        routes.pop(TASK_NAME)

    with pytest.raises(ValueError, match=r"Beat required schedule|wms-fulfillment"):
        if schedules.get("dispatch-wms-confirmations-batch", {}).get("task") != TASK_NAME:
            raise ValueError("Beat required schedule is missing: dispatch-wms-confirmations-batch")
        if routes.get(TASK_NAME) != {"queue": "wms-fulfillment"}:
            raise ValueError("WmsConfirmation must use wms-fulfillment")


def test_wms_confirmation_dispatcher_uses_runtime_owner_and_fixed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SimpleNamespace(dispatch_batch=lambda **_kwargs: None)

    async def dispatch_batch(*, limit: int) -> int:
        assert limit == 100
        return 7

    service.dispatch_batch = dispatch_batch
    monkeypatch.setattr(wms_confirmation, "_current_service", lambda: service)
    monkeypatch.setattr(wms_confirmation, "run_async", lambda factory: __import__("asyncio").run(factory()))

    assert wms_confirmation.dispatch_wms_confirmations_batch.run() == 7
    with pytest.raises(ValueError, match="100"):
        wms_confirmation.dispatch_wms_confirmations_batch.run(limit=99)
