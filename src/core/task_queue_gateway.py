from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Collection

PROCESS_RUNTIME_INBOX_TASK = "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch"
DISPATCH_SYSTEM_OUTBOX_TASK = "src.celery_app.tasks.sys.dispatch_system_outbox_batch"
DISPATCH_WMS_DATA_OUTBOX_TASK = "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch"
DISPATCH_WMS_FULFILLMENT_OUTBOX_TASK = "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch"
PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE = "src.celery_app.tasks.{target_code}.process_signal"
CHECK_WMS_EFFECT_STATUS_TASK = "src.celery_app.tasks.workline.check_wms_effect_status"
PROCESS_TRANSPORT_EVIDENCE_TASK = "src.celery_app.tasks.transport.process_transport_evidence_batch"
PROCESS_EXECUTION_FACTS_TASK = "src.celery_app.tasks.execution.process_execution_facts_batch"
DISPATCH_WMS_CONFIRMATIONS_TASK = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
DISPATCH_DEVICE_COMMANDS_TASK = "src.celery_app.tasks.device_command.dispatch_device_commands_batch"
DRAIN_SAFETY_INCIDENTS_TASK = "src.celery_app.tasks.workline.drain_safety_incidents_batch"


class OutboxDispatchTarget(StrEnum):
    """事务提交后可公开唤醒的瞬时 Outbox dispatcher target。"""

    SYSTEM = "SYSTEM"
    WMS_DATA = "WMS_DATA"
    WMS_FULFILLMENT = "WMS_FULFILLMENT"


_OUTBOX_TASK_BY_TARGET = {
    OutboxDispatchTarget.SYSTEM: DISPATCH_SYSTEM_OUTBOX_TASK,
    OutboxDispatchTarget.WMS_DATA: DISPATCH_WMS_DATA_OUTBOX_TASK,
    OutboxDispatchTarget.WMS_FULFILLMENT: DISPATCH_WMS_FULFILLMENT_OUTBOX_TASK,
}


class TaskQueueGateway(Protocol):
    """应用层排队端口，隐藏 Celery/Redis Stream/Kafka 等具体实现。"""

    def enqueue_runtime_inbox(self, *, limit: int = 10) -> None: ...

    def enqueue_outbox(self, *, targets: Collection[OutboxDispatchTarget], limit: int = 50) -> None: ...

    def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None: ...

    def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None: ...

    def enqueue_transport_evidence(self) -> None: ...

    def enqueue_execution_facts(self) -> None: ...

    def enqueue_wms_confirmations(self) -> None: ...

    def enqueue_device_commands(self) -> None: ...

    def enqueue_safety_drain(self) -> None: ...


class CeleryTaskQueueGateway:
    """Celery 适配器；业务代码只依赖 TaskQueueGateway 端口。"""

    def _send_task(self, task_name: str, *, kwargs: dict[str, Any]) -> None:
        from src.celery_app.app import celery_app

        cast("Any", celery_app).send_task(task_name, kwargs=kwargs)

    def enqueue_runtime_inbox(self, *, limit: int = 10) -> None:
        self._send_task(PROCESS_RUNTIME_INBOX_TASK, kwargs={"limit": limit})

    def enqueue_outbox(self, *, targets: Collection[OutboxDispatchTarget], limit: int = 50) -> None:
        for target in OutboxDispatchTarget:
            if target in targets:
                self._send_task(_OUTBOX_TASK_BY_TARGET[target], kwargs={"limit": limit})

    def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None:
        self._send_task(
            PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE.format(target_code=target_code),
            kwargs={"payload": payload},
        )

    def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None:
        self._send_task(CHECK_WMS_EFFECT_STATUS_TASK, kwargs={"dispatch_key": dispatch_key})

    def enqueue_transport_evidence(self) -> None:
        self._send_task(PROCESS_TRANSPORT_EVIDENCE_TASK, kwargs={"limit": 100})

    def enqueue_execution_facts(self) -> None:
        self._send_task(PROCESS_EXECUTION_FACTS_TASK, kwargs={})

    def enqueue_wms_confirmations(self) -> None:
        self._send_task(DISPATCH_WMS_CONFIRMATIONS_TASK, kwargs={})

    def enqueue_device_commands(self) -> None:
        self._send_task(DISPATCH_DEVICE_COMMANDS_TASK, kwargs={"limit": 100})

    def enqueue_safety_drain(self) -> None:
        self._send_task(DRAIN_SAFETY_INCIDENTS_TASK, kwargs={"limit": 10, "command_limit": 100})


task_queue_gateway = CeleryTaskQueueGateway()

__all__ = [
    "CHECK_WMS_EFFECT_STATUS_TASK",
    "DISPATCH_DEVICE_COMMANDS_TASK",
    "DISPATCH_SYSTEM_OUTBOX_TASK",
    "DISPATCH_WMS_CONFIRMATIONS_TASK",
    "DISPATCH_WMS_DATA_OUTBOX_TASK",
    "DISPATCH_WMS_FULFILLMENT_OUTBOX_TASK",
    "DRAIN_SAFETY_INCIDENTS_TASK",
    "PROCESS_EXECUTION_FACTS_TASK",
    "PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE",
    "PROCESS_RUNTIME_INBOX_TASK",
    "PROCESS_TRANSPORT_EVIDENCE_TASK",
    "CeleryTaskQueueGateway",
    "OutboxDispatchTarget",
    "TaskQueueGateway",
    "task_queue_gateway",
]
