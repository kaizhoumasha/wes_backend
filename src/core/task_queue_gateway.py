from __future__ import annotations

from typing import Any, Protocol, cast

SUBMIT_TRANSPORT_TASK = "src.celery_app.tasks.transport.submit_transport_tasks_batch"
ADVANCE_TRANSPORT_DEBUG_TASK = "src.celery_app.tasks.transport.advance_transport_debug_runs_batch"
PUBLISH_TRANSPORT_OUTCOMES_TASK = "src.celery_app.tasks.transport.publish_transport_outcomes_batch"
PROCESS_DEVICE_EVIDENCE_TASK = "src.celery_app.tasks.device_command.process_device_evidence_batch"

PROCESS_TRANSPORT_EVIDENCE_TASK = "src.celery_app.tasks.transport.process_transport_evidence_batch"
PROCESS_EXECUTION_FACTS_TASK = "src.celery_app.tasks.execution.process_execution_facts_batch"
DISPATCH_WMS_CONFIRMATIONS_TASK = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
DISPATCH_DEVICE_COMMANDS_TASK = "src.celery_app.tasks.device_command.dispatch_device_commands_batch"


class TaskQueueGateway(Protocol):
    """应用层排队端口，隐藏 Celery/Redis Stream/Kafka 等具体实现。"""

    def enqueue_transport_submit(self) -> None: ...

    def enqueue_transport_debug(self) -> None: ...

    def enqueue_transport_outcomes(self) -> None: ...

    def enqueue_device_evidence(self) -> None: ...

    def enqueue_transport_evidence(self) -> None: ...

    def enqueue_execution_facts(self) -> None: ...

    def enqueue_wms_confirmations(self) -> None: ...

    def enqueue_device_commands(self) -> None: ...


class CeleryTaskQueueGateway:
    """Celery 适配器；业务代码只依赖 TaskQueueGateway 端口。"""

    def _send_task(self, task_name: str, *, kwargs: dict[str, Any]) -> None:
        from src.celery_app.app import celery_app

        cast("Any", celery_app).send_task(task_name, kwargs=kwargs)

    def enqueue_transport_submit(self) -> None:
        self._send_task(SUBMIT_TRANSPORT_TASK, kwargs={"limit": 100})

    def enqueue_transport_debug(self) -> None:
        self._send_task(ADVANCE_TRANSPORT_DEBUG_TASK, kwargs={"limit": 100})

    def enqueue_transport_outcomes(self) -> None:
        self._send_task(PUBLISH_TRANSPORT_OUTCOMES_TASK, kwargs={"limit": 100})

    def enqueue_device_evidence(self) -> None:
        self._send_task(PROCESS_DEVICE_EVIDENCE_TASK, kwargs={"limit": 100})

    def enqueue_transport_evidence(self) -> None:
        self._send_task(PROCESS_TRANSPORT_EVIDENCE_TASK, kwargs={"limit": 100})

    def enqueue_execution_facts(self) -> None:
        self._send_task(PROCESS_EXECUTION_FACTS_TASK, kwargs={})

    def enqueue_wms_confirmations(self) -> None:
        self._send_task(DISPATCH_WMS_CONFIRMATIONS_TASK, kwargs={})

    def enqueue_device_commands(self) -> None:
        self._send_task(DISPATCH_DEVICE_COMMANDS_TASK, kwargs={"limit": 100})


task_queue_gateway = CeleryTaskQueueGateway()

__all__ = [
    "ADVANCE_TRANSPORT_DEBUG_TASK",
    "DISPATCH_DEVICE_COMMANDS_TASK",
    "DISPATCH_WMS_CONFIRMATIONS_TASK",
    "PROCESS_DEVICE_EVIDENCE_TASK",
    "PROCESS_EXECUTION_FACTS_TASK",
    "PROCESS_TRANSPORT_EVIDENCE_TASK",
    "PUBLISH_TRANSPORT_OUTCOMES_TASK",
    "SUBMIT_TRANSPORT_TASK",
    "CeleryTaskQueueGateway",
    "TaskQueueGateway",
    "task_queue_gateway",
]
