"""人工出库联调台的显式组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareRuntimeFacts, PrepareTaskType

from src.app.wms_integration.outbound_picking.services import PickingTaskPrepareCoordinator
from src.app.workline_integration_debug.service import IntegrationDebugService, IntegrationRunWorkLineOwner
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.services import DeviceCommandService
    from src.app.execution.services import WmsConfirmationService
    from src.app.transport.service import TransportService


class WorkLineOwnerPort(Protocol):
    async def validate_owner(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        request_payload: dict[str, Any],
    ) -> bool: ...


class CombinedWorkLineConfirmationOwner:
    """保持既有 WorkLine owner，并追加联调 run 的精确 operation identity。"""

    def __init__(self, existing: WorkLineOwnerPort, debug: WorkLineOwnerPort | None = None) -> None:
        self._existing = existing
        self._debug = debug or IntegrationRunWorkLineOwner()

    async def validate_owner(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        request_payload: dict[str, Any],
    ) -> bool:
        if await self._existing.validate_owner(db, workline_id=workline_id, request_payload=request_payload):
            return True
        return await self._debug.validate_owner(db, workline_id=workline_id, request_payload=request_payload)


class ManualIntegrationPreparePolicy:
    """临时联调固定选择 MANUAL；现场准入仍由 WorkLine 空闲与事故事实约束。"""

    def select_task_type(self, context: PrepareContext) -> PrepareTaskType | None:
        return PrepareTaskType.MANUAL if context.is_active and context.line_type == "MANUAL" else None

    def is_ready(self, facts: PrepareRuntimeFacts, *, now: datetime) -> bool:
        del now
        return not facts.has_active_incident


@dataclass(frozen=True, slots=True)
class IntegrationDebugRuntime:
    service: IntegrationDebugService


def build_integration_debug_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    confirmations: WmsConfirmationService,
    transport: TransportService,
    device_commands: DeviceCommandService,
) -> IntegrationDebugRuntime:
    return IntegrationDebugRuntime(
        service=IntegrationDebugService(
            session_factory,
            confirmations=confirmations,
            transport=transport,
            device_commands=device_commands,
            prepare=PickingTaskPrepareCoordinator(
                session_factory,
                policy=ManualIntegrationPreparePolicy(),
                task_queue_gateway=task_queue_gateway,
            ),
        )
    )


__all__ = [
    "CombinedWorkLineConfirmationOwner",
    "IntegrationDebugRuntime",
    "ManualIntegrationPreparePolicy",
    "build_integration_debug_runtime",
]
