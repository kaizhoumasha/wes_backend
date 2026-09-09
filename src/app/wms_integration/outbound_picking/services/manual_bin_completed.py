"""人工 Bin 完成决定的 Evidence 接收；业务应用留给人工线插件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.outbound_picking.manual_bin_completed_event_handler import (
    ManualBinCompletedPersistenceResult,
)
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import ManualBinCompletedInvalidData
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import ManualBinCompletedEvent


class ManualBinCompletedService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        evidence_service: InboundEvidenceService | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()

    async def record(
        self,
        envelope: ManualBinCompletedEvent | ManualBinCompletedInvalidData,
        *,
        received_at: datetime,
    ) -> ManualBinCompletedPersistenceResult:
        invalid = isinstance(envelope, ManualBinCompletedInvalidData)
        payload = envelope.raw_envelope if invalid else envelope.model_dump(mode="json")
        operation, operation_id = payload["operation"], payload["operation_id"]
        async with self._sessions.begin() as db:
            acceptance = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{operation}:{operation_id}",
                normalized_payload=payload,
                received_at=received_at,
                contract_key=operation,
                contract_version="1.0",
                operation=operation,
                operation_id=operation_id,
                apply_status=(InboundEvidenceApplyStatus.IGNORED if invalid else InboundEvidenceApplyStatus.PENDING),
            )
            evidence = acceptance.evidence
            timestamp_ms = int(timezone.to_utc(evidence.received_at).timestamp() * 1000)
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return ManualBinCompletedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=timestamp_ms,
                    reason_code="IDEMPOTENCY_CONFLICT",
                )
            if invalid:
                evidence.processed_at = evidence.received_at
                return ManualBinCompletedPersistenceResult(
                    code="REJECTED",
                    timestamp_ms=timestamp_ms,
                    reason_code="INVALID_DATA",
                )
            return ManualBinCompletedPersistenceResult(
                code="DUPLICATE" if acceptance.duplicate else "RECEIVED",
                timestamp_ms=timestamp_ms,
            )


__all__ = ["ManualBinCompletedService"]
