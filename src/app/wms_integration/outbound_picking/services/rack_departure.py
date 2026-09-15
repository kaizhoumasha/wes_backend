"""来源货架离场的可靠 WMS 决定及原结果读取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.execution import config
from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.departure_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.departure_wire import (
    RACK_DEPARTURE_OPERATION,
    parse_rack_departure_request,
    parse_rack_departure_response,
)
from src.app.wms_integration.outbound_picking.repositories.rack_departure_repository import RackDepartureRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import RackDepartureIntent, RackDepartureOutcome


@dataclass(frozen=True, slots=True)
class RackDepartureSnapshot:
    status: WmsConfirmationStatus
    intent: RackDepartureIntent
    outcome: RackDepartureOutcome | None
    evidence_id: int | None
    completed_at: datetime | None


class RackDepartureScheduler:
    def __init__(self, confirmations: WmsConfirmationLifecycleService) -> None:
        self._confirmations = confirmations

    async def create_in_session(
        self, db: AsyncSession, intent: RackDepartureIntent, *, picking_task_id: int, created_at: datetime
    ) -> None:
        payload = encode_request(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        result = await self._confirmations.create_or_get(
            db,
            operation=RACK_DEPARTURE_OPERATION,
            operation_id=intent.operation_id,
            picking_task_id=picking_task_id,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()


class RackDepartureResultReader:
    def __init__(self, repository: RackDepartureRepository | None = None) -> None:
        self._repository = repository or RackDepartureRepository()

    async def latest(self, db: AsyncSession, picking_task_id: int, rack_id: str) -> RackDepartureSnapshot | None:
        confirmation = await self._repository.latest(db, picking_task_id, rack_id)
        if confirmation is None:
            return None
        request = parse_rack_departure_request(confirmation.request_payload)
        if request.operation_id != confirmation.operation_id or request.data.rack_id != rack_id:
            raise ValueError("departure request identity differs from confirmation")
        from wes_plugin_sdk import TransportRackPosition, wms_operations

        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=request.operation_id,
            task_id=request.data.task_id,
            rack_id=request.data.rack_id,
            current_location=TransportRackPosition(request.data.current_location.location_code),
            current_face=request.data.current_face,
        )
        outcome = None
        if confirmation.status == WmsConfirmationStatus.COMPLETED:
            evidence = await self._repository.evidence(db, confirmation.response_evidence_id)
            if (
                evidence is None
                or evidence.id != confirmation.response_evidence_id
                or evidence.kind != InboundEvidenceKind.WMS_RESULT
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.operation != RACK_DEPARTURE_OPERATION
                or evidence.operation_id != confirmation.operation_id
            ):
                raise ValueError("departure result evidence identity mismatch")
            payload = evidence.normalized_payload
            code = payload.get("code") if isinstance(payload, dict) else None
            if not isinstance(code, str):
                raise ValueError("departure result has no approved response code")
            status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
            if status is None:
                raise ValueError("departure result has no approved response status")
            response = parse_rack_departure_response(status, payload, request=request)
            if response.code == "DECIDED" and confirmation.response_result != response.data.result:
                raise ValueError("departure result differs from confirmation")
            outcome = decode_outcome(payload)
        return RackDepartureSnapshot(
            status=confirmation.status,
            intent=intent,
            outcome=outcome,
            evidence_id=confirmation.response_evidence_id,
            completed_at=confirmation.completed_at,
        )


__all__ = ["RackDepartureResultReader", "RackDepartureScheduler", "RackDepartureSnapshot"]
