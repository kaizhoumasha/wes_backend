"""PickingTask 完成确认的可靠创建和严格结果读取；本地完成时机由插件决定。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.execution import config
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind, WmsConfirmation
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.completion_confirm_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.completion_confirm_wire import (
    COMPLETION_CONFIRM_OPERATION,
    parse_completion_confirm_request,
    parse_completion_confirm_response,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import CompletionConfirmIntent, CompletionConfirmOutcome


@dataclass(frozen=True, slots=True)
class CompletionConfirmationSnapshot:
    status: WmsConfirmationStatus
    task_id: str
    plan_revision: int
    outcome: CompletionConfirmOutcome | None
    completed_at: datetime | None


class PickingTaskCompletionScheduler:
    def __init__(self, confirmations: WmsConfirmationLifecycleService | None = None) -> None:
        self._confirmations = confirmations or WmsConfirmationLifecycleService()

    async def create_in_session(
        self, db: AsyncSession, intent: CompletionConfirmIntent, *, picking_task_id: int, created_at: datetime
    ) -> None:
        payload = encode_request(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        result = await self._confirmations.create_or_get(
            db,
            operation=COMPLETION_CONFIRM_OPERATION,
            operation_id=intent.operation_id,
            picking_task_id=picking_task_id,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()
        if result.duplicate:
            raise ValueError("new completion confirmation identity already exists")


class PickingTaskCompletionResultReader:
    async def latest(self, db: AsyncSession, picking_task_id: int) -> CompletionConfirmationSnapshot | None:
        confirmations = cast("Any", WmsConfirmation).__table__.c
        confirmation = await db.scalar(
            select(WmsConfirmation)
            .where(
                confirmations.picking_task_id == picking_task_id,
                confirmations.operation == COMPLETION_CONFIRM_OPERATION,
            )
            .order_by(confirmations.id.desc())
            .limit(1)
        )
        if confirmation is None:
            return None
        request = parse_completion_confirm_request(confirmation.request_payload)
        if request.operation_id != confirmation.operation_id:
            raise ValueError("completion request identity differs from confirmation")
        outcome = None
        if confirmation.status == WmsConfirmationStatus.COMPLETED:
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            if (
                evidence is None
                or evidence.id != confirmation.response_evidence_id
                or evidence.kind != InboundEvidenceKind.WMS_RESULT
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.operation != COMPLETION_CONFIRM_OPERATION
                or evidence.operation_id != confirmation.operation_id
            ):
                raise ValueError("completion result evidence identity mismatch")
            payload = evidence.normalized_payload
            code = payload.get("code") if isinstance(payload, dict) else None
            status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
            if status is None:
                raise ValueError("completion result has no approved response status")
            response = parse_completion_confirm_response(status, payload, request=request)
            if response.code == "DECIDED" and confirmation.response_result != response.data.result:
                raise ValueError("completion result differs from confirmation")
            outcome = decode_outcome(payload)
        return CompletionConfirmationSnapshot(
            status=confirmation.status,
            task_id=request.data.task_id,
            plan_revision=request.data.last_applied_plan_revision,
            outcome=outcome,
            completed_at=confirmation.completed_at,
        )


__all__ = ["CompletionConfirmationSnapshot", "PickingTaskCompletionResultReader", "PickingTaskCompletionScheduler"]
