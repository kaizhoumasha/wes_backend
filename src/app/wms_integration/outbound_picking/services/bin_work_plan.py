"""Work 请求的固定 typed 创建/读取端口；业务准入与重问归插件。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wes_plugin_sdk import wms_operations

from src.app.execution import config
from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind, WmsConfirmationStatus
from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository
from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.work_plan_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.work_plan_wire import (
    BIN_WORK_PLAN_OPERATION,
    parse_bin_work_plan_request,
    parse_bin_work_plan_response,
)
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import BinWorkPlanIntent, BinWorkPlanOutcome


@dataclass(frozen=True, slots=True)
class BinWorkPlanSnapshot:
    status: WmsConfirmationStatus
    intent: BinWorkPlanIntent
    outcome: BinWorkPlanOutcome | None
    evidence_id: int | None
    completed_at: datetime | None


class BinWorkPlanScheduler:
    def __init__(
        self,
        confirmations: WmsConfirmationLifecycleService | None = None,
        *,
        tasks: PickingTaskRepository | None = None,
    ) -> None:
        self._confirmations = confirmations or WmsConfirmationLifecycleService()
        self._tasks = tasks or PickingTaskRepository()

    async def create_in_session(
        self, db: AsyncSession, intent: BinWorkPlanIntent, *, picking_task_id: int, created_at: datetime
    ) -> None:
        if type(picking_task_id) is not int or picking_task_id <= 0:
            raise ValueError("picking_task_id must be a positive integer")
        payload = encode_request(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        task = await self._tasks.get_by_id_for_update(db, picking_task_id)
        if task is None or task.task_id != intent.task_id:
            raise ValueError("work plan request does not match its PickingTask")
        result = await self._confirmations.create_or_get(
            db,
            operation=BIN_WORK_PLAN_OPERATION,
            operation_id=intent.operation_id,
            picking_task_id=picking_task_id,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()


class BinWorkPlanResultReader:
    def __init__(
        self,
        *,
        confirmations: WmsConfirmationRepository | None = None,
        evidences: InboundEvidenceRepository | None = None,
        tasks: PickingTaskRepository | None = None,
    ) -> None:
        self._confirmations = confirmations or WmsConfirmationRepository()
        self._evidences = evidences or InboundEvidenceRepository()
        self._tasks = tasks or PickingTaskRepository()

    async def by_operation_id(
        self, db: AsyncSession, *, picking_task_id: int, operation_id: str
    ) -> BinWorkPlanSnapshot | None:
        confirmation = await self._confirmations.get_by_identity(db, BIN_WORK_PLAN_OPERATION, operation_id)
        if confirmation is None:
            return None
        if (
            confirmation.operation != BIN_WORK_PLAN_OPERATION
            or confirmation.operation_id != operation_id
            or confirmation.picking_task_id != picking_task_id
            or confirmation.material_execution_id is not None
            or confirmation.workline_id is not None
            or canonical_json_digest(confirmation.request_payload) != confirmation.request_digest
        ):
            raise ValueError("work plan frozen owner or request identity mismatch")
        request = parse_bin_work_plan_request(confirmation.request_payload)
        task = await self._tasks.get_by_id(db, picking_task_id)
        if request.operation_id != operation_id or task is None or task.task_id != request.data.task_id:
            raise ValueError("work plan request differs from its PickingTask")
        intent = wms_operations.outbound_bin_work_plan(
            operation_id=operation_id,
            task_id=request.data.task_id,
            bin_code=request.data.bin_code,
            scanned_at=request.data.scanned_at,
        )
        outcome = None
        if confirmation.status == WmsConfirmationStatus.COMPLETED:
            evidence = await self._evidences.get_by_id(db, confirmation.response_evidence_id)
            if (
                evidence is None
                or evidence.id != confirmation.response_evidence_id
                or evidence.kind != InboundEvidenceKind.WMS_RESULT
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.operation != BIN_WORK_PLAN_OPERATION
                or evidence.operation_id != operation_id
                or evidence.picking_task_id != picking_task_id
                or evidence.material_execution_id is not None
            ):
                raise ValueError("work plan result evidence identity mismatch")
            response = parse_bin_work_plan_response(200, evidence.normalized_payload, request=request)
            if response.code != "DECIDED" or response.data.result != confirmation.response_result:
                raise ValueError("work plan result differs from confirmation")
            outcome = decode_outcome(evidence.normalized_payload)
        return BinWorkPlanSnapshot(
            status=WmsConfirmationStatus(confirmation.status),
            intent=intent,
            outcome=outcome,
            evidence_id=confirmation.response_evidence_id,
            completed_at=confirmation.completed_at,
        )
