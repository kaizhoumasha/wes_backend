"""仅由离线 worker/Beat 测试显式加载；不进入生产装配。"""

import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

from celery import signals

from src.celery_app.app import celery_app
from src.celery_app.config import beat_schedule, task_routes
from tests.support import transport_broker

FIXTURE = json.loads(Path(os.environ["AUTOMATIC_PICKING_WAIT_FIXTURE"]).read_text())
TASK = "src.celery_app.tasks.picking_task_plan.activate_picking_task_plans_batch"


def record(kind, **values):
    with Path(FIXTURE["events"]).open("a") as output:
        output.write(json.dumps({"kind": kind, "pid": os.getpid(), "pgid": os.getpgrp(), **values}) + "\n")


@signals.after_task_publish.connect(weak=False)
def published(sender=None, headers=None, routing_key=None, **kwargs):
    record("published", task=sender, task_id=headers["id"], queue=routing_key)


@signals.task_prerun.connect(weak=False)
def received(task_id=None, task=None, **kwargs):
    record("received", task=task.name, task_id=task_id, queue=task.request.delivery_info["routing_key"])


@signals.task_postrun.connect(weak=False)
def finished(task_id=None, state=None, retval=None, **kwargs):
    record("finished", task_id=task_id, state=state, result=retval)


def build_runtime(*, session_factory, **kwargs):
    from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
    from automatic_picking.application.drain_flow import AutomaticPickingDrainFlow, MatchedDrainBuffer
    from automatic_picking.application.drain_replenishment_reader import DrainReplenishmentReader
    from automatic_picking.application.drain_repository import DrainRepository
    from automatic_picking.application.feed_repository import FeedRepository
    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.return_allocation_flow import MatchedReturnLocation
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from automatic_picking.application.work_repository import WorkRepository
    from automatic_picking.prepare_policy import AutomaticPickingPreparePolicy

    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
        PickingTaskPlanActivationService,
    )
    from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator
    from src.app.wms_integration.return_buffer_drain import (
        ReturnBufferDrainOwnerService,
        ReturnBufferDrainResultReader,
        ReturnBufferDrainScheduler,
    )

    class Buffer:
        async def read_in_session(self, db, *, workline_id):
            assert workline_id == FIXTURE["line_id"]
            if FIXTURE["location"] is None:
                return None
            return MatchedDrainBuffer(FIXTURE["location"], FIXTURE["capacity"])

    class Location:
        async def read_in_session(self, db, *, return_id):
            assert return_id == FIXTURE["return_id"]
            row = await db.get(AutomaticPickingReturn, return_id)
            assert row.workline_id == FIXTURE["line_id"] and row.bin_code == FIXTURE["bin_code"]
            return MatchedReturnLocation(row.id, row.workline_id, row.bin_code, FIXTURE["location"])

    buffer, location = Buffer(), Location()
    inbound = None
    if "inbound" in FIXTURE or FIXTURE.get("departure_recovery"):
        from datetime import timedelta

        from automatic_picking.application.drain_inbound import DrainInboundFlow, MatchedDrainInbound
        from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

        from src.app.execution.services.rack_inbound_window import RackInboundWindowService
        from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
        from src.app.transport.repository import TransportRepository
        from src.app.transport.service import TransportService

        class Provider:
            async def submit(self, **kwargs):
                raise AssertionError("offline READY must not submit Transport")

        class InboundReader:
            async def read_in_session(self, db, *, workline_id, operation_id, ready_evidence_id, rack_id):
                values = FIXTURE["inbound"]
                assert (workline_id, operation_id, ready_evidence_id, rack_id) == (
                    values["workline_id"],
                    values["operation_id"],
                    values["ready_evidence_id"],
                    values["rack_id"],
                )
                return MatchedDrainInbound(
                    workline_id=values["workline_id"],
                    operation_id=values["operation_id"],
                    ready_evidence_id=values["ready_evidence_id"],
                    rack_id=values["rack_id"],
                    target=TransportRackPosition(values["location"]),
                    target_face=values["face"],
                    rcs_template_id=TransportRcsTemplateId(values["template"]),
                )

        window = RackInboundWindowService()
        service = TransportService(
            session_factory,
            TransportRepository(),
            Provider(),
            result_timeout=timedelta(seconds=420),
            progress_hook=window.on_transport_progress,
        )
        inbound = DrainInboundFlow(ReliableRackTransportCreator(service, inbound_window=window), InboundReader())
        if FIXTURE.get("departure_recovery"):
            from automatic_picking.application.drain_departure import DrainDepartureFlow
            from automatic_picking.application.drain_rotation import DrainRotationFlow
            from automatic_picking.application.return_allocation_flow import AutomaticPickingReturnBatchFlow
            from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository

            # 只读原义务的当前 drain 依据；不装配新分配、调度或离场决定端口。
            returns = AutomaticPickingReturnBatchFlow(ReturnAllocationRepository(), None, None, None, None)
            inbound.rotation = DrainRotationFlow(returns, inbound.creator)
            inbound.departure = DrainDepartureFlow(inbound.rotation)
    drain = AutomaticPickingDrainFlow(
        DrainRepository(),
        PickingTaskPrepareCoordinator(session_factory, policy=AutomaticPickingPreparePolicy(), task_queue_gateway=None),
        ReturnBufferDrainScheduler(WmsConfirmationLifecycleService(workline_owner=ReturnBufferDrainOwnerService())),
        ReturnBufferDrainResultReader(),
        buffer,
        location,
        inbound=inbound,
        replenishment_reader=DrainReplenishmentReader(buffer_reader=buffer, location_reader=location),
    )
    works = WorkRepository()
    batch = AutomaticPickingBatchDriver(works, FeedRepository(), None, None, None, drain_flow=drain)
    plugin = build_plugin(scan_flow=None, batch_driver=batch, works=works)
    record(
        "runtime",
        plugin=plugin.plugin_key,
        version=plugin.plugin_version,
        source=inspect.getfile(DrainReplenishmentReader),
        inbound_source=inspect.getfile(type(inbound)) if inbound is not None else None,
        departure_source=inspect.getfile(type(inbound.departure)) if inbound and inbound.departure else None,
    )
    return SimpleNamespace(
        picking_task_plan_activation_service=PickingTaskPlanActivationService(
            session_factory, plugins=(plugin,), transport_creator=None
        )
    )


# 只替换当前测试子进程的组合函数；任务、宿主扫描和业务方法保持原实现。
import deployment.plugin_composition

deployment.plugin_composition.build_deployment_runtime = build_runtime
assert task_routes[TASK]["queue"] == "celery"
celery_app.conf.beat_schedule = {
    "activate-picking-task-plans-batch": beat_schedule["activate-picking-task-plans-batch"]
}
