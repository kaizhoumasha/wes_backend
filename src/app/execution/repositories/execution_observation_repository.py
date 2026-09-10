"""仅按持久化身份查询可靠对象，无锁定、领取或业务表查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceKind
from src.app.execution.models.wms_confirmation import WmsConfirmation


class ExecutionObservationRepository:
    async def get_confirmation(self, db: AsyncSession, operation: str, operation_id: str) -> WmsConfirmation | None:
        result = await db.execute(
            select(WmsConfirmation).where(
                col(WmsConfirmation.operation) == operation, col(WmsConfirmation.operation_id) == operation_id
            )
        )
        return result.scalar_one_or_none()

    async def get_evidence(self, db: AsyncSession, operation: str, operation_id: str) -> InboundEvidence | None:
        result = await db.execute(
            select(InboundEvidence).where(
                col(InboundEvidence.operation) == operation,
                col(InboundEvidence.operation_id) == operation_id,
                col(InboundEvidence.kind).in_((InboundEvidenceKind.WMS_EVENT, InboundEvidenceKind.WMS_RESULT)),
            )
        )
        return result.scalar_one_or_none()
