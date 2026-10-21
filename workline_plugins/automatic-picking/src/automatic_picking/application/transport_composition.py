"""自动线货架/料箱搬运的显式装配；不安装机械臂或完成确认路径。"""

from automatic_picking.prepare_policy import AutomaticPickingPreparePolicy
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.execution.services.reliable_rack_transport import ReliableBinTransportCreator, ReliableRackTransportCreator
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.wms_integration.outbound_picking.services import BinWorkPlanResultReader, BinWorkPlanScheduler
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchScheduler
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator
from src.app.wms_integration.outbound_picking.services.rack_departure import (
    RackDepartureResultReader,
    RackDepartureScheduler,
)
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader, ReturnBufferDrainScheduler

from .batch_driver import AutomaticPickingBatchDriver
from .drain_departure import DrainDepartureFlow
from .drain_flow import AutomaticPickingDrainFlow
from .drain_inbound import DrainInboundFlow
from .drain_replenishment_reader import DrainReplenishmentReader
from .drain_repository import DrainRepository
from .drain_rotation import DrainRotationFlow
from .feed_flow import AutomaticPickingFeedFlow
from .feed_repository import FeedRepository
from .flow import AutomaticPickingWorkFlow
from .plugin import build_plugin
from .return_allocation_flow import AutomaticPickingReturnBatchFlow
from .return_allocation_repository import ReturnAllocationRepository
from .return_device_ports import ReturnCommandMapper, ReturnDirectionReader, ReturnScanReader
from .return_flow import AutomaticPickingReturnFlow
from .return_repository import ReturnRepository
from .scan_flow import AutomaticPickingScanFlow
from .source_rack_candidates import SourceRackDepartureReader, SourceRackFaceReader, SourceRackMotionReader
from .source_rack_departure import SourceRackDepartureFlow
from .source_rack_motion import SourceRackMotionFlow
from .transport_readers import DrainInboundReader, ReturnBufferReader, ReturnLocationReader, SourceRackRotationReader
from .work_repository import WorkRepository


def build_transport_plugin(*, session_factory, transport_service, commands, workline_owner, task_queue_gateway):
    works, feeds = WorkRepository(), FeedRepository()
    work_reader = BinWorkPlanResultReader()
    work = AutomaticPickingWorkFlow(works, BinWorkPlanScheduler(), work_reader)
    confirmations = WmsConfirmationLifecycleService(workline_owner=workline_owner)
    batches = BinBatchScheduler(confirmations)
    bins = ReliableBinTransportCreator(transport_service)
    racks = ReliableRackTransportCreator(transport_service, inbound_window=RackInboundWindowService())
    locations, buffer = ReturnLocationReader(), ReturnBufferReader()
    mapper = ReturnCommandMapper()
    exits = AutomaticPickingReturnFlow(
        ReturnRepository(),
        commands,
        work_reader,
        mapper.exit_request,
        ReturnScanReader("SCAN3"),
        scan4_reader=ReturnScanReader("SCAN4"),
        direction_mapper=mapper.direction_request,
        direction_reader=ReturnDirectionReader(),
    )
    returns = AutomaticPickingReturnBatchFlow(
        ReturnAllocationRepository(),
        batches,
        bins,
        SourceRackFaceReader(),
        locations,
        motion_reader=SourceRackMotionReader(),
    )
    departure_scheduler = RackDepartureScheduler(confirmations)
    departure_reader = RackDepartureResultReader()
    rotation = SourceRackMotionFlow(returns, racks, batches, SourceRackRotationReader())
    departure = SourceRackDepartureFlow(
        returns,
        racks,
        SourceRackDepartureReader(),
        scheduler=departure_scheduler,
        departure_reader=departure_reader,
    )
    drain_rotation = DrainRotationFlow(returns, racks)
    drain_departure = DrainDepartureFlow(drain_rotation, scheduler=departure_scheduler)
    drain = AutomaticPickingDrainFlow(
        DrainRepository(),
        PickingTaskPrepareCoordinator(
            session_factory, policy=AutomaticPickingPreparePolicy(), task_queue_gateway=task_queue_gateway
        ),
        ReturnBufferDrainScheduler(confirmations),
        ReturnBufferDrainResultReader(),
        buffer,
        locations,
        inbound=DrainInboundFlow(racks, DrainInboundReader(), rotation=drain_rotation, departure=drain_departure),
        replenishment_reader=DrainReplenishmentReader(buffer_reader=buffer, location_reader=locations),
    )
    batch = AutomaticPickingBatchDriver(
        works,
        feeds,
        work,
        AutomaticPickingFeedFlow(feeds, bins),
        batches,
        exit_flow=exits,
        return_batch_flow=returns,
        rack_motion_flow=rotation,
        rack_departure_flow=departure,
        drain_flow=drain,
    )
    scan = AutomaticPickingScanFlow(
        works,
        feeds,
        work,
        commands,
        batch_driver=batch,
        exit_flow=exits,
        return_batch_flow=returns,
        rack_motion_flow=rotation,
        rack_departure_flow=departure,
    )
    return build_plugin(works=works, batch_driver=batch, scan_flow=scan)


def build_picking_plugin(
    *,
    transport_plugin,
    commands,
    physical_reader,
    source_request_factory,
    scan_reader,
    target_reader,
    target_request_factory,
    put_reader,
    rack_transports,
    rotation_reader,
    departure_reader,
):
    """在已装配搬运插件上接通正常逐盘链；设备参数/精确事实读口必须显式提供。"""
    from copy import copy

    from automatic_picking.definition import DEFINITION
    from src.app.execution.services import MaterialExecutionService
    from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader, MaterialDecideScheduler

    from .material_flow import AutomaticPickingMaterialFlow
    from .pickup_repository import PickupRepository
    from .placement_flow import AutomaticPickingPlacementFlow
    from .source_flow import AutomaticPickingSourceFlow
    from .target_preparation import AutomaticPickingTargetFlow
    from .target_replacement import AutomaticPickingTargetReplacementFlow
    from .target_rotation import AutomaticPickingTargetRotationFlow
    from .target_rotation_repository import TargetRotationRepository

    if transport_plugin.definition != DEFINITION or transport_plugin.runtime_binding is None:
        raise ValueError("picking assembly requires the automatic transport plugin")
    # 保留调用方已装配搬运对象；新装配仅在显式安装返回值后生效。
    batch = copy(transport_plugin.picking_task_batch_driver)
    scan = copy(transport_plugin.runtime_binding.business_evidence_consumer)
    scan.batch_driver = batch
    repository, reader = PickupRepository(physical_reader), MaterialDecideResultReader()
    source = AutomaticPickingSourceFlow(repository, commands, source_request_factory)
    material = AutomaticPickingMaterialFlow(
        repository,
        source,
        scan_reader,
        MaterialExecutionService(),
        scheduler=MaterialDecideScheduler(),
        reader=reader,
        put_flow=AutomaticPickingPlacementFlow(repository, reader, put_reader, scan_reader=scan_reader),
    )
    target = AutomaticPickingTargetFlow(
        repository,
        commands,
        reader,
        target_reader,
        target_request_factory,
        rotation_flow=AutomaticPickingTargetRotationFlow(
            TargetRotationRepository(physical_reader), rack_transports, reader, rotation_reader
        ),
        replacement_flow=AutomaticPickingTargetReplacementFlow(
            rack_transports, departure_reader, scheduler=batch.rack_departure_flow.scheduler
        ),
    )
    batch.source_flow, batch.material_flow, batch.target_flow = source, material, target
    scan.source_flow, scan.material_flow = source, material
    return build_plugin(
        scan_flow=scan, batch_driver=batch, works=transport_plugin.business_blocker, material_flow=material
    )
