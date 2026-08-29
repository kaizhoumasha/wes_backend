"""Execution 事务 advisory lock 的稳定 identity。"""

from __future__ import annotations

_SEPARATOR = "\x1f"


def build_advisory_lock_identity(namespace: str, *parts: object) -> str:
    """构造交给 PostgreSQL ``hashtextextended(..., 0)`` 的稳定锁 identity。"""

    values = (namespace, *(str(part) for part in parts))
    if any(not value or _SEPARATOR in value for value in values):
        raise ValueError("advisory lock identity parts must be non-empty and must not contain the unit separator")
    return _SEPARATOR.join(values)


def epoch_lifecycle_lock_identity(line_run_epoch_id: int) -> str:
    return build_advisory_lock_identity("epoch-lifecycle", line_run_epoch_id)


def bin_execution_lock_identity(bin_id: str) -> str:
    return build_advisory_lock_identity("bin-execution", bin_id)


def position_projection_lock_identity(object_type: str, object_id: str) -> str:
    return build_advisory_lock_identity("position-projection", object_type, object_id)


__all__ = [
    "bin_execution_lock_identity",
    "build_advisory_lock_identity",
    "epoch_lifecycle_lock_identity",
    "position_projection_lock_identity",
]
