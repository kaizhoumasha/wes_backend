"""单盘决定的固定 typed 持久端口；业务重问与当前游标归插件。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from wes_plugin_sdk import PickingMaterialIntent, PickingMaterialOutcome

from src.app.execution import config
from src.app.execution.models import WmsConfirmationStatus
from src.app.execution.repositories import inbound_evidence_repository, material_execution_repository
from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.material_decide_typed import decode_outcome, decode_request, encode_request
from src.app.wms_adapter.outbound_picking.material_decide_wire import (
    MATERIAL_DECIDE_OPERATION,
    parse_material_decide_request,
    parse_material_decide_response,
)
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone


@dataclass(frozen=True, slots=True)
class MaterialDecideSnapshot:
    confirmation_id: int
    status: WmsConfirmationStatus
    intent: PickingMaterialIntent
    outcome: PickingMaterialOutcome | None
    evidence_id: int | None
    completed_at: datetime | None
    published_at: datetime | None


class MaterialDecideScheduler:
    def __init__(self, confirmations=None):
        self._confirmations = confirmations or WmsConfirmationLifecycleService()

    async def create_in_session(self, db, intent, *, material_execution_id, created_at):
        if type(intent) is not PickingMaterialIntent:
            raise TypeError("material decide requires PickingMaterialIntent")
        if type(material_execution_id) is not int or material_execution_id <= 0:
            raise ValueError("material_execution_id must be positive integer")
        payload = encode_request(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        result = await self._confirmations.create_or_get(
            db,
            operation=MATERIAL_DECIDE_OPERATION,
            operation_id=intent.operation_id,
            material_execution_id=material_execution_id,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()
        return result.confirmation.id


class MaterialDecideResultReader:
    def __init__(self, confirmations=None):
        self._confirmations = confirmations or WmsConfirmationRepository()

    async def by_id(self, db, *, material_execution_id, confirmation_id):
        row = await self._confirmations.get_by_id(db, confirmation_id)
        return await self._snapshot(db, row, material_execution_id)

    async def by_operation_id(self, db, *, material_execution_id, operation_id):
        row = await self._confirmations.get_by_identity(db, MATERIAL_DECIDE_OPERATION, operation_id)
        return await self._snapshot(db, row, material_execution_id)

    async def _snapshot(self, db, row, material_execution_id):
        if row is None:
            return None
        execution = await material_execution_repository.get_by_id(db, material_execution_id)
        if (
            row.operation != MATERIAL_DECIDE_OPERATION
            or row.material_execution_id != material_execution_id
            or row.picking_task_id is not None
            or row.workline_id is not None
            or execution is None
            or canonical_json_digest(row.request_payload) != row.request_digest
        ):
            raise ValueError("material request owner or digest differs")
        intent = decode_request(row.request_payload)
        if intent.operation_id != row.operation_id:
            raise ValueError("material request operation identity differs")
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
                or evidence.operation != MATERIAL_DECIDE_OPERATION
                or evidence.operation_id != row.operation_id
                or evidence.normalized_payload.get("operation_id") != row.operation_id
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
                or row.completed_at is None
            ):
                raise ValueError("material result Evidence identity differs")
            response = parse_material_decide_response(
                200, evidence.normalized_payload, request=parse_material_decide_request(row.request_payload)
            )
            if response.code != "DECIDED" or response.data.result != row.response_result:
                raise ValueError("material result differs from Confirmation")
            outcome, published_at = decode_outcome(evidence.normalized_payload), evidence.published_at
        return MaterialDecideSnapshot(
            row.id,
            WmsConfirmationStatus(row.status),
            intent,
            outcome,
            row.response_evidence_id,
            row.completed_at,
            published_at,
        )
