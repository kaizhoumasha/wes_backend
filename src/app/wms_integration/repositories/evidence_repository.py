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


wms_call_evidence_repository = WmsCallEvidenceRepository()


__all__ = [
    "WmsCallEvidenceRepository",
    "wms_call_evidence_repository",
]
