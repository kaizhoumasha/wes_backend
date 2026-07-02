"""WMS evidence Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from src.app.wms_integration.models import WmsCallEvidence, WmsCallEvidenceArchive, WmsEvidenceStatus
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class WmsCallEvidenceRepository(BaseRepository[WmsCallEvidence]):
    """WMS 调用证据数据访问层。"""

    def __init__(self) -> None:
        super().__init__(WmsCallEvidence)

    async def get_by_evidence_key(self, db: AsyncSession, evidence_key: str) -> WmsCallEvidence | None:
        """按 evidence_key 查询唯一证据。"""

        columns = cast("Any", WmsCallEvidence).__table__.c
        result = await db.execute(select(WmsCallEvidence).where(columns.evidence_key == evidence_key))
        return result.scalar_one_or_none()

    async def list_recent_for_drift_scan(
        self,
        db: AsyncSession,
        *,
        limit: int = 500,
        operation_name: str | None = None,
    ) -> list[WmsCallEvidence]:
        """读取最近 evidence，供 WMS drift job 做只读分类。"""

        columns = cast("Any", WmsCallEvidence).__table__.c
        stmt = select(WmsCallEvidence).order_by(columns.started_at.desc(), columns.id.desc()).limit(max(1, limit))
        if operation_name is not None:
            stmt = stmt.where(columns.operation_name == operation_name)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def list_expired_for_archive(
        self,
        db: AsyncSession,
        *,
        cutoff_at: datetime,
        limit: int = 500,
    ) -> list[WmsCallEvidence]:
        """读取已超过保留期且非 in-flight 的 evidence。"""

        columns = cast("Any", WmsCallEvidence).__table__.c
        stmt = (
            select(WmsCallEvidence)
            .where(columns.started_at < cutoff_at)
            .where(columns.status != WmsEvidenceStatus.STARTED)
            .order_by(columns.started_at.asc(), columns.id.asc())
            .limit(max(1, limit))
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_ids(self, db: AsyncSession, ids: list[int]) -> int:
        """按主键批量物理删除热表 evidence。"""

        if not ids:
            return 0
        columns = cast("Any", WmsCallEvidence).__table__.c
        result = await db.execute(delete(WmsCallEvidence).where(columns.id.in_(ids)))
        await db.flush()
        return int(result.rowcount or 0)


class WmsCallEvidenceArchiveRepository(BaseRepository[WmsCallEvidenceArchive]):
    """WMS 调用证据归档数据访问层。"""

    def __init__(self) -> None:
        super().__init__(WmsCallEvidenceArchive)

    async def get_by_evidence_key(self, db: AsyncSession, evidence_key: str) -> WmsCallEvidenceArchive | None:
        """按 evidence_key 查询唯一归档证据。"""

        columns = cast("Any", WmsCallEvidenceArchive).__table__.c
        result = await db.execute(select(WmsCallEvidenceArchive).where(columns.evidence_key == evidence_key))
        return result.scalar_one_or_none()

    async def get_by_original_evidence_id(
        self,
        db: AsyncSession,
        original_evidence_id: int,
    ) -> WmsCallEvidenceArchive | None:
        """按原始 evidence ID 查询唯一归档证据。"""

        columns = cast("Any", WmsCallEvidenceArchive).__table__.c
        result = await db.execute(
            select(WmsCallEvidenceArchive).where(columns.original_evidence_id == original_evidence_id)
        )
        return result.scalar_one_or_none()


wms_call_evidence_repository = WmsCallEvidenceRepository()
wms_call_evidence_archive_repository = WmsCallEvidenceArchiveRepository()


__all__ = [
    "WmsCallEvidenceArchiveRepository",
    "WmsCallEvidenceRepository",
    "wms_call_evidence_archive_repository",
    "wms_call_evidence_repository",
]
