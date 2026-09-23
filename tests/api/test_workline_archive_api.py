"""WorkLine 一键归档 API 合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import WorkLineStateTransitionRequest
from src.app.workline.services.workline_archive_service import WorkLineArchiveResult
from src.app.workline.v1 import archive as archive_api


def test_archive_open_work_route_uses_dedicated_permission() -> None:
    route = next(
        route
        for route in archive_api.router.routes
        if route.path == "/work_lines/{id}/archive-open-work" and "POST" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [
        "biz:workline:archive-open-work"
    ]


def test_archive_picking_task_route_uses_dedicated_permission() -> None:
    route = next(
        route
        for route in archive_api.router.routes
        if route.path == "/work_lines/{id}/archive-picking-task" and "POST" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [
        "biz:workline:archive-picking-task"
    ]


@pytest.mark.asyncio
async def test_archive_open_work_commits_then_invalidates_workline_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SimpleNamespace(
        archive_open_work=AsyncMock(
            return_value=WorkLineArchiveResult(
                workline_id=7,
                version=5,
                archived_picking_tasks=1,
                archived_plugin_tasks=3,
            )
        )
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_archive_service=service)))
    uow = SimpleNamespace(session=object(), commit=AsyncMock())

    class UowContext:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return uow

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(archive_api, "WorklineUnitOfWork", lambda **_kwargs: UowContext())
    invalidate = AsyncMock()
    monkeypatch.setattr(archive_api.workline_service, "invalidate_cache", invalidate)

    response = await archive_api.archive_workline_open_work(
        db=object(),
        cache=object(),
        request=request,
        id=7,
        payload=WorkLineStateTransitionRequest(version=4),
    )

    service.archive_open_work.assert_awaited_once_with(uow.session, workline_id=7, version=4)
    uow.commit.assert_awaited_once_with()
    invalidate.assert_awaited_once()
    assert response["data"].model_dump() == {
        "workline_id": 7,
        "version": 5,
        "archived_picking_tasks": 1,
        "archived_plugin_tasks": 3,
        "archived_total": 4,
        "archived_single_picking_task": False,
        "archived_single_picking_task_id": None,
        "picking_task_status_before": None,
    }


@pytest.mark.asyncio
async def test_archive_picking_task_commits_then_invalidates_workline_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.wms_integration.outbound_picking.models import PickingTaskStatus

    service = SimpleNamespace(
        archive_picking_task=AsyncMock(
            return_value=WorkLineArchiveResult(
                workline_id=7,
                version=5,
                archived_picking_tasks=0,
                archived_plugin_tasks=0,
                archived_single_picking_task=True,
                archived_single_picking_task_id=99,
                picking_task_status_before=PickingTaskStatus.EXECUTING,
            )
        )
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_archive_service=service)))
    uow = SimpleNamespace(session=object(), commit=AsyncMock())

    class UowContext:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return uow

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(archive_api, "WorklineUnitOfWork", lambda **_kwargs: UowContext())
    invalidate = AsyncMock()
    monkeypatch.setattr(archive_api.workline_service, "invalidate_cache", invalidate)

    response = await archive_api.archive_workline_picking_task(
        db=object(),
        cache=object(),
        request=request,
        id=7,
        payload=WorkLineStateTransitionRequest(version=4, picking_task_id=99),
    )

    service.archive_picking_task.assert_awaited_once_with(
        uow.session,
        workline_id=7,
        version=4,
        picking_task_id=99,
        task_id=None,
    )
    uow.commit.assert_awaited_once_with()
    invalidate.assert_awaited_once()
    dumped = response["data"].model_dump()
    assert dumped["archived_single_picking_task"] is True
    assert dumped["archived_single_picking_task_id"] == 99
    assert dumped["picking_task_status_before"] == "EXECUTING"
    assert dumped["version"] == 5


@pytest.mark.asyncio
async def test_archive_picking_task_rejects_missing_identifier_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(archive_picking_task=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_archive_service=service)))
    uow = SimpleNamespace(session=object(), commit=AsyncMock())

    class UowContext:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return uow

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(archive_api, "WorklineUnitOfWork", lambda **_kwargs: UowContext())
    invalidate = AsyncMock()
    monkeypatch.setattr(archive_api.workline_service, "invalidate_cache", invalidate)

    response = await archive_api.archive_workline_picking_task(
        db=object(),
        cache=object(),
        request=request,
        id=7,
        payload=WorkLineStateTransitionRequest(version=4),
    )

    service.archive_picking_task.assert_not_awaited()
    uow.commit.assert_not_awaited()
    invalidate.assert_not_awaited()
    assert response["code"] == "2001"
