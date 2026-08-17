from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime
from src.celery_app.config import beat_schedule, task_routes
from src.celery_app.tasks import execution

TASK_NAME = "src.celery_app.tasks.execution.process_execution_facts_batch"


def test_execution_fact_task_is_registered_and_routed_to_wes_worker() -> None:
    assert execution.process_execution_facts_batch.name == TASK_NAME
    assert TASK_NAME in celery_app.tasks
    assert task_routes[TASK_NAME] == {"queue": "device-command"}
    assert beat_schedule["process-execution-facts-batch"] == {
        "task": TASK_NAME,
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    }


def test_execution_task_requires_an_explicit_child_runtime(monkeypatch) -> None:
    monkeypatch.setattr(celery_async_runtime, "_execution_runtime", None)

    try:
        execution._current_processor()
    except RuntimeError as exc:
        assert "Execution runtime is unavailable" in str(exc)
    else:
        raise AssertionError("unbound execution runtime must fail closed")
