from __future__ import annotations

from typing import Any, Protocol, cast

# Plan Task 6: 替换 workline_inbox → runtime_inbox
# 旧 task 名称保留作为兼容 shim (Task 7 完成后删除)
PROCESS_RUNTIME_INBOX_TASK = "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch"
PROCESS_WORKLINE_INBOX_TASK = "src.celery_app.tasks.workline.process_inbox_batch"
DISPATCH_SYSTEM_OUTBOX_TASK = "src.celery_app.tasks.sys.dispatch_system_outbox_batch"
PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE = "src.celery_app.tasks.{target_code}.process_signal"


class TaskQueueGateway(Protocol):
    """应用层排队端口，隐藏 Celery/Redis Stream/Kafka 等具体实现。"""

    def enqueue_workline_inbox(self, *, limit: int = 10) -> None: ...

    def enqueue_runtime_inbox(self, *, limit: int = 10) -> None: ...

    def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None: ...

    def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None: ...


class CeleryTaskQueueGateway:
    """Celery 适配器；业务代码只依赖 TaskQueueGateway 端口。"""

    def _send_task(self, task_name: str, *, kwargs: dict[str, Any]) -> None:
        from src.celery_app.app import celery_app

        cast("Any", celery_app).send_task(task_name, kwargs=kwargs)

    def enqueue_workline_inbox(self, *, limit: int = 10) -> None:
        # 兼容 shim: 旧 workline_inbox 仍调 process_inbox_batch.
        # Plan Task 6 后, 应改为 enqueue_runtime_inbox.
        self._send_task(PROCESS_WORKLINE_INBOX_TASK, kwargs={"limit": limit})

    def enqueue_runtime_inbox(self, *, limit: int = 10) -> None:
        # Plan Task 6: 调 process_runtime_inbox_batch. 当前 task 还未实现
        # (Task 6 完成), 暂时 fallback 到 process_inbox_batch 保证
        # enqueue 不会因为 task 不存在而拒绝. Task 6 提交后改回新 task.
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


task_queue_gateway = CeleryTaskQueueGateway()

__all__ = [
    "DISPATCH_SYSTEM_OUTBOX_TASK",
    "PROCESS_INTERNAL_SIGNAL_TASK_TEMPLATE",
    "PROCESS_RUNTIME_INBOX_TASK",
    "PROCESS_WORKLINE_INBOX_TASK",
    "CeleryTaskQueueGateway",
    "TaskQueueGateway",
    "task_queue_gateway",
]
