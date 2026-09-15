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


@pytest.mark.asyncio
async def test_archive_open_work_commits_then_invalidates_workline_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SimpleNamespace(
        archive_open_work=AsyncMock(
            return_value=WorkLineArchiveResult(
                workline_id=7,
                version=5,
                archived_picking_tasks=1,
                archived_plugin_tasks=3,
                archived_integration_runs=1,
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
        "archived_integration_runs": 1,
        "archived_total": 5,
    }
