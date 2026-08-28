"""Transport 联调任务定向清理的共享判定合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STATUS_NOT_RECONCILING = "STATUS_NOT_RECONCILING"
TRANSPORT_EVIDENCE_EXISTS = "TRANSPORT_EVIDENCE_EXISTS"
TRANSPORT_OUTCOME_EXISTS = "TRANSPORT_OUTCOME_EXISTS"


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
    eligible: bool
    blockers: tuple[str, ...]
    evidence_count: int
    outcome_version: int
    member_count: int
    binding_count: int
    active_binding_count: int


@dataclass(frozen=True, slots=True)
class TransportDebugResetResult:
    transport_task_id: str
    deleted_member_count: int
    deleted_binding_count: int


def build_transport_debug_reset_preview(
    *,
    transport_task_id: str,
    status: str,
    outcome_version: int,
    outcome_json: dict[str, Any] | None,
    evidence_count: int,
    member_count: int,
    binding_count: int,
    active_binding_count: int,
) -> TransportDebugResetPreview:
    """按同一组硬条件生成 CLI/API 共用的清理预检结果。"""

    blockers: list[str] = []
    if status != "RECONCILING":
        blockers.append(STATUS_NOT_RECONCILING)
    if evidence_count:
        blockers.append(TRANSPORT_EVIDENCE_EXISTS)
    if outcome_version != 0 or outcome_json is not None:
        blockers.append(TRANSPORT_OUTCOME_EXISTS)
    return TransportDebugResetPreview(
        transport_task_id=transport_task_id,
        status=status,
        eligible=not blockers,
        blockers=tuple(blockers),
        evidence_count=evidence_count,
        outcome_version=outcome_version,
        member_count=member_count,
        binding_count=binding_count,
        active_binding_count=active_binding_count,
    )


__all__ = [
    "STATUS_NOT_RECONCILING",
    "TRANSPORT_EVIDENCE_EXISTS",
    "TRANSPORT_OUTCOME_EXISTS",
    "TransportDebugResetPreview",
    "TransportDebugResetResult",
    "build_transport_debug_reset_preview",
    "normalize_transport_task_id",
]
