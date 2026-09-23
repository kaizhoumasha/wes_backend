"""作业线清线只归档业务任务，不改写设备与外部可靠义务。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.workline_archive_service import (
    WorkLineArchiveNotFoundError,
    WorkLineArchivePickingTaskInvalidStateError,
    WorkLineArchivePickingTaskNotFoundError,
    WorkLineArchiveService,
    WorkLineArchiveVersionConflictError,
)


def _setup_service(*, plugin_archived: int = 3, picking_archived: int = 1):
    workline = WorkLine(
        id=7,
        line_code="WL-7",
        line_name="七号线",
        line_type=LineType.MANUAL,
        version=4,
        is_active=True,
        plugin_key="manual-picking",
        plugin_version="1.0.0",
    )
    worklines = AsyncMock()
    worklines.get_for_update.return_value = workline

    async def advance_version(_db: object, target: WorkLine) -> WorkLine:
        target.increment_version()
        return target

    worklines.advance_version_for_archive.side_effect = advance_version
    picking_tasks = AsyncMock()
    picking_tasks.archive_open_for_workline.return_value = picking_archived
    business_archiver = AsyncMock()
    business_archiver.archive_open_work.return_value = plugin_archived
    reservation_archiver = AsyncMock()
    reservation_archiver.archive_active_for_workline.return_value = 1
    plugin = SimpleNamespace(
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        business_archiver=business_archiver,
    )
    return (
        WorkLineArchiveService(
            plugins=(plugin,),
            workline_repository=worklines,
            picking_task_repository=picking_tasks,
            reservation_archiver=reservation_archiver,
        ),
        workline,
        worklines,
        picking_tasks,
        business_archiver,
        reservation_archiver,
    )


@pytest.mark.asyncio
async def test_archive_open_work_archives_current_task_and_plugin_work_in_one_locked_scope() -> None:
    service, workline, worklines, picking_tasks, business_archiver, reservation_archiver = _setup_service()
    db = object()
    archived_at = datetime(2026, 9, 15, 7, 30, tzinfo=UTC)

    result = await service.archive_open_work(db, workline_id=7, version=4, now=archived_at)

    worklines.get_for_update.assert_awaited_once_with(db, 7)
    picking_tasks.archive_open_for_workline.assert_awaited_once_with(
        db,
        workline_id=7,
        archived_at=archived_at.replace(tzinfo=None),
    )
    business_archiver.archive_open_work.assert_awaited_once_with(
        db,
        workline_id=7,
        archived_at=archived_at.replace(tzinfo=None),
    )
    reservation_archiver.archive_active_for_workline.assert_awaited_once_with(
        db,
        workline_id=7,
        archived_at=archived_at.replace(tzinfo=None),
    )
    assert result.workline_id == 7
    assert result.version == 5
    assert result.archived_picking_tasks == 1
    assert result.archived_plugin_tasks == 3
    assert result.archived_integration_runs == 1
    assert workline.version == 5


@pytest.mark.asyncio
async def test_archive_open_work_is_idempotent_when_nothing_is_open() -> None:
    service, workline, _, _, _, reservation_archiver = _setup_service(plugin_archived=0, picking_archived=0)
    reservation_archiver.archive_active_for_workline.return_value = 0

    result = await service.archive_open_work(object(), workline_id=7, version=4)

    assert result.version == 4
    assert workline.version == 4
    assert result.archived_total == 0


@pytest.mark.asyncio
async def test_archive_open_work_rejects_missing_or_stale_workline() -> None:
    service, _, worklines, picking_tasks, business_archiver, reservation_archiver = _setup_service()
    worklines.get_for_update.return_value = None
    with pytest.raises(WorkLineArchiveNotFoundError):
        await service.archive_open_work(object(), workline_id=7, version=4)

    worklines.get_for_update.return_value = SimpleNamespace(version=5)
    with pytest.raises(WorkLineArchiveVersionConflictError):
        await service.archive_open_work(object(), workline_id=7, version=4)

    picking_tasks.archive_open_for_workline.assert_not_awaited()
    business_archiver.archive_open_work.assert_not_awaited()
    reservation_archiver.archive_active_for_workline.assert_not_awaited()


def _setup_single_task_service(
    *,
    task_status: PickingTaskStatus = PickingTaskStatus.EXECUTING,
    task_workline_id: int = 7,
    task_pk: int = 99,
):
    workline = WorkLine(
        id=7,
        line_code="WL-7",
        line_name="七号线",
        line_type=LineType.MANUAL,
        version=4,
        is_active=True,
        plugin_key="manual-picking",
        plugin_version="1.0.0",
    )
    worklines = AsyncMock()
    worklines.get_for_update.return_value = workline

    async def advance_version(_db: object, target: WorkLine) -> WorkLine:
        target.increment_version()
        return target

    worklines.advance_version_for_archive.side_effect = advance_version
    picking_tasks = AsyncMock()
    picking_tasks.archive_single.return_value = None
    business_archiver = AsyncMock()
    business_archiver.archive_open_work.return_value = 0
    reservation_archiver = AsyncMock()
    reservation_archiver.archive_active_for_workline.return_value = 0
    plugin = SimpleNamespace(
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        business_archiver=business_archiver,
    )
    service = WorkLineArchiveService(
        plugins=(plugin,),
        workline_repository=worklines,
        picking_task_repository=picking_tasks,
        reservation_archiver=reservation_archiver,
    )
    task = SimpleNamespace(
        id=task_pk,
        workline_id=task_workline_id,
        status=task_status,
    )
    return service, workline, worklines, picking_tasks, business_archiver, reservation_archiver, task


@pytest.mark.asyncio
async def test_archive_picking_task_archives_single_executing_task_and_bumps_version() -> None:
    service, workline, worklines, picking_tasks, _, _, task = _setup_single_task_service()
    picking_tasks.get_by_id_for_update.return_value = task
    db = object()
    archived_at = datetime(2026, 9, 19, 3, 30, tzinfo=UTC)

    result = await service.archive_picking_task(db, workline_id=7, version=4, picking_task_id=99, now=archived_at)

    worklines.get_for_update.assert_awaited_once_with(db, 7)
    picking_tasks.get_by_id_for_update.assert_awaited_once_with(db, 99)
    picking_tasks.lock_task_identity.assert_not_awaited()
    picking_tasks.archive_single.assert_awaited_once_with(db, task=task, archived_at=archived_at.replace(tzinfo=None))
    worklines.advance_version_for_archive.assert_awaited_once_with(db, workline)
    assert result.workline_id == 7
    assert result.version == 5
    assert result.archived_single_picking_task is True
    assert result.archived_single_picking_task_id == 99
    assert result.picking_task_status_before == PickingTaskStatus.EXECUTING
    assert result.archived_total == 0


@pytest.mark.asyncio
async def test_archive_picking_task_by_business_task_id_uses_advisory_lock() -> None:
    service, _, _, picking_tasks, _, _, task = _setup_single_task_service()
    picking_tasks.get_by_task_id_for_update.return_value = task
    db = object()

    await service.archive_picking_task(db, workline_id=7, version=4, task_id="BUSINESS-123")

    picking_tasks.lock_task_identity.assert_awaited_once_with(db, "BUSINESS-123")
    picking_tasks.get_by_task_id_for_update.assert_awaited_once_with(db, "BUSINESS-123")
    picking_tasks.get_by_id_for_update.assert_not_awaited()
    picking_tasks.archive_single.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_picking_task_rejects_cross_workline_task_with_not_found() -> None:
    service, _, _, picking_tasks, _, _, task = _setup_single_task_service(task_workline_id=999)
    picking_tasks.get_by_id_for_update.return_value = task
    db = object()

    with pytest.raises(WorkLineArchivePickingTaskNotFoundError):
        await service.archive_picking_task(db, workline_id=7, version=4, picking_task_id=99)

    picking_tasks.archive_single.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_value",),
    [
        (PickingTaskStatus.QUEUED,),
        (PickingTaskStatus.CANCELLED,),
        (PickingTaskStatus.ARCHIVED,),
    ],
)
async def test_archive_picking_task_rejects_invalid_status_with_invalid_state_error(
    status_value: PickingTaskStatus,
) -> None:
    service, _, _, picking_tasks, _, _, task = _setup_single_task_service(task_status=status_value)
    picking_tasks.get_by_id_for_update.return_value = task
    db = object()

    with pytest.raises(WorkLineArchivePickingTaskInvalidStateError):
        await service.archive_picking_task(db, workline_id=7, version=4, picking_task_id=99)

    picking_tasks.archive_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_picking_task_rejects_stale_workline_version() -> None:
    service, _, worklines, picking_tasks, _, _, _ = _setup_single_task_service()
    worklines.get_for_update.return_value = SimpleNamespace(version=5)
    db = object()

    with pytest.raises(WorkLineArchiveVersionConflictError):
        await service.archive_picking_task(db, workline_id=7, version=4, picking_task_id=99)

    picking_tasks.archive_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_picking_task_rejects_missing_workline() -> None:
    service, _, worklines, picking_tasks, _, _, _ = _setup_single_task_service()
    worklines.get_for_update.return_value = None
    db = object()

    with pytest.raises(WorkLineArchiveNotFoundError):
        await service.archive_picking_task(db, workline_id=7, version=4, picking_task_id=99)

    picking_tasks.archive_single.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("picking_task_id", "task_id"),
    [
        (None, None),
        (99, "BUSINESS-123"),
    ],
)
async def test_archive_picking_task_rejects_ambiguous_or_missing_identifier(
    picking_task_id: int | None, task_id: str | None
) -> None:
    service, _, _, picking_tasks, _, _, _ = _setup_single_task_service()
    db = object()

    with pytest.raises(ValueError, match="二选一"):
        await service.archive_picking_task(
            db, workline_id=7, version=4, picking_task_id=picking_task_id, task_id=task_id
        )

    picking_tasks.archive_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_picking_task_rejects_nonexistent_task_with_not_found() -> None:
    service, _, _, picking_tasks, _, _, _ = _setup_single_task_service()
    picking_tasks.get_by_id_for_update.return_value = None
    db = object()

    with pytest.raises(WorkLineArchivePickingTaskNotFoundError):
        await service.archive_picking_task(db, workline_id=7, version=4, picking_task_id=99)

    picking_tasks.archive_single.assert_not_awaited()
