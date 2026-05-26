import pytest


def test_internal_signal_tasks_are_registered() -> None:
    import src.celery_app.tasks.core
    import src.celery_app.tasks.handling
    import src.celery_app.tasks.sys
    import src.celery_app.tasks.workline
    from src.celery_app.app import celery_app

    registered_tasks = celery_app.tasks.keys()

    expected_tasks = [
        "src.celery_app.tasks.core.process_signal",
        "src.celery_app.tasks.handling.process_signal",
        "src.celery_app.tasks.sys.process_signal",
        "src.celery_app.tasks.workline.process_signal",
    ]

    for task in expected_tasks:
        assert task in registered_tasks, f"{task} not registered in Celery"
