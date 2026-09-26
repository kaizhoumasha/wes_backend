"""Device-scoped ECS_TEST defaults remain separate from WorkLine runtime rules."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, sentinel

import pytest

from src.app.device.services.device_service import DeviceService


@pytest.mark.asyncio
async def test_saving_default_commits_before_cache_invalidation_and_preserves_workline_rules() -> None:
    workline_rules = {"ecs_test_rules": [{"source_device_code": "SOURCE-1", "target_device_code": "OLD"}]}
    workline = SimpleNamespace(is_active=True, runtime_config_json=workline_rules, version=8)
    device = SimpleNamespace(id=17, device_code="SOURCE-1", version=4, work_line=workline)
    events: list[str] = []
    service = DeviceService()
    service.repo = SimpleNamespace(set_ecs_test_default=AsyncMock(return_value=device))  # type: ignore[assignment]
    service._commit_mutation = AsyncMock(side_effect=lambda _db: events.append("commit"))  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda *_args, **_kwargs: events.append("invalidate")
    )

    result = await service.save_ecs_test_default(
        sentinel.db,
        "SOURCE-1",
        {"target_device_code": "TARGET-2", "task_type": "MOVE_FORWARD", "params": {}},
        sentinel.cache,
    )

    assert result is device
    service.repo.set_ecs_test_default.assert_awaited_once_with(
        sentinel.db,
        "SOURCE-1",
        {"target_device_code": "TARGET-2", "task_type": "MOVE_FORWARD", "params": {}},
    )
    assert events == ["commit", "invalidate"]
    service.invalidate_cache.assert_awaited_once_with(sentinel.cache, 17, invalidate_list=True)
    assert workline.runtime_config_json == workline_rules
    assert workline.version == 8


@pytest.mark.asyncio
async def test_explicit_clear_commits_none_then_invalidates_device_cache() -> None:
    device = SimpleNamespace(id=18, device_code="SOURCE-2", version=3)
    events: list[str] = []
    service = DeviceService()
    service.repo = SimpleNamespace(set_ecs_test_default=AsyncMock(return_value=device))  # type: ignore[assignment]
    service._commit_mutation = AsyncMock(side_effect=lambda _db: events.append("commit"))  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda *_args, **_kwargs: events.append("invalidate")
    )

    result = await service.save_ecs_test_default(sentinel.db, "SOURCE-2", None, sentinel.cache)

    assert result is device
    service.repo.set_ecs_test_default.assert_awaited_once_with(sentinel.db, "SOURCE-2", None)
    assert events == ["commit", "invalidate"]


@pytest.mark.asyncio
async def test_missing_device_does_not_commit_or_invalidate_cache() -> None:
    service = DeviceService()
    service.repo = SimpleNamespace(set_ecs_test_default=AsyncMock(return_value=None))  # type: ignore[assignment]
    service._commit_mutation = AsyncMock()  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock()  # type: ignore[method-assign]

    result = await service.save_ecs_test_default(
        sentinel.db,
        "MISSING",
        {"target_device_code": "TARGET", "task_type": "MOVE_FORWARD", "params": {}},
        sentinel.cache,
    )

    assert result is None
    service._commit_mutation.assert_not_awaited()
    service.invalidate_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_commit_does_not_invalidate_cache() -> None:
    device = SimpleNamespace(id=19, device_code="SOURCE-3", version=2)
    service = DeviceService()
    service.repo = SimpleNamespace(set_ecs_test_default=AsyncMock(return_value=device))  # type: ignore[assignment]
    service._commit_mutation = AsyncMock(side_effect=RuntimeError("commit failed"))  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.save_ecs_test_default(
            sentinel.db,
            "SOURCE-3",
            {"target_device_code": "TARGET", "task_type": "MOVE_FORWARD", "params": {}},
            sentinel.cache,
        )

    service.invalidate_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_default_is_rejected_before_repository_write() -> None:
    service = DeviceService()
    service.repo = SimpleNamespace(set_ecs_test_default=AsyncMock())  # type: ignore[assignment]
    service._commit_mutation = AsyncMock()  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="params 必须为对象"):
        await service.save_ecs_test_default(
            sentinel.db,
            "SOURCE-1",
            {"target_device_code": "TARGET", "task_type": "MOVE_FORWARD", "params": []},
            sentinel.cache,
        )

    service.repo.set_ecs_test_default.assert_not_awaited()
    service._commit_mutation.assert_not_awaited()
