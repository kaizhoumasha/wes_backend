"""DeviceCommand 生产装配、静态队列和任务注册合同。"""

from __future__ import annotations

import importlib

import pytest

from src.app.device.composition import resolve_device_command_runtime_config


def test_ecs_runtime_config_is_fail_closed_and_has_fixed_queue() -> None:
    config = resolve_device_command_runtime_config(
        {
            "ECS_CONNECT_TIMEOUT_SECONDS": "1.5",
            "ECS_READ_TIMEOUT_SECONDS": "2.5",
            "DEVICE_COMMAND_QUEUE": "device-command",
        }
    )

    assert config.timeout_seconds == 4.0
    assert config.queue == "device-command"

    with pytest.raises(ValueError):
        resolve_device_command_runtime_config({})
    with pytest.raises(ValueError):
        resolve_device_command_runtime_config(
            {
                "ECS_CONNECT_TIMEOUT_SECONDS": "1",
                "ECS_READ_TIMEOUT_SECONDS": "1",
                "DEVICE_COMMAND_QUEUE": "device",
            }
        )


def test_three_bounded_tasks_are_registered_and_routed_to_fixed_queue() -> None:
    from src.celery_app.app import celery_app
    from src.celery_app.config import beat_schedule, task_routes

    importlib.import_module("src.celery_app.tasks.device_command")
    names = {
        "src.celery_app.tasks.device_command.dispatch_device_commands_batch": 10.0,
        "src.celery_app.tasks.device_command.process_device_evidence_batch": 10.0,
        "src.celery_app.tasks.device_command.reconcile_device_commands_batch": 30.0,
    }
    for name, period in names.items():
        assert name in celery_app.tasks
        assert task_routes[name] == {"queue": "device-command"}
        schedules = [item for item in beat_schedule.values() if item["task"] == name]
        assert schedules == [
            {
                "task": name,
                "schedule": period,
                "kwargs": {"limit": 100},
                "options": {"expires": period},
            }
        ]
