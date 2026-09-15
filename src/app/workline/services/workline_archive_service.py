"""作业线业务任务归档服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import (
    PickingTaskRepository,
    picking_task_repository,
)
from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin_version
from src.app.workline.repositories.workline_repository import WorkLineRepository, workline_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime


class WorkLineArchiveNotFoundError(LookupError):
    """目标 WorkLine 不存在。"""


class WorkLineArchiveVersionConflictError(ValueError):
    """请求使用了过期的 WorkLine version。"""


class WorkLineArchiveConfigurationError(ValueError):
    """当前冻结插件不能执行其业务归档。"""


@dataclass(frozen=True, slots=True)
class WorkLineArchiveResult:
    workline_id: int
    version: int
    archived_picking_tasks: int
    archived_plugin_tasks: int
    archived_integration_runs: int

    @property
    def archived_total(self) -> int:
        return self.archived_picking_tasks + self.archived_plugin_tasks + self.archived_integration_runs


class WorkLineArchiveService:
    """在 WorkLine 行锁内归档已绑定任务、插件业务与联调 run。"""

    def __init__(
        self,
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        workline_repository: WorkLineRepository | Any = workline_repository,
        picking_task_repository: PickingTaskRepository | Any = picking_task_repository,
        reservation_archiver: Any | None = None,
    ) -> None:
        self._plugins = plugins
        self._worklines = workline_repository
        self._picking_tasks = picking_task_repository
        self._reservation_archiver = reservation_archiver

    async def archive_open_work(
        self,
        db: Any,
        *,
        workline_id: int,
        version: int,
        now: datetime | None = None,
    ) -> WorkLineArchiveResult:
        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise WorkLineArchiveNotFoundError(f"WorkLine {workline_id} 不存在")
        if type(version) is not int or version != workline.version:
            raise WorkLineArchiveVersionConflictError(f"WorkLine {workline_id} 版本已变化，请重新读取状态")

        archived_at = timezone.to_db_datetime(now) if now is not None else timezone.now_for_db()
        if archived_at is None:
            raise ValueError("now 必须是有效时间")
        archiver = self._resolve_business_archiver(workline)
        archived_picking_tasks = await self._picking_tasks.archive_open_for_workline(
            db,
            workline_id=workline_id,
            archived_at=archived_at,
        )
        archived_plugin_tasks = (
            await archiver.archive_open_work(db, workline_id=workline_id, archived_at=archived_at)
            if archiver is not None
            else 0
        )
        archived_integration_runs = (
            await self._reservation_archiver.archive_active_for_workline(
                db,
                workline_id=workline_id,
                archived_at=archived_at,
            )
            if self._reservation_archiver is not None
            else 0
        )
        if archived_picking_tasks or archived_plugin_tasks or archived_integration_runs:
            workline = await self._worklines.advance_version_for_archive(db, workline)
        return WorkLineArchiveResult(
            workline_id=workline_id,
            version=workline.version,
            archived_picking_tasks=archived_picking_tasks,
            archived_plugin_tasks=archived_plugin_tasks,
            archived_integration_runs=archived_integration_runs,
        )

    def _resolve_business_archiver(self, workline: Any) -> Any | None:
        if workline.plugin_key is None:
            return None
        if not workline.plugin_version:
            raise WorkLineArchiveConfigurationError("WorkLine 当前插件版本未冻结")
        try:
            plugin = resolve_installed_plugin_version(
                self._plugins,
                workline.plugin_key,
                workline.plugin_version,
            )
        except (LookupError, ValueError) as exc:
            raise WorkLineArchiveConfigurationError(str(exc)) from exc
        return plugin.business_archiver


__all__ = [
    "WorkLineArchiveConfigurationError",
    "WorkLineArchiveNotFoundError",
    "WorkLineArchiveResult",
    "WorkLineArchiveService",
    "WorkLineArchiveVersionConflictError",
]
