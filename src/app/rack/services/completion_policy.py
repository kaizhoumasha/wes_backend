"""Rack operation 完成策略解释器。"""

from __future__ import annotations

from typing import Any

from src.app.rack.models.operation import RackOperationStatus, RackTaskStatus
from src.app.sys.models import OperationCompletionPolicy
from src.utils.value_normalization import enum_value


def resolve_operation_completion_policy(operation: Any | None) -> OperationCompletionPolicy:
    """从持久化 operation 中解析完成策略，异常值按 Rack 默认策略处理。"""

    raw_policy = getattr(operation, "completion_policy", None)
    try:
        return OperationCompletionPolicy(enum_value(raw_policy))
    except ValueError:
        return OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED


def resolve_request_completion_policy(
    completion_policy: OperationCompletionPolicy | str | None,
) -> OperationCompletionPolicy:
    """解析请求指定的完成策略；Rack 默认必须等待资源投影确认。"""

    if completion_policy is not None:
        return OperationCompletionPolicy(enum_value(completion_policy))
    return OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED


def derive_required_task_status(required_tasks: list[Any]) -> RackOperationStatus | None:
    """按 required task 状态派生 projection 前的 operation 状态。

    返回 None 表示所有 required task 已成功，调用方可继续执行策略特定的投影确认。
    """

    if not required_tasks:
        return RackOperationStatus.PENDING

    statuses = {_task_status(task) for task in required_tasks}
    if statuses & {
        RackTaskStatus.FAILED.value,
        RackTaskStatus.TIMEOUT.value,
        RackTaskStatus.CANCELLED.value,
    }:
        return RackOperationStatus.FAILED
    if RackTaskStatus.RECONCILING.value in statuses:
        return RackOperationStatus.RECONCILING
    if statuses & {
        RackTaskStatus.PLANNED.value,
        RackTaskStatus.REQUESTED.value,
        RackTaskStatus.IN_PROGRESS.value,
    }:
        return RackOperationStatus.PENDING
    return None


def requires_resource_projection_confirmation(completion_policy: OperationCompletionPolicy) -> bool:
    return completion_policy == OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED


def _task_status(task: Any) -> str | None:
    return enum_value(getattr(task, "task_status", None))


__all__ = [
    "derive_required_task_status",
    "requires_resource_projection_confirmation",
    "resolve_operation_completion_policy",
    "resolve_request_completion_policy",
]
