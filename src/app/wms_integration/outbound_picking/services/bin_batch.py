"""入站与退箱 typed intent 创建已有可靠 WMS 义务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from wes_plugin_sdk import BinInboundBatchIntent, BinReturnBatchIntent, BinReturnCandidate, wms_operations

from src.app.execution import config
from src.app.execution.models import InboundEvidence, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import (
    decode_outcome as decode_inbound,
)
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request as encode_inbound
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import (
    BIN_INBOUND_BATCH_OPERATION,
    parse_bin_inbound_batch_request,
    parse_bin_inbound_batch_response,
)
from src.app.wms_adapter.outbound_picking.return_batch_typed import decode_outcome as decode_return
from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request as encode_return
from src.app.wms_adapter.outbound_picking.return_batch_wire import (
    BIN_RETURN_BATCH_OPERATION,
    parse_bin_return_batch_request,
    parse_bin_return_batch_response,
)
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class BinBatchScheduler:
    def __init__(self, confirmations: WmsConfirmationLifecycleService) -> None:
        self._confirmations = confirmations

    async def create_in_session(
        self,
        db: AsyncSession,
        intent: BinInboundBatchIntent | BinReturnBatchIntent,
        *,
        workline_id: int,
        created_at: datetime,
    ) -> None:
        timestamp = int(timezone.to_utc(created_at).timestamp() * 1000)
        if type(intent) is BinInboundBatchIntent:
            operation = BIN_INBOUND_BATCH_OPERATION
            payload = encode_inbound(intent, timestamp=timestamp)
        elif type(intent) is BinReturnBatchIntent:
            operation = BIN_RETURN_BATCH_OPERATION
            payload = encode_return(intent, timestamp=timestamp)
        else:
            raise TypeError("bin batch requires a fixed typed intent")
        result = await self._confirmations.create_or_get(
            db,
            operation=operation,
            operation_id=intent.operation_id,
            workline_id=workline_id,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()


class BinInboundBatchOwnerService:
    def __init__(
        self, *, tasks: PickingTaskRepository | None = None, plans: PickingTaskPlanDeltaRepository | None = None
    ):
        self._tasks = tasks or PickingTaskRepository()
        self._plans = plans or PickingTaskPlanDeltaRepository()

    async def validate_owner(self, db: AsyncSession, *, workline_id: int, request_payload: dict[str, object]) -> bool:
        try:
            request = parse_bin_inbound_batch_request(request_payload)
        except (ValueError, TypeError):
            return False
        task = await self._tasks.get_by_task_id_for_update(db, request.data.task_id)
        if task is None or task.id is None or task.workline_id != workline_id:
            return False
        return any(
            row.plan_revision == request.data.plan_revision
            and row.rack_id == request.data.rack_id
            and row.rack_face == request.data.rack_face
            for row in await self._plans.list_bin_source_racks(db, task.id)
        )


class BinBatchResultReader:
    """把原可靠义务和匹配响应转为插件可消费的 typed 值。"""

    def __init__(self, confirmations: WmsConfirmationRepository | None = None) -> None:
        self._confirmations = confirmations or WmsConfirmationRepository()

    async def _matched_confirmation(self, db: AsyncSession, evidence: object, *, workline_id: int, operation: str):
        if getattr(evidence, "operation", None) != operation:
            raise ValueError("batch evidence operation differs from original confirmation")
        operation_id = getattr(evidence, "operation_id", None)
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("batch evidence does not match original confirmation")
        confirmation = await self._confirmations.get_by_identity_for_update(db, operation, operation_id)
        if (
            confirmation is None
            or confirmation.status != WmsConfirmationStatus.COMPLETED
            or confirmation.workline_id != workline_id
            or confirmation.response_evidence_id != getattr(evidence, "id", None)
            or confirmation.operation_id != operation_id
        ):
            raise ValueError("batch evidence does not match original confirmation")
        return confirmation

    @staticmethod
    def _response_status(payload: object) -> int:
        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, str):
            raise TypeError("batch response code must be text")
        status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
        if status is None:
            raise ValueError("batch response has no approved status")
        return status

    async def read_inbound(self, db: AsyncSession, evidence: object, *, workline_id: int):
        confirmation = await self._matched_confirmation(
            db, evidence, workline_id=workline_id, operation=BIN_INBOUND_BATCH_OPERATION
        )
        request = parse_bin_inbound_batch_request(confirmation.request_payload)
        payload = getattr(evidence, "normalized_payload", None)
        _ = parse_bin_inbound_batch_response(self._response_status(payload), payload, request=request)
        intent = wms_operations.outbound_bin_inbound_batch(
            operation_id=request.operation_id,
            task_id=request.data.task_id,
            plan_revision=request.data.plan_revision,
            rack_id=request.data.rack_id,
            rack_face=request.data.rack_face,
        )
        return intent, decode_inbound(payload)

    async def read_return(self, db: AsyncSession, evidence: object, *, workline_id: int):
        confirmation = await self._matched_confirmation(
            db, evidence, workline_id=workline_id, operation=BIN_RETURN_BATCH_OPERATION
        )
        request = parse_bin_return_batch_request(confirmation.request_payload)
        payload = getattr(evidence, "normalized_payload", None)
        _ = parse_bin_return_batch_response(self._response_status(payload), payload, request=request)
        intent = wms_operations.outbound_bin_return_batch(
            operation_id=request.operation_id,
            workline_code=request.data.workline_code,
            rack_id=request.data.rack_id,
            rack_face=request.data.rack_face,
            return_candidates=tuple(
                BinReturnCandidate(candidate.sequence_no, candidate.bin_code, candidate.source.location_code)
                for candidate in request.data.return_candidates
            ),
        )
        return intent, decode_return(payload)

    async def _latest_for_face(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        operation: str,
        rack_id: str,
        rack_face: str,
        task_id: str | None = None,
        plan_revision: int | None = None,
    ) -> tuple[InboundEvidence, datetime] | None:
        confirmations = cast("Any", WmsConfirmation).__table__.c
        evidences = cast("Any", InboundEvidence).__table__.c
        statement = (
            select(InboundEvidence, confirmations.completed_at)
            .join(WmsConfirmation, confirmations.response_evidence_id == evidences.id)
            .where(
                confirmations.workline_id == workline_id,
                confirmations.operation == operation,
                confirmations.status == WmsConfirmationStatus.COMPLETED,
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.request_payload["data"]["rack_face"].as_string() == rack_face,
                evidences.published_at.is_not(None),
            )
            .order_by(confirmations.completed_at.desc(), confirmations.id.desc())
            .limit(2 if operation == BIN_INBOUND_BATCH_OPERATION else 1)
        )
        if task_id is not None:
            statement = statement.where(confirmations.request_payload["data"]["task_id"].as_string() == task_id)
        if plan_revision is not None:
            statement = statement.where(
                confirmations.request_payload["data"]["plan_revision"].as_integer() == plan_revision
            )
        rows = (await db.execute(statement)).all()
        if not rows:
            return None
        if operation == BIN_INBOUND_BATCH_OPERATION and len(rows) > 1:
            raise ValueError("multiple inbound allocations for one rack face require reconciliation")
        evidence, completed_at = rows[0]
        if completed_at is None:
            raise ValueError("completed batch confirmation lacks completion time")
        return evidence, completed_at

    async def latest_return(self, db: AsyncSession, *, workline_id: int, rack_id: str, rack_face: str):
        latest = await self._latest_for_face(
            db, workline_id=workline_id, operation=BIN_RETURN_BATCH_OPERATION, rack_id=rack_id, rack_face=rack_face
        )
        if latest is None:
            return None
        evidence, completed_at = latest
        _, outcome = await self.read_return(db, evidence, workline_id=workline_id)
        return outcome, completed_at

    async def has_unclosed_return(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        rack_id: str,
        rack_face: str,
    ) -> bool:
        confirmations = cast("Any", WmsConfirmation).__table__.c
        confirmation_id = await db.scalar(
            select(confirmations.id)
            .where(
                confirmations.workline_id == workline_id,
                confirmations.operation == BIN_RETURN_BATCH_OPERATION,
                confirmations.status.in_(
                    (
                        WmsConfirmationStatus.PENDING,
                        WmsConfirmationStatus.DISPATCHING,
                        WmsConfirmationStatus.RECONCILING,
                    )
                ),
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.request_payload["data"]["rack_face"].as_string() == rack_face,
            )
            .limit(1)
        )
        return confirmation_id is not None

    async def latest_inbound(
        self, db: AsyncSession, *, workline_id: int, task_id: str, plan_revision: int, rack_id: str, rack_face: str
    ):
        detail = await self.latest_inbound_detail(
            db,
            workline_id=workline_id,
            task_id=task_id,
            plan_revision=plan_revision,
            rack_id=rack_id,
            rack_face=rack_face,
        )
        if detail is None:
            return None
        _, outcome, _, completed_at = detail
        return outcome, completed_at

    async def latest_inbound_detail(
        self, db: AsyncSession, *, workline_id: int, task_id: str, plan_revision: int, rack_id: str, rack_face: str
    ):
        latest = await self._latest_for_face(
            db,
            workline_id=workline_id,
            operation=BIN_INBOUND_BATCH_OPERATION,
            task_id=task_id,
            plan_revision=plan_revision,
            rack_id=rack_id,
            rack_face=rack_face,
        )
        if latest is None:
            return None
        evidence, completed_at = latest
        intent, outcome = await self.read_inbound(db, evidence, workline_id=workline_id)
        return intent, outcome, evidence, completed_at


__all__ = ["BinBatchResultReader", "BinBatchScheduler", "BinInboundBatchOwnerService"]
