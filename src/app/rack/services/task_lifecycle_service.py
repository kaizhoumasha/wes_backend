"""货架级任务生命周期服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.rack.models.operation import (
    RackTask,
    RackTaskStatus,
    RackTaskType,
)
from src.app.rack.repositories.operation_repository import (
    RackTaskRepository,
    rack_task_repository,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_str, enum_value, optional_int, require_text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


class RackTaskLifecycleService:
    """货架级任务创建幂等服务。"""

    def __init__(
        self,
        *,
        rack_task_repository: RackTaskRepository = rack_task_repository,
    ) -> None:
        self.rack_task_repository = rack_task_repository

    async def record_requested_task(
        self,
        db: AsyncSession,
        *,
        session: Any | None,
        workline: Any | None,
        outbox: Any,
        operation_id: int | None = None,
        operation_key: str,
        operation_type: str,
        sequence_no: int,
        task_type: str,
        task_key: str,
        dispatch_key: str,
        target_code: str,
        request_json: dict[str, Any],
        timeout_seconds: int | None = None,
        source_system: str | None = None,
        trace_id: str | None = None,
        rack_kind: str | None = None,
        rack_code: str | None = None,
        source_position_code: str | None = None,
        target_position_code: str | None = None,
        target_position_role: str | None = None,
        actions_json: dict[str, Any] | None = None,
    ) -> RackTask:
        """记录 rack task 请求，并保证 task_key 只能指向同一个低级动作。"""

        operation_key = require_text(operation_key, "operation_key")
        operation_type = require_text(operation_type, "operation_type")
        if sequence_no <= 0:
            raise ValueError("operation sequence_no 必须大于 0")
        normalized_task_type = _rack_task_type(task_type)
        existing = await self.rack_task_repository.get_by_task_key(db, task_key)
        if existing is not None:
            _ensure_same_task_identity(
                existing,
                operation_key=operation_key,
                operation_type=operation_type,
                sequence_no=sequence_no,
                task_type=normalized_task_type,
                dispatch_key=dispatch_key,
            )
            return existing

        existing_by_sequence = await self.rack_task_repository.get_by_operation_sequence(
            db,
            operation_key=operation_key,
            sequence_no=sequence_no,
        )
        if existing_by_sequence is not None:
            _ensure_operation_sequence_available(
                existing_by_sequence,
                operation_type=operation_type,
                task_type=normalized_task_type,
                task_key=task_key,
                dispatch_key=dispatch_key,
            )

        existing_by_dispatch = await self.rack_task_repository.get_by_dispatch_key(db, dispatch_key)
        if existing_by_dispatch is not None:
            raise ValueError("dispatch_key 已绑定不同 rack task")

        request_evidence = dict(request_json)
        if timeout_seconds is not None:
            request_evidence.setdefault("timeout_seconds", timeout_seconds)

        task = await self.rack_task_repository.create(
            db,
            {
                "task_key": task_key,
                "operation_id": operation_id,
                "operation_key": operation_key,
                "operation_type": operation_type,
                "sequence_no": sequence_no,
                "task_type": normalized_task_type,
                "task_status": RackTaskStatus.REQUESTED.value,
                "workline_id": optional_int(getattr(workline, "id", None)),
                "workline_code": coerce_optional_str(getattr(workline, "line_code", None))
                or coerce_optional_str(request_json.get("workline_code"))
                or coerce_optional_str(request_json.get("source_workline_code")),
                "material_session_id": optional_int(getattr(session, "id", None)),
                "rack_kind": rack_kind or coerce_optional_str(request_json.get("rack_kind")),
                "rack_code": rack_code or coerce_optional_str(request_json.get("rack_code")),
                "source_position_code": source_position_code
                or coerce_optional_str(request_json.get("source_position_code")),
                "target_position_code": target_position_code
                or coerce_optional_str(request_json.get("target_position_code")),
                "target_position_role": target_position_role
                or coerce_optional_str(request_json.get("target_position_role")),
                "dispatch_key": dispatch_key,
                "outbox_id": optional_int(getattr(outbox, "id", None)),
                "target_code": target_code,
                "source_system": source_system,
                "trace_id": trace_id,
                "source_event_id": _source_event_id(request_json),
                "source_version": coerce_optional_str(request_json.get("source_version")),
                "request_json": request_evidence,
                "actions_json": dict(actions_json or {}),
                "requested_at": timezone.now_for_db(),
            },
        )
        if task is None:
            raise RuntimeError("创建 RackTask 失败")
        return task


def _ensure_same_task_identity(
    task: Any,
    *,
    operation_key: str,
    operation_type: str,
    sequence_no: int,
    task_type: str,
    dispatch_key: str,
) -> None:
    if (
        getattr(task, "operation_key", None) != operation_key
        or getattr(task, "operation_type", None) != operation_type
        or getattr(task, "sequence_no", None) != sequence_no
        or enum_value(getattr(task, "task_type", None)) != task_type
        or getattr(task, "dispatch_key", None) != dispatch_key
    ):
        raise ValueError("task_key 已绑定不同 rack task")


def _ensure_operation_sequence_available(
    task: Any,
    *,
    operation_type: str,
    task_type: str,
    task_key: str,
    dispatch_key: str,
) -> None:
    if (
        getattr(task, "operation_type", None) != operation_type
        or enum_value(getattr(task, "task_type", None)) != task_type
        or getattr(task, "task_key", None) != task_key
        or getattr(task, "dispatch_key", None) != dispatch_key
    ):
        raise ValueError("operation sequence 已绑定不同 rack task")


def _rack_task_type(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized not in {item.value for item in RackTaskType}:
        raise ValueError(f"不支持的 rack task 类型: {value}")
    return normalized


def _source_event_id(payload_json: Mapping[str, Any]) -> str | None:
    return (
        coerce_optional_str(payload_json.get("source_event_id"))
        or coerce_optional_str(payload_json.get("event_id"))
        or coerce_optional_str(payload_json.get("request_id"))
    )


def _required_int(value: Any, field_name: str) -> int:
    resolved = optional_int(value)
    if resolved is None:
        raise ValueError(f"{field_name} 缺失")
    return resolved


rack_task_lifecycle_service = RackTaskLifecycleService()


__all__ = ["RackTaskLifecycleService", "rack_task_lifecycle_service"]
