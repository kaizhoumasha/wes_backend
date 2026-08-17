"""InboundEvidence 身份、冲突与应用状态 owner。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import and_, exists, not_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.database.base_repository import BaseRepository


class InboundEvidenceRepository(BaseRepository[InboundEvidence]):
    def __init__(self) -> None:
        super().__init__(InboundEvidence)

    async def lock_source_identity(self, db: AsyncSession, source_identity: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:source_identity, 0))"),
            {"source_identity": source_identity},
        )

    async def get_by_source_identity_for_update(
        self,
        db: AsyncSession,
        source_identity: str,
    ) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        result = await db.execute(
            select(InboundEvidence).where(columns.source_identity == source_identity).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, db: AsyncSession, evidence_id: int) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        result = await db.execute(select(InboundEvidence).where(columns.id == evidence_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_device_result_for_command_for_update(
        self,
        db: AsyncSession,
        command_code: str,
    ) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        result = await db.execute(
            select(InboundEvidence)
            .where(
                columns.command_code == command_code,
                columns.kind == InboundEvidenceKind.DEVICE_RESULT,
            )
            .order_by(columns.id)
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, evidence: InboundEvidence) -> InboundEvidence:
        db.add(evidence)
        await db.flush()
        return evidence

    async def add_conflict(
        self,
        db: AsyncSession,
        conflict: InboundEvidenceConflict,
    ) -> InboundEvidenceConflict:
        db.add(conflict)
        await db.flush()
        return conflict

    async def claim_next_pending(
        self,
        db: AsyncSession,
        *,
        kinds: tuple[InboundEvidenceKind, ...],
    ) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        result = await db.execute(
            select(InboundEvidence)
            .where(
                columns.apply_status == InboundEvidenceApplyStatus.PENDING,
                columns.kind.in_(kinds),
            )
            .order_by(columns.received_at, columns.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def claim_decision_batch(
        self,
        db: AsyncSession,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> list[InboundEvidence]:
        columns = cast("Any", InboundEvidence).__table__.c
        earlier_transport_outcomes = InboundEvidence.__table__.alias("earlier_transport_outcomes")
        earlier_columns = earlier_transport_outcomes.c
        epoch_columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(
            select(InboundEvidence)
            .join(LineRunEpoch, columns.line_run_epoch_id == epoch_columns.id)
            .where(
                columns.apply_status == InboundEvidenceApplyStatus.APPLIED,
                columns.published_at.is_(None),
                epoch_columns.status == LineRunEpochStatus.ACTIVE,
                not_(
                    and_(
                        columns.kind == InboundEvidenceKind.DEVICE_RESULT,
                        columns.material_execution_id.is_(None),
                    )
                ),
                not_(
                    exists(
                        select(1).where(
                            earlier_columns.kind == InboundEvidenceKind.TRANSPORT_RESULT,
                            earlier_columns.transport_task_id == columns.transport_task_id,
                            earlier_columns.material_execution_id == columns.material_execution_id,
                            earlier_columns.apply_status == InboundEvidenceApplyStatus.APPLIED,
                            earlier_columns.published_at.is_(None),
                            earlier_columns.normalized_payload["status"].as_string() == "UNKNOWN",
                            earlier_columns.normalized_payload["outcome_version"].as_integer()
                            < columns.normalized_payload["outcome_version"].as_integer(),
                        )
                    )
                ),
                (columns.decision_next_attempt_at.is_(None) | (columns.decision_next_attempt_at <= now)),
                (columns.decision_claim_token.is_(None) | (columns.decision_claim_expires_at < now)),
            )
            .order_by(
                columns.decision_next_attempt_at.is_not(None),
                columns.decision_next_attempt_at,
                columns.received_at,
                columns.id,
            )
            .limit(limit)
            .with_for_update(of=InboundEvidence, skip_locked=True)
        )
        evidences = list(result.scalars().all())
        for evidence in evidences:
            evidence.decision_claim_token = claim_token
            evidence.decision_claim_expires_at = claim_expires_at
        await db.flush()
        return evidences

    async def get_decision_claim_for_update(
        self,
        db: AsyncSession,
        *,
        evidence_id: int,
        claim_token: str,
        now: datetime,
    ) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        result = await db.execute(
            select(InboundEvidence)
            .where(
                columns.id == evidence_id,
                columns.apply_status == InboundEvidenceApplyStatus.APPLIED,
                columns.published_at.is_(None),
                columns.decision_claim_token == claim_token,
                columns.decision_claim_expires_at >= now,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()

    async def mark_applied(
        self,
        db: AsyncSession,
        evidence: InboundEvidence,
        *,
        processed_at: datetime,
    ) -> None:
        evidence.apply_status = InboundEvidenceApplyStatus.APPLIED
        evidence.processed_at = processed_at
        await db.flush()

    async def mark_reconciling(
        self,
        db: AsyncSession,
        evidence: InboundEvidence,
        *,
        processed_at: datetime,
    ) -> None:
        evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
        evidence.processed_at = processed_at
        await db.flush()


inbound_evidence_repository = InboundEvidenceRepository()

__all__ = ["InboundEvidenceRepository", "inbound_evidence_repository"]
