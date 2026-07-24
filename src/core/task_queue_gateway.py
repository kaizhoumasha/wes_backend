from __future__ import annotations

from typing import Any, Protocol, cast

PROCESS_RUNTIME_INBOX_TASK = "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch"
DISPATCH_SYSTEM_OUTBOX_TASK = "src.celery_app.tasks.sys.dispatch_system_outbox_batch"
PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE = "src.celery_app.tasks.{target_code}.process_signal"
PROCESS_QUERY_SHADOW_COMPARISON_TASK = "src.celery_app.tasks.workline.process_query_shadow_comparison"
CHECK_WMS_EFFECT_STATUS_TASK = "src.celery_app.tasks.workline.check_wms_effect_status"


class TaskQueueGateway(Protocol):
    """应用层排队端口，隐藏 Celery/Redis Stream/Kafka 等具体实现。"""

    def enqueue_runtime_inbox(self, *, limit: int = 10) -> None: ...

    def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None: ...

    def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None: ...

    def enqueue_query_shadow_comparison(self, payload: dict[str, Any]) -> None: ...

    def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None: ...


class CeleryTaskQueueGateway:
    """Celery 适配器；业务代码只依赖 TaskQueueGateway 端口。"""

    def _send_task(self, task_name: str, *, kwargs: dict[str, Any]) -> None:
        from src.celery_app.app import celery_app

        cast("Any", celery_app).send_task(task_name, kwargs=kwargs)

    def enqueue_runtime_inbox(self, *, limit: int = 10) -> None:
        self._send_task(PROCESS_RUNTIME_INBOX_TASK, kwargs={"limit": limit})

    def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None:
        # 当前 Celery 任务是批量兜底模型；outbox_id 保留给单条队列后端使用。
        _ = outbox_id
        self._send_task(DISPATCH_SYSTEM_OUTBOX_TASK, kwargs={"limit": limit})

    def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None:
        self._send_task(
            PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE.format(target_code=target_code),
            kwargs={"payload": payload},
        )

    def enqueue_query_shadow_comparison(self, payload: dict[str, Any]) -> None:
        self._send_task(PROCESS_QUERY_SHADOW_COMPARISON_TASK, kwargs={"payload": payload})

    def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None:
        self._send_task(CHECK_WMS_EFFECT_STATUS_TASK, kwargs={"dispatch_key": dispatch_key})


task_queue_gateway = CeleryTaskQueueGateway()

__all__ = [
    "CHECK_WMS_EFFECT_STATUS_TASK",
    "DISPATCH_SYSTEM_OUTBOX_TASK",
    "PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE",
    "PROCESS_QUERY_SHADOW_COMPARISON_TASK",
    "PROCESS_RUNTIME_INBOX_TASK",
    "CeleryTaskQueueGateway",
    "TaskQueueGateway",
    "task_queue_gateway",
]
