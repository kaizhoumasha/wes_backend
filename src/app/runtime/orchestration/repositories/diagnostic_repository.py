"""WorklineDiagnostic Repository 层。"""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.runtime.orchestration.models.diagnostic import DiagnosticStatus, WorklineDiagnostic
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

    @staticmethod
    def _insert_values(data: dict[str, Any]) -> dict[str, Any]:
        diagnostic = WorklineDiagnostic(**data)
        table = cast("Any", WorklineDiagnostic).__table__
        values: dict[str, Any] = {}
        for column in table.columns:
            value = getattr(diagnostic, column.name)
            if column.primary_key and value is None:
                continue
            values[column.name] = value
        return values

    async def create_idempotent_by_diagnostic_key(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> WorklineDiagnostic:
        """按 diagnostic_key 原子创建；冲突时返回已有记录，不回滚当前事务。"""

        diagnostic_key = str(data["diagnostic_key"])
        table = cast("Any", WorklineDiagnostic).__table__
        dialect_name = db.get_bind().dialect.name
        insert_fn = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert_fn(table)
            .values(**self._insert_values(data))
            .on_conflict_do_nothing(index_elements=[table.c.diagnostic_key])
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        created_id = result.scalar_one_or_none()
        if isinstance(created_id, int):
            created = await self.get_by_id(db, created_id)
            if created is not None:
                return created

        existing = await self.get_by_diagnostic_key(db, diagnostic_key)
        if existing is not None:
            return existing
        raise RuntimeError(f"创建工作线诊断失败: {diagnostic_key}")

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
