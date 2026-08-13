"""Device evidence 的唯一持久化 owner。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.device.models.evidence import (
    DeviceEvidence,
    DeviceEvidenceApplyStatus,
    DeviceEvidenceConflict,
    DeviceEvidenceKind,
    DeviceStatusObservation,
)
from src.database.base_repository import BaseRepository


class DeviceEvidenceRepository(BaseRepository[DeviceEvidence]):
    """统一 evidence identity、命令终态和冲突写入。"""

    def __init__(self) -> None:
        super().__init__(DeviceEvidence)

    async def lock_source_event_id(self, db: AsyncSession, source_event_id: str) -> None:
        """串行化首次插入竞争；不存在的 evidence 行本身无法被 ``FOR UPDATE`` 锁定。"""

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:source_event_id, 0))"),
            {"source_event_id": source_event_id},
        )

    async def get_by_source_event_id_for_update(
        self,
        db: AsyncSession,
        source_event_id: str,
    ) -> DeviceEvidence | None:
        columns = cast("Any", DeviceEvidence).__table__.c
        result = await db.execute(
            select(DeviceEvidence).where(columns.source_event_id == source_event_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_result_for_command_for_update(
        self,
        db: AsyncSession,
        command_code: str,
    ) -> DeviceEvidence | None:
        columns = cast("Any", DeviceEvidence).__table__.c
        result = await db.execute(
            select(DeviceEvidence)
            .where(columns.command_code == command_code, columns.kind == DeviceEvidenceKind.RESULT)
            .order_by(columns.id)
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, evidence: DeviceEvidence) -> DeviceEvidence:
        db.add(evidence)
        await db.flush()
        return evidence

    async def add_conflict(
        self,
        db: AsyncSession,
        conflict: DeviceEvidenceConflict,
    ) -> DeviceEvidenceConflict:
        db.add(conflict)
        await db.flush()
        return conflict

    async def add_status_observation(
        self,
        db: AsyncSession,
        observation: DeviceStatusObservation,
    ) -> DeviceStatusObservation:
        db.add(observation)
        await db.flush()
        return observation

    async def claim_next_pending(self, db: AsyncSession) -> DeviceEvidence | None:
        columns = cast("Any", DeviceEvidence).__table__.c
        result = await db.execute(
            select(DeviceEvidence)
            .where(columns.apply_status == DeviceEvidenceApplyStatus.PENDING)
            .order_by(columns.received_at, columns.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def mark_applied(self, db: AsyncSession, evidence: DeviceEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = DeviceEvidenceApplyStatus.APPLIED
        evidence.processed_at = processed_at
        await db.flush()

    async def mark_reconciling(
        self,
        db: AsyncSession,
        evidence: DeviceEvidence,
        *,
        processed_at: datetime,
    ) -> None:
        evidence.apply_status = DeviceEvidenceApplyStatus.RECONCILING
        evidence.processed_at = processed_at
        await db.flush()


device_evidence_repository = DeviceEvidenceRepository()

__all__ = ["DeviceEvidenceRepository", "device_evidence_repository"]
