"""完整正常链的测试子进程装配；只复用既有扫描，不安排任何派发。"""

import asyncio
import json
import os
from functools import partial
from pathlib import Path

import deployment.plugin_composition
from deployment.plugin_configuration import DeploymentPluginSettings, deployment_plugin_settings
from src.celery_app.config import beat_schedule, task_routes
from src.core.task_queue_gateway import task_queue_gateway

# 先保存真实组合根；复用的进程支撑模块会安装其 drain 专用测试替身。
build_installed_runtime = deployment.plugin_composition.build_deployment_runtime
from drain_wait_worker_runtime import FIXTURE, celery_app, record

if "plugin_env_file" in FIXTURE:
    DeploymentPluginSettings.model_config["env_file"] = FIXTURE["plugin_env_file"]
    deployment_plugin_settings.cache_clear()


def build_runtime(*, session_factory, transport_runtime, device_command_service, **kwargs):
    class SourceCommands:
        async def read_in_session(self, db, *, evidence_id):
            # 测试只提供可靠归属，不向设备报文增加未确认字段。
            return json.loads(await asyncio.to_thread(Path(FIXTURE["associations"]).read_text)).get(str(evidence_id))

    class OfflineWms:
        async def dispatch(self, *args, **kwargs):
            record("forbidden_dispatch")
            raise AssertionError("offline acceptance must not dispatch WMS")

    kwargs["enabled_plugin_keys"] = ("automatic-picking",)
    runtime = build_installed_runtime(
        session_factory=session_factory,
        transport_runtime=transport_runtime,
        device_command_service=device_command_service,
        automatic_picking_source_commands=SourceCommands(),
        **kwargs,
    )
    runtime.execution.wms_confirmation_service._adapter = OfflineWms()
    plugin = runtime.plugins[0]
    record(
        "normal_runtime",
        source=__file__,
        version=plugin.plugin_version,
        queue=os.environ["CELERY_WORKER_QUEUES"],
        first_plan_timeout_ms=plugin.picking_task_completion_driver.first_plan_timeout_ms,
    )
    return runtime


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
