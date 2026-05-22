"""沙箱工作线清理服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.workline.models.operation import SandboxCleanupResponse
from src.app.workline.repositories.sandbox_cleanup_repository import (
    SandboxCleanupRepository,
    sandbox_cleanup_repository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.models import WorkLine


class SandboxCleanupService:
    """沙箱工作线清理服务。

    负责预览和清理单条 SIMULATION 工作线的沙箱运行时数据。
    """

    def __init__(self, repository: SandboxCleanupRepository | None = None) -> None:
        self._repository = repository or sandbox_cleanup_repository

    async def preview_cleanup(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
    ) -> SandboxCleanupResponse:
        """返回沙箱清理预览响应。"""

        workline = await self._repository.get_workline(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: workline_id={workline_id}")
        self._require_simulation_workline(workline)
        selection = await self._repository.collect_selection(db, workline_id)

        return SandboxCleanupResponse(
            workline_id=workline_id,
            dry_run=True,
            deleted=False,
            counts=selection.counts(),
            affected_session_ids=selection.sessions,
            message="已预览工作线沙箱清理范围，未删除数据",
        )

    async def cleanup_workline(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        confirmation: str | None,
    ) -> SandboxCleanupResponse:
        """执行沙箱清理并返回删除结果。"""

        workline = await self._repository.get_workline_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: workline_id={workline_id}")
        self._require_simulation_workline(workline)
        if confirmation != workline.line_code:
            raise ValueError("清理确认失败：confirmation 必须等于工作线编码")

        selection = await self._repository.collect_selection(db, workline_id)
        await self._repository.execute_cleanup(db, workline_id=workline_id, selection=selection)

        return SandboxCleanupResponse(
            workline_id=workline_id,
            dry_run=False,
            deleted=True,
            counts=selection.counts(),
            affected_session_ids=selection.sessions,
            message="已清理该 SIMULATION 工作线的沙箱运行时数据，并重置工作线运行状态",
        )

    def _require_simulation_workline(self, workline: WorkLine) -> None:
        if not self._repository.is_simulation_workline(workline):
            raise ValueError(
                f"仅允许 SIMULATION 工作线执行沙箱清理: workline_id={workline.id}, run_mode={workline.run_mode}"
            )


sandbox_cleanup_service = SandboxCleanupService()


__all__ = ["SandboxCleanupService", "sandbox_cleanup_service"]
