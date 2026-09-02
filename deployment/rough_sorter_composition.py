"""粗分机插件唯一静态部署组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rough_sorter.application.factory import RoughSorterPluginFactFactory
from rough_sorter.application.persistence import RoughSorterInitialExecutionCorrelator
from rough_sorter.application.start_plan import RoughSorterStartPlanBuilder
from rough_sorter.application.transport import RoughSorterTransportOutcomePublisher
from rough_sorter.application.wms_follow_up import RoughSorterWmsFollowUpPlanner
from rough_sorter.application.wms_recovery import RecoveryEventEvidenceRecorder, RecoveryEventHandler
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION, build_handlers

from src.app.execution.composition import ExecutionRuntime, build_execution_runtime
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.wms_adapter.inbound_adapter import WmsInboundAdapter
from src.app.workline.services.workline_start_service import WorkLineStartService
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.services import DeviceCommandService
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationAdapterPort
    from src.app.transport.composition import TransportRuntime


@dataclass(frozen=True, slots=True)
class RoughSorterDeploymentRuntime:
    execution: ExecutionRuntime
    transport_outcome_publisher: RoughSorterTransportOutcomePublisher
    wms_recovery_event_handler: RecoveryEventHandler


def build_rough_sorter_start_service() -> WorkLineStartService:
    """API 进程唯一的粗分机 START 业务组合。"""

    return WorkLineStartService(plan_builder=RoughSorterStartPlanBuilder())


def build_rough_sorter_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
    device_command_service: DeviceCommandService,
) -> RoughSorterDeploymentRuntime:
    """Web/Celery 共用的唯一静态 rough sorter 运行时装配。"""

    factory = RoughSorterPluginFactFactory(transport_repository=transport_runtime.repository)
    plugin_binding = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key=PLUGIN_KEY,
                plugin_version=PLUGIN_VERSION,
                handlers=build_handlers(),
                fact_factory=factory,
                initial_execution_correlator=RoughSorterInitialExecutionCorrelator(),
            ),
        )
    )
    execution = build_execution_runtime(
        session_factory=session_factory,
        plugin_binding=plugin_binding,
        device_command_service=device_command_service,
        transport_service=transport_runtime.service,
        position_projection_service=transport_runtime.position_projection_service,
        wms_confirmation_adapter=cast("WmsConfirmationAdapterPort", WmsInboundAdapter(transport_runtime.client)),
        wms_confirmation_follow_up_planner=RoughSorterWmsFollowUpPlanner(),
        task_queue_gateway=task_queue_gateway,
    )
    return RoughSorterDeploymentRuntime(
        execution=execution,
        transport_outcome_publisher=RoughSorterTransportOutcomePublisher(
            session_factory=session_factory,
            evidence_service=execution.inbound_evidence_service,
        ),
        wms_recovery_event_handler=RecoveryEventHandler(
            RecoveryEventEvidenceRecorder(
                session_factory,
                evidence_service=execution.inbound_evidence_service,
                task_queue_gateway=task_queue_gateway,
            )
        ),
    )


__all__ = [
    "RoughSorterDeploymentRuntime",
    "build_rough_sorter_runtime",
    "build_rough_sorter_start_service",
]
