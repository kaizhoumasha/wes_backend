"""非生产调试过程数据清理服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.workline.models.operation import DebugDataCleanupResponse
from src.app.workline.repositories.debug_data_cleanup_repository import (
    DebugDataCleanupRepository,
    DebugDataCleanupSelection,
    debug_data_cleanup_repository,
)
from src.core.conf import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.models import WorkLine


ALL_CONFIRMATION = "CLEAR-ALL-DEBUG-DATA"
NON_PROD_ENVS = {"dev", "test"}


class DebugDataCleanupService:
    """非生产调试过程数据清理服务。"""

    def __init__(self, repository: DebugDataCleanupRepository | None = None) -> None:
        self._repository = repository or debug_data_cleanup_repository

    async def preview_workline(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
    ) -> DebugDataCleanupResponse:
        """返回单条工作线调试过程数据清理预览。"""

        self._require_non_prod_environment()
        workline = await self._get_required_workline(db, workline_id)
        selection = await self._repository.collect_for_workline(db, workline_id)
        return self._response(
            scope="WORKLINE",
            workline_id=workline_id,
            selection=selection,
            dry_run=True,
            deleted=False,
            message=f"已预览工作线 {workline.line_code} 调试过程数据清理范围，未删除数据",
        )

    async def cleanup_workline(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        confirmation: str | None,
    ) -> DebugDataCleanupResponse:
        """执行单条工作线调试过程数据清理。"""

        self._require_non_prod_environment()
        workline = await self._repository.get_workline_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: workline_id={workline_id}")
        self._require_workline_confirmation(workline, confirmation)

        selection = await self._repository.collect_for_workline(db, workline_id)
        await self._repository.execute_cleanup(db, selection=selection)
        return self._response(
            scope="WORKLINE",
            workline_id=workline_id,
            selection=selection,
            dry_run=False,
            deleted=True,
            message=f"已清理工作线 {workline.line_code} 的调试过程数据，未修改工作线与设备元数据",
        )

    async def preview_all(self, db: AsyncSession) -> DebugDataCleanupResponse:
        """返回全部工作线调试过程数据清理预览。"""

        self._require_non_prod_environment()
        selection = await self._repository.collect_for_all_worklines(db)
        return self._response(
            scope="ALL",
            workline_id=None,
            selection=selection,
            dry_run=True,
            deleted=False,
            message="已预览全部工作线调试过程数据清理范围，未删除数据",
        )

    async def cleanup_all(
        self,
        db: AsyncSession,
        *,
        confirmation: str | None,
    ) -> DebugDataCleanupResponse:
        """执行全部工作线调试过程数据清理。"""

        self._require_non_prod_environment()
        if confirmation != ALL_CONFIRMATION:
            raise ValueError(f"清理确认失败：confirmation 必须等于 {ALL_CONFIRMATION}")

        selection = await self._repository.collect_for_all_worklines(db)
        await self._repository.execute_cleanup(db, selection=selection)
        return self._response(
            scope="ALL",
            workline_id=None,
            selection=selection,
            dry_run=False,
            deleted=True,
            message="已清理全部工作线调试过程数据，未修改工作线与设备元数据",
        )

    async def _get_required_workline(self, db: AsyncSession, workline_id: int) -> WorkLine:
        workline = await self._repository.get_workline(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: workline_id={workline_id}")
        return workline

    def _require_non_prod_environment(self) -> None:
        if settings.APP_ENV not in NON_PROD_ENVS:
            raise ValueError("仅允许在非生产环境清理调试过程数据")

    def _require_workline_confirmation(self, workline: WorkLine, confirmation: str | None) -> None:
        if confirmation != workline.line_code:
            raise ValueError("清理确认失败：confirmation 必须等于工作线编码")

    def _response(
        self,
        *,
        scope: str,
        workline_id: int | None,
        selection: DebugDataCleanupSelection,
        dry_run: bool,
        deleted: bool,
        message: str,
    ) -> DebugDataCleanupResponse:
        return DebugDataCleanupResponse(
            scope=scope,
            workline_id=workline_id,
            dry_run=dry_run,
            deleted=deleted,
            counts=selection.counts(),
            affected_workline_ids=selection.worklines,
            affected_session_ids=selection.sessions,
            message=message,
        )


debug_data_cleanup_service = DebugDataCleanupService()


__all__ = ["ALL_CONFIRMATION", "DebugDataCleanupService", "debug_data_cleanup_service"]
