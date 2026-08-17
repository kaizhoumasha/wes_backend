"""批量对账 evidence 与 execution 冻结关联的持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select

from src.app.execution.models import InboundEvidenceExecutionBinding
from src.database.base_repository import BaseRepository


class InboundEvidenceExecutionBindingRepository(BaseRepository[InboundEvidenceExecutionBinding]):
    def __init__(self) -> None:
        super().__init__(InboundEvidenceExecutionBinding)

    async def list_for_evidence_for_update(
        self,
        db: object,
        evidence_id: int,
    ) -> list[InboundEvidenceExecutionBinding]:
        columns = cast("Any", InboundEvidenceExecutionBinding).__table__.c
        result = await db.execute(
            select(InboundEvidenceExecutionBinding)
            .where(columns.inbound_evidence_id == evidence_id)
            .order_by(columns.ordinal)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def add(
        self,
        db: object,
        binding: InboundEvidenceExecutionBinding,
    ) -> InboundEvidenceExecutionBinding:
        db.add(binding)
        await db.flush()
        return binding


inbound_evidence_execution_binding_repository = InboundEvidenceExecutionBindingRepository()

__all__ = [
    "InboundEvidenceExecutionBindingRepository",
    "inbound_evidence_execution_binding_repository",
]
