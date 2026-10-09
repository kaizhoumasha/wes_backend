"""基础日志维护薄任务与固定调度配置的 FAST owner。"""

from typing import Annotated
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError

from src.celery_app.config import beat_schedule, task_routes
from src.celery_app.tasks import core
from src.core.conf import Settings


def test_cleanup_task_calls_only_log_service_and_uses_existing_bridge(monkeypatch):
    import asyncio

    from src.app.sys.services.api_access_log_service import api_access_log_service

    cleanup = AsyncMock(return_value=3)
    monkeypatch.setattr(api_access_log_service, "cleanup", cleanup)
    monkeypatch.setattr(core, "run_async", lambda factory: asyncio.run(factory()))
    assert core.cleanup_api_access_logs() == 3
    cleanup.assert_awaited_once()


def test_cleanup_beat_uses_sixty_seconds_and_existing_default_queue():
    schedule = beat_schedule["cleanup-api-access-logs"]
    assert schedule == {"task": "src.celery_app.tasks.core.cleanup_api_access_logs", "schedule": 60.0}
    assert task_routes["src.celery_app.tasks.core.*"] == {"queue": "default"}


@pytest.mark.parametrize(
    "name,default,minimum,maximum",
    [
        ("API_ACCESS_LOG_RETENTION_DAYS", 7, 1, 365),
        ("API_ACCESS_LOG_WRITE_TIMEOUT_MS", 100, 10, 1000),
        ("API_ACCESS_LOG_MAX_CONCURRENT_WRITES", 2, 1, 8),
    ],
)
def test_log_settings_have_single_validated_defaults(name, default, minimum, maximum):
    field = Settings.model_fields[name]
    assert field.default == default
    adapter = TypeAdapter(Annotated[field.annotation, *field.metadata])
    assert adapter.validate_python(minimum) == minimum
    assert adapter.validate_python(maximum) == maximum
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)
