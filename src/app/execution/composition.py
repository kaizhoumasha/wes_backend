"""Execution Decision processing 的显式部署组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.execution.repositories import (
    BinExecutionRepository,
    InboundEvidenceRepository,
    MaterialExecutionRepository,
    PositionProjectionRepository,
    RackReplacementTransportBindingRepository,
    WmsConfirmationRepository,
)
from src.app.execution.services import (
    BinExecutionService,
    DecisionApplier,
    FactProcessor,
    InboundEvidenceService,
    MaterialExecutionService,
    PositionProjectionService,
    WmsConfirmationRequestResolver,
    WmsConfirmationService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.services import DeviceCommandService
    from src.app.execution.plugin_binding import StaticPluginBinding
    from src.app.execution.services.wms_confirmation_service import (
        WmsBusinessWaitPlanner,
        WmsConfirmationAdapterPort,
    )
    from src.app.transport.service import TransportService
    from src.core.task_queue_gateway import TaskQueueGateway


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    bin_execution_service: BinExecutionService
    material_execution_service: MaterialExecutionService
    position_projection_service: PositionProjectionService
    inbound_evidence_service: InboundEvidenceService
    wms_confirmation_service: WmsConfirmationService
    fact_processor: FactProcessor


def build_execution_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    plugin_binding: StaticPluginBinding,
    wms_request_resolver: WmsConfirmationRequestResolver,
    device_command_service: DeviceCommandService,
    transport_service: TransportService,
    position_projection_service: PositionProjectionService,
    wms_confirmation_adapter: WmsConfirmationAdapterPort,
    wms_business_wait_planner: WmsBusinessWaitPlanner,
    task_queue_gateway: TaskQueueGateway,
) -> ExecutionRuntime:
    """只组合已显式注入的插件/WMS typed adapter，不发现或导入具体插件。"""

    material_repository = MaterialExecutionRepository()
    bin_execution_repository = BinExecutionRepository()
    position_projection_repository = PositionProjectionRepository()
    evidence_repository = InboundEvidenceRepository()
    confirmation_repository = WmsConfirmationRepository()
    rack_binding_repository = RackReplacementTransportBindingRepository()
    material_service = MaterialExecutionService(repository=material_repository)
    bin_execution_service = BinExecutionService(
        repository=bin_execution_repository,
        projection_repository=position_projection_repository,
    )
    evidence_service = InboundEvidenceService(repository=evidence_repository)
    confirmation_service = WmsConfirmationService(
        repository=confirmation_repository,
        session_factory=session_factory,
        adapter=wms_confirmation_adapter,
        evidence_service=evidence_service,
        business_wait_planner=wms_business_wait_planner,
        task_queue_gateway=task_queue_gateway,
    )
    applier = DecisionApplier(
        device_command_service=device_command_service,
        wms_confirmation_service=confirmation_service,
        wms_request_resolver=wms_request_resolver,
        rack_binding_repository=rack_binding_repository,
        transport_service=transport_service,
        material_execution_service=material_service,
    )
    return ExecutionRuntime(
        bin_execution_service=bin_execution_service,
        material_execution_service=material_service,
        position_projection_service=position_projection_service,
        inbound_evidence_service=evidence_service,
        wms_confirmation_service=confirmation_service,
        fact_processor=FactProcessor(
            session_factory=session_factory,
            plugin_binding=plugin_binding,
            decision_applier=applier,
            evidence_repository=evidence_repository,
            execution_repository=material_repository,
            material_execution_service=material_service,
        ),
    )


__all__ = ["ExecutionRuntime", "build_execution_runtime"]
