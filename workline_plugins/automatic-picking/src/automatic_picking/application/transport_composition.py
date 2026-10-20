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
