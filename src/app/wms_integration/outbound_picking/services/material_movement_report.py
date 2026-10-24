"""当前单盘报告的固定 typed 读口；不决定工作线的业务关闭。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.execution.models import WmsConfirmationStatus
from src.app.execution.repositories import inbound_evidence_repository, material_execution_repository
from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
from src.app.wms_adapter.outbound_picking.movement_report_typed import decode_outcome, decode_request
from src.app.wms_adapter.outbound_picking.movement_report_wire import (
    MATERIAL_MOVEMENT_REPORT_OPERATION,
    parse_material_movement_report_response,
)
from src.utils.canonical_json import canonical_json_digest

if TYPE_CHECKING:
    from datetime import datetime

    from wes_plugin_sdk import MaterialMovementReportIntent, MaterialMovementReportOutcome


@dataclass(frozen=True, slots=True)
class MaterialMovementReportSnapshot:
    confirmation_id: int
    status: WmsConfirmationStatus
    intent: MaterialMovementReportIntent
    outcome: MaterialMovementReportOutcome | None
    evidence_id: int | None
    completed_at: datetime | None
    published_at: datetime | None


class MaterialMovementReportResultReader:
    def __init__(self, confirmations=None):
        self._confirmations = confirmations or WmsConfirmationRepository()

    async def by_id(self, db, *, material_execution_id, confirmation_id):
        row = await self._confirmations.get_by_id(db, confirmation_id)
        if row is None:
            return None
        execution = await material_execution_repository.get_by_id(db, material_execution_id)
        if (
            row.operation != MATERIAL_MOVEMENT_REPORT_OPERATION
            or row.material_execution_id != material_execution_id
            or row.picking_task_id is not None
            or row.workline_id is not None
            or execution is None
            or canonical_json_digest(row.request_payload) != row.request_digest
        ):
            raise ValueError("movement report owner or digest differs")
        intent = decode_request(row.request_payload)
        if intent.operation_id != row.operation_id:
            raise ValueError("movement report operation identity differs")
        outcome, published_at = None, None
        if row.status == WmsConfirmationStatus.COMPLETED:
            evidence = await inbound_evidence_repository.get_by_id(db, row.response_evidence_id)
            if (
                evidence is None
                or evidence.kind != "WMS_RESULT"
                or evidence.apply_status != "APPLIED"
                or evidence.material_execution_id != material_execution_id
                or evidence.workline_id != execution.workline_id
                or evidence.picking_task_id is not None
                or evidence.operation != MATERIAL_MOVEMENT_REPORT_OPERATION
                or evidence.operation_id != row.operation_id
                or evidence.normalized_payload.get("operation_id") != row.operation_id
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
                or row.completed_at is None
            ):
                raise ValueError("movement report result Evidence identity differs")
            response = parse_material_movement_report_response(200, evidence.normalized_payload)
            if response.code != row.response_result:
                raise ValueError("movement report result differs from Confirmation")
            outcome, published_at = decode_outcome(evidence.normalized_payload), evidence.published_at
        return MaterialMovementReportSnapshot(
            row.id,
            WmsConfirmationStatus(row.status),
            intent,
            outcome,
            row.response_evidence_id,
            row.completed_at,
            published_at,
        )
