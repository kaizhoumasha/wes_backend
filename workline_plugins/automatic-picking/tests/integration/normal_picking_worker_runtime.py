"""完整正常链的测试子进程装配；只复用既有扫描，不安排任何派发。"""

import asyncio
import inspect
import json
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from drain_wait_worker_runtime import FIXTURE, celery_app, record

from src.celery_app.config import beat_schedule, task_routes
from src.core.task_queue_gateway import task_queue_gateway


def build_runtime(*, session_factory, transport_runtime, device_command_service, **kwargs):
    from automatic_picking.application.picking_device_ports import (
        PickingArmBinding,
        PickingArmCommandMapper,
        PickingDepartureReader,
        PickingMaterialScanReader,
        PickingPutReader,
        PickingSourceReader,
        PickingTargetReader,
    )
    from automatic_picking.application.transport_composition import build_picking_plugin, build_transport_plugin

    from src.app.execution.composition import build_execution_runtime
    from src.app.execution.plugin_binding import StaticPluginBinding
    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
    from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
        PickingTaskPlanActivationService,
    )
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    class Bindings:
        async def read_in_session(self, db, *, workline_id):
            assert workline_id == FIXTURE["line_id"]
            return PickingArmBinding(**FIXTURE["arm_binding"])

    class SourceCommands:
        async def read_in_session(self, db, *, evidence_id):
            # 关联是测试明确给定的事实，不把未冻结字段写入设备报文。
            return json.loads(await asyncio.to_thread(Path(FIXTURE["associations"]).read_text)).get(str(evidence_id))

    class UnavailableRotation:
        async def read_in_session(self, *args, **kwargs):
            raise AssertionError("normal-chain acceptance must not enter ROTATE")

    class OfflineWms:
        async def dispatch(self, *args, **kwargs):
            record("forbidden_dispatch")
            raise AssertionError("offline acceptance must not dispatch WMS")

    base = build_transport_plugin(
        session_factory=session_factory,
        transport_service=transport_runtime.service,
        commands=device_command_service,
        workline_owner=RackDepartureOwnerService(),
        task_queue_gateway=task_queue_gateway,
    )
    plugin = build_picking_plugin(
        transport_plugin=base,
        commands=device_command_service,
        physical_reader=PickingSourceReader(),
        arm_commands=PickingArmCommandMapper(Bindings()),
        scan_reader=PickingMaterialScanReader(SourceCommands()),
        target_reader=PickingTargetReader(),
        put_reader=PickingPutReader(),
        rack_transports=ReliableRackTransportCreator(
            transport_runtime.service, inbound_window=RackInboundWindowService()
        ),
        rotation_reader=UnavailableRotation(),
        departure_reader=PickingDepartureReader(),
    )
    execution = build_execution_runtime(
        session_factory=session_factory,
        plugin_binding=StaticPluginBinding((plugin.runtime_binding,)),
        device_command_service=device_command_service,
        transport_service=transport_runtime.service,
        position_projection_service=transport_runtime.position_projection_service,
        wms_confirmation_adapter=OfflineWms(),
        wms_confirmation_follow_up_planner=None,
        task_queue_gateway=task_queue_gateway,
    )
    record(
        "normal_runtime",
        source=inspect.getfile(build_picking_plugin),
        version=plugin.plugin_version,
        queue=os.environ["CELERY_WORKER_QUEUES"],
    )
    return SimpleNamespace(
        execution=execution,
        plugins=(plugin,),
        transport_outcome_publisher=InstalledPluginTransportOutcomePublisher(session_factory, (plugin,)),
        picking_task_plan_activation_service=PickingTaskPlanActivationService(
            session_factory,
            plugins=(plugin,),
            transport_creator=ReliableRackTransportCreator(
                transport_runtime.service, inbound_window=RackInboundWindowService()
            ),
        ),
    )


import deployment.plugin_composition

# 沿用 drain 进程验收的日志、Beat 白名单及通知丢弃，只替换本子进程的完整插件装配。
deployment.plugin_composition.build_deployment_runtime = build_runtime
name = "process-device-evidence-batch"
assert task_routes[beat_schedule[name]["task"]]["queue"] == "device-command"
celery_app.conf.beat_schedule[name] = beat_schedule[name]
# 本验收不测时间合同，只缩短测试子进程的扫描周期；任务和路由仍取既有定义。
celery_app.conf.beat_schedule = {
    key: {**entry, "schedule": 1.0} for key, entry in celery_app.conf.beat_schedule.items()
}
for method in (
    "enqueue_device_evidence",
    "enqueue_device_commands",
    "enqueue_wms_confirmations",
    "enqueue_transport_submit",
    "enqueue_picking_task_prepare",
):
    setattr(task_queue_gateway, method, partial(record, "wakeup_dropped", method=method))
