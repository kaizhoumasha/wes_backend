"""部署制品的显式业务插件清单与通用运行时组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from deployment.plugin_definitions import load_plugin_definitions
from src.app.device.services import device_service
from src.app.execution.composition import ExecutionRuntime, build_execution_runtime
from src.app.execution.plugin_binding import StaticPluginBinding
from src.app.execution.repositories.position_projection_repository import position_projection_repository
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.execution.services.reliable_rack_transport import ReliableBinTransportCreator, ReliableRackTransportCreator
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationLifecycleService,
    WorkLineConfirmationOwnerPort,
)
from src.app.transport.debug_run_service import TransportDebugReturnBatchOwner
from src.app.transport.repository import TransportRepository
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter

# Web/worker 组合根注册共享外键目标；不依赖具体插件是否安装或启用。
from src.app.wms_integration.outbound_picking.models import PickingTask  # noqa: F401
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.wms_integration.outbound_picking.services.bin_batch import (
    BinBatchResultReader,
    BinBatchScheduler,
    BinInboundBatchOwnerService,
)
from src.app.wms_integration.outbound_picking.services.manual_bin_admission import (
    ManualBinAdmissionOwnerService,
    ManualBinAdmissionScheduler,
)
from src.app.wms_integration.outbound_picking.services.picking_task_completion import PickingTaskCompletionScheduler
from src.app.wms_integration.outbound_picking.services.picking_task_confirmation_owner import (
    PickingTaskConfirmationOwnerService,
)
from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
    PickingTaskPlanActivationService,
)
from src.app.wms_integration.outbound_picking.services.picking_task_prepare_batch import (
    PickingTaskPrepareBatchService,
)
from src.app.wms_integration.outbound_picking.services.rack_departure import (
    RackDepartureResultReader,
    RackDepartureScheduler,
)
from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
from src.app.wms_integration.outbound_picking.services.return_batch_owner import ReturnBatchOwnerService
from src.app.wms_integration.outbound_picking.services.return_rack_arrival import (
    ReturnRackArrivalResultReader,
    ReturnRackArrivalScheduler,
)
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainOwnerService
from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher, InstalledPluginWmsFollowUpPlanner
from src.app.workline.services.workline_archive_service import WorkLineArchiveService
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.app.workline.services.workline_start_service import WorkLineStartService
from src.core.task_queue_gateway import task_queue_gateway
from src.core.uuid7 import new_uuid7

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.composition import DeviceEndpointAdapterProvider
    from src.app.device.services import DeviceCommandService
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationAdapterPort
    from src.app.transport.composition import TransportRuntime
    from src.app.workline.installed_plugin import InstalledWorkLinePlugin


@dataclass(frozen=True, slots=True)
class DeploymentRuntime:
    execution: ExecutionRuntime
    plugins: tuple[InstalledWorkLinePlugin, ...]
    workline_start_service: WorkLineStartService
    workline_configuration_service: WorkLineConfigurationService
    workline_archive_service: WorkLineArchiveService
    transport_outcome_publisher: InstalledPluginTransportOutcomePublisher
    wms_recovery_event_handler: object | None
    picking_task_prepare_service: PickingTaskPrepareBatchService
    picking_task_plan_activation_service: PickingTaskPlanActivationService


class CombinedWorkLineConfirmationOwner:
    def __init__(self, first: WorkLineConfirmationOwnerPort, second: WorkLineConfirmationOwnerPort) -> None:
        self._first = first
        self._second = second

    async def validate_owner(self, db: AsyncSession, *, workline_id: int, request_payload: dict[str, Any]) -> bool:
        if await self._first.validate_owner(db, workline_id=workline_id, request_payload=request_payload):
            return True
        return await self._second.validate_owner(db, workline_id=workline_id, request_payload=request_payload)


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
    prepare_workline_reserved: Callable[[AsyncSession, int], Awaitable[bool]] | None = None
    workline_owner = CombinedWorkLineConfirmationOwner(
        CombinedWorkLineConfirmationOwner(ReturnBatchOwnerService(), TransportDebugReturnBatchOwner()),
        ReturnBufferDrainOwnerService(),
    )
    workline_owner = CombinedWorkLineConfirmationOwner(workline_owner, RackDepartureOwnerService())
    workline_owner = CombinedWorkLineConfirmationOwner(workline_owner, BinInboundBatchOwnerService())
    plugins: tuple[InstalledWorkLinePlugin, ...] = ()
    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.application.batch_driver import ManualPickingBatchDriver
        from manual_picking.application.batch_flow import ManualPickingBatchFlow
        from manual_picking.application.batch_repository import BatchRepository
        from manual_picking.application.batch_result import ManualPickingBatchResultFlow
        from manual_picking.application.completion_flow import ManualPickingCompletionFlow
        from manual_picking.application.completion_repository import ManualPickingCompletionRepository
        from manual_picking.application.drain_flow import ManualPickingDrainFlow
        from manual_picking.application.drain_repository import DrainRepository
        from manual_picking.application.passage_repository import PassageRepository
        from manual_picking.application.plugin import build_plugin
        from manual_picking.application.scan_flow import ManualPickingScanFlow
        from manual_picking.prepare_policy import ManualPickingPreparePolicy

        from src.app.wms_adapter.outbound_picking import manual_bin_typed
        from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator
        from src.app.wms_integration.return_buffer_drain import (
            ReturnBufferDrainResultReader,
            ReturnBufferDrainScheduler,
        )

        workline_owner = CombinedWorkLineConfirmationOwner(workline_owner, ManualBinAdmissionOwnerService())
        passages = PassageRepository()
        batch_reader = BinBatchResultReader()
        drain_reader = ReturnBufferDrainResultReader()
        drains = DrainRepository(drain_reader)

        prepare_workline_reserved = drains.is_reserved

        batch_scheduler = BinBatchScheduler(WmsConfirmationLifecycleService(workline_owner=workline_owner))
        rack_creator = ReliableRackTransportCreator(
            transport_runtime.service, inbound_window=RackInboundWindowService()
        )
        batch_repository = BatchRepository(batch_reader)
        batch_result = ManualPickingBatchResultFlow(
            batch_reader, ReliableBinTransportCreator(transport_runtime.service), passages
        )
        drain_flow = ManualPickingDrainFlow(
            drains,
            passages,
            PickingTaskPrepareCoordinator(
                session_factory,
                policy=ManualPickingPreparePolicy(),
                task_queue_gateway=task_queue_gateway,
                workline_reserved=prepare_workline_reserved,
            ),
            ReturnBufferDrainScheduler(WmsConfirmationLifecycleService(workline_owner=workline_owner)),
            batch_scheduler,
            batch_reader,
        )
        batch_driver = ManualPickingBatchDriver(
            ManualPickingBatchFlow(
                batch_repository,
                passages,
                batch_scheduler,
                batch_result,
                uuid_factory=new_uuid7,
            ),
            plans=PickingTaskPlanDeltaRepository(),
            positions=position_projection_repository,
            transports=TransportRepository(),
            rack_creator=rack_creator,
            departure_scheduler=RackDepartureScheduler(WmsConfirmationLifecycleService(workline_owner=workline_owner)),
            departure_reader=RackDepartureResultReader(),
            arrival_scheduler=ReturnRackArrivalScheduler(WmsConfirmationLifecycleService()),
            arrival_reader=ReturnRackArrivalResultReader(),
            passages=passages,
            drain=drain_flow,
        )
        scan_flow = ManualPickingScanFlow(
            commands=device_command_service,
            admissions=ManualBinAdmissionScheduler(WmsConfirmationLifecycleService(workline_owner=workline_owner)),
            wms_reader=manual_bin_typed,
            batch_reader=batch_reader,
            batch_result=batch_result,
            drain_repository=drains,
            drain_reader=drain_reader,
            rack_creator=rack_creator,
        )
        completion_driver = ManualPickingCompletionFlow(
            ManualPickingCompletionRepository(history=batch_reader),
            PickingTaskCompletionScheduler(WmsConfirmationLifecycleService(workline_owner=workline_owner)),
            uuid_factory=new_uuid7,
        )
        plugins = (
            build_plugin(
                scan_flow=scan_flow,
                batch_driver=batch_driver,
                completion_driver=completion_driver,
                drain_flow=drain_flow,
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
        workline_owner=workline_owner,
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
            drain_triggers={
                plugin.plugin_key: plugin.drain_trigger for plugin in plugins if plugin.drain_trigger is not None
            },
            device_cache_invalidator=device_service,
        ),
        workline_archive_service=WorkLineArchiveService(
            plugins=plugins,
        ),
        transport_outcome_publisher=InstalledPluginTransportOutcomePublisher(session_factory, plugins),
        wms_recovery_event_handler=None,
        picking_task_prepare_service=PickingTaskPrepareBatchService(
            session_factory,
            plugins=plugins,
            task_queue_gateway=task_queue_gateway,
            workline_reserved=prepare_workline_reserved,
        ),
        picking_task_plan_activation_service=PickingTaskPlanActivationService(
            session_factory,
            plugins=plugins,
            transport_creator=ReliableRackTransportCreator(
                transport_runtime.service, inbound_window=RackInboundWindowService()
            ),
        ),
    )


__all__ = [
    "DeploymentRuntime",
    "build_deployment_runtime",
]
