"""WMS evidence Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.wms_integration.models import WmsCallEvidence
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
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


wms_call_evidence_repository = WmsCallEvidenceRepository()


__all__ = [
    "WmsCallEvidenceRepository",
    "wms_call_evidence_repository",
]
