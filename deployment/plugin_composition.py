"""部署制品的显式业务插件清单与通用运行时组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rough_sorter.application.business_blocker import RoughSorterBusinessBlocker
from rough_sorter.application.factory import RoughSorterPluginFactFactory
from rough_sorter.application.persistence import RoughSorterInitialExecutionCorrelator
from rough_sorter.application.start_plan import RoughSorterStartPlanBuilder
from rough_sorter.application.transport import RoughSorterTransportOutcomePublisher
from rough_sorter.application.wms_follow_up import RoughSorterWmsFollowUpPlanner
from rough_sorter.application.wms_recovery import RecoveryEventEvidenceRecorder, RecoveryEventHandler
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION, build_handlers

from src.app.device.services import device_service
from src.app.execution.composition import ExecutionRuntime, build_execution_runtime
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.wms_adapter.inbound_adapter import WmsInboundAdapter
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.workline import LineType
from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher, InstalledPluginWmsFollowUpPlanner
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.app.workline.services.workline_start_service import WorkLineStartService
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.composition import DeviceEndpointAdapterProvider
    from src.app.device.services import DeviceCommandService
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationAdapterPort
    from src.app.transport.composition import TransportRuntime


@dataclass(frozen=True, slots=True)
class DeploymentRuntime:
    execution: ExecutionRuntime
    plugins: tuple[InstalledWorkLinePlugin, ...]
    workline_start_service: WorkLineStartService
    workline_configuration_service: WorkLineConfigurationService
    transport_outcome_publisher: InstalledPluginTransportOutcomePublisher
    wms_recovery_event_handler: RecoveryEventHandler


def build_deployment_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
    device_command_service: DeviceCommandService,
    device_adapter_provider: DeviceEndpointAdapterProvider | None = None,
) -> DeploymentRuntime:
    """Web/Celery 共用的部署期显式插件装配。"""

    factory = RoughSorterPluginFactFactory(transport_repository=transport_runtime.repository)
    rough_sorter_start_plan_builder = RoughSorterStartPlanBuilder(adapter_provider=device_adapter_provider)
    rough_sorter_transport_outcome_publisher = RoughSorterTransportOutcomePublisher(session_factory=session_factory)
    plugins = (
        InstalledWorkLinePlugin(
            display_name="粗分业务",
            runtime_binding=PluginRuntimeBinding(
                plugin_key=PLUGIN_KEY,
                plugin_version=PLUGIN_VERSION,
                handlers=build_handlers(),
                fact_factory=factory,
                initial_execution_correlator=RoughSorterInitialExecutionCorrelator(),
            ),
            start_plan_builder=rough_sorter_start_plan_builder,
            supported_line_types=(LineType.AUTO, LineType.MANUAL, LineType.HYBRID),
            business_blocker=RoughSorterBusinessBlocker(),
            compatibility_checker=rough_sorter_start_plan_builder.compatibility_incompatibility_reasons,
            configuration_checker=rough_sorter_start_plan_builder.configuration_incompatibility_reasons,
            wms_confirmation_follow_up_planner=RoughSorterWmsFollowUpPlanner(),
            transport_outcome_publisher=rough_sorter_transport_outcome_publisher,
        ),
    )
    plugin_binding = StaticPluginBinding(tuple(plugin.runtime_binding for plugin in plugins))
    execution = build_execution_runtime(
        session_factory=session_factory,
        plugin_binding=plugin_binding,
        device_command_service=device_command_service,
        transport_service=transport_runtime.service,
        position_projection_service=transport_runtime.position_projection_service,
        wms_confirmation_adapter=cast("WmsConfirmationAdapterPort", WmsInboundAdapter(transport_runtime.client)),
        wms_confirmation_follow_up_planner=InstalledPluginWmsFollowUpPlanner(plugins),
        task_queue_gateway=task_queue_gateway,
    )
    return DeploymentRuntime(
        execution=execution,
        plugins=plugins,
        workline_start_service=WorkLineStartService(plugins=plugins),
        workline_configuration_service=WorkLineConfigurationService(
            plugins=plugins,
            device_cache_invalidator=device_service,
        ),
        transport_outcome_publisher=InstalledPluginTransportOutcomePublisher(session_factory, plugins),
        wms_recovery_event_handler=RecoveryEventHandler(
            RecoveryEventEvidenceRecorder(
                session_factory,
                evidence_service=execution.inbound_evidence_service,
                task_queue_gateway=task_queue_gateway,
            )
        ),
    )


__all__ = [
    "DeploymentRuntime",
    "build_deployment_runtime",
]
