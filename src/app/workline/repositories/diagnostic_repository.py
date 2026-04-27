"""WorklineDiagnostic Repository 层。"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.database.base_repository import BaseRepository


class WorklineDiagnosticRepository(BaseRepository[WorklineDiagnostic]):
    """工作线诊断数据访问层。"""

    def __init__(self) -> None:
        super().__init__(WorklineDiagnostic)

    async def get_by_diagnostic_key(self, db: AsyncSession, diagnostic_key: str) -> WorklineDiagnostic | None:
        """按幂等键查询诊断。"""

        columns = cast("Any", WorklineDiagnostic).__table__.c
        result = await db.execute(select(WorklineDiagnostic).where(columns.diagnostic_key == diagnostic_key))
        return result.scalar_one_or_none()

    async def get_active_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[WorklineDiagnostic]:
        """按 trace_id 查询活跃诊断。"""

        columns = cast("Any", WorklineDiagnostic).__table__.c
        result = await db.execute(
            select(WorklineDiagnostic)
            .where(
                columns.trace_id == trace_id,
                columns.status == "ACTIVE",
            )
            .order_by(columns.created_at.desc())
        )
        return list(result.scalars().all())


workline_diagnostic_repository = WorklineDiagnosticRepository()


__all__ = ["WorklineDiagnosticRepository", "workline_diagnostic_repository"]
