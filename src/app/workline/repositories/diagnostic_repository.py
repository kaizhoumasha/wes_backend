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

    async def update_resource_wait_by_key(
        self,
        db: AsyncSession,
        *,
        diagnostic_key: str,
        message: str,
        evidence_json: dict[str, Any],
    ) -> WorklineDiagnostic | None:
        """幂等更新 RESOURCE_WAIT 诊断证据。"""

        columns = cast("Any", WorklineDiagnostic).__table__.c
        await db.execute(
            update(WorklineDiagnostic)
            .where(columns.diagnostic_key == diagnostic_key)
            .values(
                message=message,
                technical_summary=message,
                evidence_json=evidence_json,
                status=DiagnosticStatus.ACTIVE,
                resolved_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        return await self.get_by_diagnostic_key(db, diagnostic_key)

    async def resolve_resource_wait_by_key(
        self,
        db: AsyncSession,
        *,
        diagnostic_key: str,
    ) -> int:
        """按幂等键关闭 ACTIVE RESOURCE_WAIT 诊断。"""

        columns = cast("Any", WorklineDiagnostic).__table__.c
        result = await db.execute(
            update(WorklineDiagnostic)
            .where(
                columns.diagnostic_key == diagnostic_key,
                columns.diagnostic_code == "RESOURCE_WAIT",
                columns.status == DiagnosticStatus.ACTIVE,
            )
            .values(status=DiagnosticStatus.RESOLVED, resolved_at=timezone.now_for_db())
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def resolve_other_active_resource_waits_for_inbox(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        keep_diagnostic_key: str,
    ) -> int:
        """同一 Inbox 转等新资源前，关闭旧 ACTIVE RESOURCE_WAIT。"""

        columns = cast("Any", WorklineDiagnostic).__table__.c
        result = await db.execute(
            update(WorklineDiagnostic)
            .where(
                columns.inbox_id == inbox_id,
                columns.diagnostic_code == "RESOURCE_WAIT",
                columns.status == DiagnosticStatus.ACTIVE,
                columns.diagnostic_key != keep_diagnostic_key,
            )
            .values(status=DiagnosticStatus.RESOLVED, resolved_at=timezone.now_for_db())
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)


workline_diagnostic_repository = WorklineDiagnosticRepository()


__all__ = ["WorklineDiagnosticRepository", "workline_diagnostic_repository"]
