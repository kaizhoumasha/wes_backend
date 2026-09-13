"""部署制品的显式业务插件清单与通用运行时组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from deployment.plugin_definitions import load_plugin_definitions
from src.app.device.services import device_service
from src.app.execution.composition import ExecutionRuntime, build_execution_runtime
from src.app.execution.plugin_binding import StaticPluginBinding
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.transport.debug_run_service import TransportDebugReturnBatchOwner
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter

# Web/worker 组合根注册共享外键目标；不依赖具体插件是否安装或启用。
from src.app.wms_integration.outbound_picking.models import PickingTask  # noqa: F401
from src.app.wms_integration.outbound_picking.services.picking_task_confirmation_owner import (
    PickingTaskConfirmationOwnerService,
)
from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
    PickingTaskPlanActivationService,
)
from src.app.wms_integration.outbound_picking.services.picking_task_prepare_batch import (
    PickingTaskPrepareBatchService,
)
from src.app.wms_integration.outbound_picking.services.return_batch_owner import ReturnBatchOwnerService
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher, InstalledPluginWmsFollowUpPlanner
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.app.workline.services.workline_start_service import WorkLineStartService
from src.app.workline_integration_debug.composition import CombinedWorkLineConfirmationOwner
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
    wms_recovery_event_handler: object | None
    picking_task_prepare_service: PickingTaskPrepareBatchService
    picking_task_plan_activation_service: PickingTaskPlanActivationService


def build_deployment_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
    device_command_service: DeviceCommandService,
    device_adapter_provider: DeviceEndpointAdapterProvider | None = None,
    enabled_plugin_keys: tuple[str, ...] = (),
) -> DeploymentRuntime:
    """Web/Celery 共用的部署期显式插件装配。"""

    definitions = load_plugin_definitions(enabled_plugin_keys)
    by_key = {definition.plugin_key: definition for definition in definitions}

    plugins: tuple[InstalledWorkLinePlugin, ...] = ()
    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.plugin import build_handlers, build_prepare_policy, build_transport_outcome_publisher

        (plan_handler,) = build_handlers()

        plugins += (
            InstalledWorkLinePlugin(
                definition=by_key["manual-picking"],
                picking_task_prepare_policy=build_prepare_policy(),
                picking_task_plan_applied_handler=plan_handler,
                transport_outcome_publisher=build_transport_outcome_publisher(),
            ),
        )
    plugin_binding = StaticPluginBinding(
        tuple(plugin.runtime_binding for plugin in plugins if plugin.runtime_binding is not None),
        definitions=definitions,
    )
    execution = build_execution_runtime(
        session_factory=session_factory,
        plugin_binding=plugin_binding,
        device_command_service=device_command_service,
        transport_service=transport_runtime.service,
        position_projection_service=transport_runtime.position_projection_service,
        wms_confirmation_adapter=cast("WmsConfirmationAdapterPort", WmsConfirmationAdapter(transport_runtime.client)),
        wms_confirmation_follow_up_planner=InstalledPluginWmsFollowUpPlanner(plugins),
        task_queue_gateway=task_queue_gateway,
        picking_task_owner=PickingTaskConfirmationOwnerService(),
        workline_owner=CombinedWorkLineConfirmationOwner(
            CombinedWorkLineConfirmationOwner(ReturnBatchOwnerService(), TransportDebugReturnBatchOwner())
        ),
    )
    return DeploymentRuntime(
        execution=execution,
        plugins=plugins,
        workline_start_service=WorkLineStartService(
            plugins=plugins,
            device_adapter_provider=device_adapter_provider,
            task_queue_gateway=task_queue_gateway,
        ),
        workline_configuration_service=WorkLineConfigurationService(
            definitions=definitions,
            business_blockers={
                plugin.plugin_key: plugin.business_blocker for plugin in plugins if plugin.business_blocker is not None
            },
            device_cache_invalidator=device_service,
        ),
        transport_outcome_publisher=InstalledPluginTransportOutcomePublisher(session_factory, plugins),
        wms_recovery_event_handler=None,
        picking_task_prepare_service=PickingTaskPrepareBatchService(
            session_factory,
            plugins=plugins,
            task_queue_gateway=task_queue_gateway,
        ),
        picking_task_plan_activation_service=PickingTaskPlanActivationService(
            session_factory,
            plugins=plugins,
            transport_creator=ReliableRackTransportCreator(transport_runtime.service),
        ),
    )


__all__ = [
    "DeploymentRuntime",
    "build_deployment_runtime",
]
