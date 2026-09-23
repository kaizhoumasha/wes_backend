"""退料货架到位报告的可靠 WMS 事实提交及原结果读取。"""

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
from src.app.wms_adapter.outbound_picking.arrival_report_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.arrival_report_wire import (
    RETURN_RACK_ARRIVAL_REPORT_OPERATION,
    parse_return_rack_arrival_report_request,
    parse_return_rack_arrival_report_response,
)
from src.app.wms_integration.outbound_picking.repositories.return_rack_arrival_repository import (
    ReturnRackArrivalRepository,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import ReturnRackArrivalReportIntent, ReturnRackArrivalReportOutcome


@dataclass(frozen=True, slots=True)
class ReturnRackArrivalSnapshot:
    status: WmsConfirmationStatus
    intent: ReturnRackArrivalReportIntent
    outcome: ReturnRackArrivalReportOutcome | None
    evidence_id: int | None
    completed_at: datetime | None


class ReturnRackArrivalScheduler:
    def __init__(self, confirmations: WmsConfirmationLifecycleService) -> None:
        self._confirmations = confirmations

    async def create_in_session(
        self,
        db: AsyncSession,
        intent: ReturnRackArrivalReportIntent,
        *,
        picking_task_id: int,
        created_at: datetime,
    ) -> None:
        payload = encode_request(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        result = await self._confirmations.create_or_get(
            db,
            operation=RETURN_RACK_ARRIVAL_REPORT_OPERATION,
            operation_id=intent.operation_id,
            picking_task_id=picking_task_id,
            workline_id=None,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()


class ReturnRackArrivalResultReader:
    def __init__(self, repository: ReturnRackArrivalRepository | None = None) -> None:
        self._repository = repository or ReturnRackArrivalRepository()

    async def latest(
        self, db: AsyncSession, picking_task_id: int, rack_id: str, transport_task_id: str
    ) -> ReturnRackArrivalSnapshot | None:
        confirmation = await self._repository.latest(db, picking_task_id, rack_id, transport_task_id)
        if confirmation is None:
            return None
        request = parse_return_rack_arrival_report_request(confirmation.request_payload)
        if request.operation_id != confirmation.operation_id or request.data.rack_id != rack_id:
            raise ValueError("arrival request identity differs from confirmation")
        from wes_plugin_sdk import TransportRackPosition, wms_operations

        intent = wms_operations.outbound_return_rack_arrival_report(
            operation_id=request.operation_id,
            task_id=request.data.task_id,
            transport_task_id=request.data.transport_task_id,
            outcome_revision=request.data.outcome_revision,
            rack_id=request.data.rack_id,
            final_position=TransportRackPosition(request.data.final_position.location_code),
            arrival_face=request.data.arrival_face,
        )
        outcome = None
        if confirmation.status == WmsConfirmationStatus.COMPLETED:
            evidence = await self._repository.evidence(db, confirmation.response_evidence_id)
            if (
                evidence is None
                or evidence.id != confirmation.response_evidence_id
                or evidence.kind != InboundEvidenceKind.WMS_RESULT
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.operation != RETURN_RACK_ARRIVAL_REPORT_OPERATION
                or evidence.operation_id != confirmation.operation_id
            ):
                raise ValueError("arrival result evidence identity mismatch")
            payload = evidence.normalized_payload
            code = payload.get("code") if isinstance(payload, dict) else None
            if not isinstance(code, str):
                raise ValueError("arrival result has no approved response code")
            status = {"RECORDED": 200, "DUPLICATE": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
            if status is None:
                raise ValueError("arrival result has no approved response status")
            response = parse_return_rack_arrival_report_response(status, payload)
            if response.operation_id != confirmation.operation_id:
                raise ValueError("arrival result differs from confirmation")
            if response.code in {"RECORDED", "DUPLICATE"} and confirmation.response_result != response.code:
                raise ValueError("arrival result differs from confirmation")
            outcome = decode_outcome(payload)
        return ReturnRackArrivalSnapshot(
            status=confirmation.status,
            intent=intent,
            outcome=outcome,
            evidence_id=confirmation.response_evidence_id,
            completed_at=confirmation.completed_at,
        )


__all__ = ["ReturnRackArrivalResultReader", "ReturnRackArrivalScheduler", "ReturnRackArrivalSnapshot"]
