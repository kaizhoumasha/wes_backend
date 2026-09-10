"""可靠事实查询应用边界，不触发派发、插件或恢复动作。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.execution.observation import ConfirmationObservation, EvidenceObservation
from src.app.execution.repositories.execution_observation_repository import ExecutionObservationRepository
from src.database.db import get_db_context
from src.utils.timezone import timezone


def _optional_utc(value: datetime | None) -> str | None:
    return timezone.to_utc(value).isoformat() if value is not None else None


class ExecutionObservationService:
    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        repository: ExecutionObservationRepository,
    ) -> None:
        self._sessions = session_factory
        self._repository = repository

    async def get_confirmation(self, operation: str, operation_id: str) -> ConfirmationObservation | None:
        async with self._sessions() as db:
            record = await self._repository.get_confirmation(db, operation, operation_id)
            if record is None:
                return None
            return ConfirmationObservation(
                operation=record.operation,
                operation_id=record.operation_id,
                status=record.status,
                attempt_count=record.attempt_count,
                retry_eligible=record.retry_eligible,
                next_attempt_at=_optional_utc(record.next_attempt_at),
                deadline_at=timezone.to_utc(record.deadline_at).isoformat(),
                last_dispatch_at=_optional_utc(record.last_dispatch_at),
                response_evidence_id=record.response_evidence_id,
                response_result=record.response_result,
                updated_at=_optional_utc(record.updated_at),
            )

    async def get_evidence(self, operation: str, operation_id: str) -> EvidenceObservation | None:
        async with self._sessions() as db:
            record = await self._repository.get_evidence(db, operation, operation_id)
            if record is None:
                return None
            return EvidenceObservation(
                operation=operation,
                operation_id=operation_id,
                apply_status=record.apply_status,
                received_at=timezone.to_utc(record.received_at).isoformat(),
                processed_at=_optional_utc(record.processed_at),
                published_at=_optional_utc(record.published_at),
                decision_attempt_count=record.decision_attempt_count,
                decision_next_attempt_at=_optional_utc(record.decision_next_attempt_at),
            )


execution_observation_service = ExecutionObservationService(get_db_context, ExecutionObservationRepository())
