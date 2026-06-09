"""WorklineDiagnostic Repository 层。"""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.diagnostic import DiagnosticStatus, WorklineDiagnostic
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


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

    async def resolve_entry_admission_by_inbox_id(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
    ) -> int:
        """将已成功重试的入口准入阻塞诊断标记为已解决。"""

        columns = cast("Any", WorklineDiagnostic).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            update(WorklineDiagnostic)
            .where(
                columns.inbox_id == inbox_id,
                columns.status == DiagnosticStatus.ACTIVE.value,
                columns.evidence_json["reason"].as_string() == "WORKLINE_ENTRY_ADMISSION_BLOCKED",
            )
            .values(
                status=DiagnosticStatus.RESOLVED,
                resolved_at=now,
                updated_at=now,
            )
        )
        return int(result.rowcount or 0)


workline_diagnostic_repository = WorklineDiagnosticRepository()


__all__ = ["WorklineDiagnosticRepository", "workline_diagnostic_repository"]
