"""人工工作位准入的 typed intent 到既有可靠 WMS 义务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution import config
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.manual_bin_admission_wire import (
    MANUAL_BIN_ADMISSION_OPERATION,
    parse_manual_bin_admission_request,
)
from src.app.wms_adapter.outbound_picking.manual_bin_typed import encode_admission
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import ManualBinAdmissionIntent


class ManualBinAdmissionScheduler:
    def __init__(self, confirmations: WmsConfirmationLifecycleService) -> None:
        self._confirmations = confirmations

    async def create_in_session(
        self,
        db: AsyncSession,
        intent: ManualBinAdmissionIntent,
        *,
        workline_id: int,
        created_at: datetime,
        not_before: datetime | None = None,
    ) -> None:
        payload = encode_admission(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        due = not_before or created_at
        result = await self._confirmations.create_or_get(
            db,
            operation=MANUAL_BIN_ADMISSION_OPERATION,
            operation_id=intent.operation_id,
            workline_id=workline_id,
            request_payload=payload,
            deadline_at=due + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()
        if not result.duplicate:  # type: ignore[attr-defined]
            result.confirmation.next_attempt_at = due  # type: ignore[attr-defined]


class ManualBinAdmissionOwnerService:
    def __init__(self, tasks: PickingTaskRepository | None = None) -> None:
        self._tasks = tasks or PickingTaskRepository()

    async def validate_owner(self, db: AsyncSession, *, workline_id: int, request_payload: dict[str, object]) -> bool:
        try:
            request = parse_manual_bin_admission_request(request_payload)
        except (ValueError, TypeError):
            return False
        task = await self._tasks.get_by_task_id_for_update(db, request.data.task_id)
        return bool(
            task is not None
            and task.workline_id == workline_id
            and task.status == PickingTaskStatus.EXECUTING
            and task.task_type == PickingTaskType.MANUAL
        )


__all__ = ["ManualBinAdmissionOwnerService", "ManualBinAdmissionScheduler"]
