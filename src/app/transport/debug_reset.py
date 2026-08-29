"""Transport 联调任务定向清理的共享合同。"""

from __future__ import annotations

from dataclasses import dataclass


def normalize_transport_task_id(value: str) -> str:
    """归一化 CLI/Service 共用的持久化任务身份。"""

    task_id = value.strip()
    if not task_id or len(task_id) > 80 or "\x00" in task_id:
        raise ValueError("transport_task_id must contain 1..80 non-NUL characters")
    return task_id


@dataclass(frozen=True, slots=True)
class TransportDebugResetPreview:
    transport_task_id: str
    status: str
    evidence_count: int
    callback_receipt_count: int
    position_projection_count: int
    outcome_version: int
    member_count: int
    binding_count: int
    active_binding_count: int


@dataclass(frozen=True, slots=True)
class TransportDebugResetResult:
    transport_task_id: str
    deleted_callback_receipt_count: int
    deleted_evidence_count: int
    deleted_position_projection_count: int
    deleted_member_count: int
    deleted_binding_count: int


__all__ = [
    "TransportDebugResetPreview",
    "TransportDebugResetResult",
    "normalize_transport_task_id",
]
