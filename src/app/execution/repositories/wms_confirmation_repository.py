"""WmsConfirmation 持久化 owner。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import and_, case, exists, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from sqlalchemy.orm import aliased

from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.database.base_repository import BaseRepository


class WmsConfirmationRepository(BaseRepository[WmsConfirmation]):
    def __init__(self) -> None:
        super().__init__(WmsConfirmation)

    async def lock_identity(self, db: AsyncSession, operation: str, operation_id: str) -> None:
        identity = f"{operation}\x1f{operation_id}"
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": identity},
        )

    async def get_by_identity_for_update(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
    ) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(columns.operation == operation, columns.operation_id == operation_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, confirmation: WmsConfirmation) -> WmsConfirmation:
        db.add(confirmation)
        await db.flush()
        return confirmation

    async def list_for_execution(
        self,
        db: AsyncSession,
        material_execution_id: int,
    ) -> list[WmsConfirmation]:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(columns.material_execution_id == material_execution_id)
            .order_by(columns.created_at, columns.id)
        )
        return list(result.scalars())

    async def list_for_executions_for_update(
        self,
        db: AsyncSession,
        *,
        material_execution_ids: tuple[int, ...],
        operation: str,
    ) -> list[WmsConfirmation]:
        if not material_execution_ids:
            return []
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(
                columns.material_execution_id.in_(material_execution_ids),
                columns.operation == operation,
            )
            .order_by(columns.created_at, columns.id)
            .with_for_update()
        )
        return list(result.scalars())

    async def list_for_execution_operations_for_update(
        self,
        db: AsyncSession,
        *,
        material_execution_id: int,
        operations: tuple[str, ...],
    ) -> list[WmsConfirmation]:
        """按冻结 operation 顺序锁定 execution 的外部确认义务。"""

        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(
                columns.material_execution_id == material_execution_id,
                columns.operation.in_(operations),
            )
            .order_by(case({operation: index for index, operation in enumerate(operations)}, value=columns.operation))
            .with_for_update()
        )
        return list(result.scalars())

    async def claim_eligible(
        self,
        db: AsyncSession,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> list[WmsConfirmation]:
        columns = cast("Any", WmsConfirmation).__table__.c
        execution_columns = cast("Any", MaterialExecution).__table__.c
        earlier_execution = aliased(MaterialExecution)
        earlier_columns = cast("Any", earlier_execution)
        predecessor = aliased(WmsConfirmation)
        predecessor_columns = cast("Any", predecessor)
        e03 = "wms.inventory.confirm_inbound@v1"
        e07 = "wms.fulfillment.notify_pkg_binding@v1"
        no_earlier_active_execution = ~exists(
            select(1).where(
                earlier_columns.workline_id == execution_columns.workline_id,
                earlier_columns.line_run_epoch_id == execution_columns.line_run_epoch_id,
                earlier_columns.status != MaterialExecutionStatus.CLOSED,
                or_(
                    earlier_columns.admission_received_at.is_(None),
                    earlier_columns.admission_evidence_id.is_(None),
                    tuple_(
                        earlier_columns.admission_received_at,
                        earlier_columns.admission_evidence_id,
                        earlier_columns.id,
                    )
                    < tuple_(
                        execution_columns.admission_received_at,
                        execution_columns.admission_evidence_id,
                        execution_columns.id,
                    ),
                ),
            )
        )
        completed_e03 = exists(
            select(1).where(
                predecessor_columns.material_execution_id == columns.material_execution_id,
                predecessor_columns.operation == e03,
                predecessor_columns.status == WmsConfirmationStatus.COMPLETED,
            )
        )
        execution_barrier = or_(
            columns.operation.not_in((e03, e07)),
            and_(
                execution_columns.admission_received_at.is_not(None),
                execution_columns.admission_evidence_id.is_not(None),
                no_earlier_active_execution,
                or_(columns.operation == e03, and_(columns.operation == e07, completed_e03)),
            ),
        )
        result = await db.execute(
            select(WmsConfirmation)
            .join(MaterialExecution, execution_columns.id == columns.material_execution_id)
            .where(
                or_(
                    and_(
                        columns.status == WmsConfirmationStatus.PENDING,
                        or_(columns.next_attempt_at.is_(None), columns.next_attempt_at <= now),
                    ),
                    and_(
                        columns.status == WmsConfirmationStatus.DISPATCHING,
                        columns.claim_expires_at.is_not(None),
                        columns.claim_expires_at <= now,
                    ),
                ),
                execution_barrier,
            )
            .order_by(
                case((columns.operation.in_((e03, e07)), 0), else_=1),
                execution_columns.workline_id,
                execution_columns.line_run_epoch_id,
                execution_columns.admission_received_at,
                execution_columns.admission_evidence_id,
                execution_columns.id,
                case({e03: 0, e07: 1}, value=columns.operation, else_=2),
                columns.created_at,
                columns.id,
            )
            .limit(limit)
            .with_for_update(of=WmsConfirmation, skip_locked=True)
        )
        confirmations = list(result.scalars())
        for confirmation in confirmations:
            confirmation.status = WmsConfirmationStatus.DISPATCHING
            confirmation.claim_token = claim_token
            confirmation.claimed_at = now
            confirmation.claim_expires_at = claim_expires_at
            confirmation.last_dispatch_at = now
            confirmation.attempt_count += 1
        await db.flush()
        return confirmations

    async def get_claimed_for_update(
        self,
        db: AsyncSession,
        confirmation_id: int,
        claim_token: str,
    ) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(
                columns.id == confirmation_id,
                columns.status == WmsConfirmationStatus.DISPATCHING,
                columns.claim_token == claim_token,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


wms_confirmation_repository = WmsConfirmationRepository()

__all__ = ["WmsConfirmationRepository", "wms_confirmation_repository"]
