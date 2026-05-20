"""工作线货架级任务服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.workline.models.rack_task import (
    WorklineRackTask,
    WorklineRackTaskStatus,
    WorklineRackTaskType,
)
from src.app.workline.repositories.rack_task_repository import (
    WorklineRackTaskRepository,
    workline_rack_task_repository,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


_FULL_BOX_PROGRESS_STATUSES = {
    "ACCEPTED",
    "QUEUED",
    "IN_PROGRESS",
    "PHYSICAL_COMPLETED",
    "RESOURCE_PROJECTED",
    "WMS_CONFIRMED",
}
_FULL_BOX_SUCCESS_STATUSES = {"BUSINESS_COMPLETED"}
_FULL_BOX_FAILED_STATUSES = {
    "REJECTED",
    "WMS_REJECTED",
    "FAILED",
    "FAILED_AGV",
    "FAILED_CTU",
    "CANCELLED",
    "UNKNOWN",
    "REJECTED_EXCHANGE_AREA_FULL",
    "REJECTED_EMPTY_BIN_UNAVAILABLE",
}


class WorklineRackTaskService:
    """工作线货架级任务服务。

    该服务只维护 rack 级任务闭环，不承担 resource 现状投影，也不改变物料
    session 的等待字段；物料 session 只通过 context 引用当前等待的 rack task。
    """

    def __init__(
        self,
        *,
        rack_task_repository: WorklineRackTaskRepository = workline_rack_task_repository,
    ) -> None:
        self.rack_task_repository = rack_task_repository

    async def record_requested_from_rack_task_request(
        self,
        db: AsyncSession,
        *,
        session: Any | None,
        workline: Any,
        outbox: Any,
        task_type: str,
        task_key: str,
        dispatch_key: str,
        target_code: str,
        payload_json: dict[str, Any],
        timeout_seconds: int,
        source_system: str | None,
        trace_id: str | None,
        rack_code: str | None = None,
        position_code: str | None = None,
    ) -> WorklineRackTask:
        """记录 rack task 请求，并保证 task_key/dispatch_key 幂等。"""

        existing = await self.rack_task_repository.get_by_task_key(db, task_key)
        if existing is None:
            existing = await self.rack_task_repository.get_by_dispatch_key(db, dispatch_key)
        if existing is not None:
            return existing

        task = await self.rack_task_repository.create(
            db,
            {
                "task_key": task_key,
                "task_type": _rack_task_type(task_type),
                "task_status": WorklineRackTaskStatus.REQUESTED.value,
                "workline_id": _required_int(getattr(workline, "id", None), "workline.id"),
                "workline_code": _optional_str(getattr(workline, "line_code", None))
                or _optional_str(payload_json.get("workline_code"))
                or _optional_str(payload_json.get("source_workline_code")),
                "material_session_id": _optional_int(getattr(session, "id", None)),
                "rack_code": rack_code or _optional_str(payload_json.get("rack_code")),
                "position_code": position_code
                or _optional_str(payload_json.get("position_code"))
                or _optional_str(payload_json.get("target_position_code")),
                "dispatch_key": dispatch_key,
                "outbox_id": _optional_int(getattr(outbox, "id", None)),
                "target_code": target_code,
                "source_system": source_system,
                "trace_id": trace_id,
                "source_event_id": _source_event_id(payload_json),
                "source_version": _optional_str(payload_json.get("source_version")),
                "request_json": {
                    "payload": dict(payload_json),
                    "timeout_seconds": timeout_seconds,
                    "target_code": target_code,
                    "dispatch_key": dispatch_key,
                },
                "requested_at": timezone.now_for_db(),
            },
        )
        if task is None:
            raise RuntimeError("创建 WorklineRackTask 失败")
        return task

    async def record_callback_from_external_http(
        self,
        db: AsyncSession,
        *,
        payload_json: dict[str, Any],
        trace_id: str | None = None,
        **_: Any,
    ) -> WorklineRackTask | None:
        """按外部回调证据更新 rack task。

        callback ingress 仍由 callback 域负责验签和写入 Inbox；这里仅根据
        dispatch_key / request_code 更新 rack task 状态。
        """

        dispatch_key = (
            _optional_str(payload_json.get("dispatch_key"))
            or _optional_str(payload_json.get("exchange_request_code"))
            or _optional_str(payload_json.get("request_code"))
        )
        if dispatch_key is None:
            return None

        task = await self.rack_task_repository.get_by_dispatch_key(db, dispatch_key)
        if task is None:
            return None

        status, error_code, error_message = _callback_status(payload_json)
        now = timezone.now_for_db()
        task.task_status = status
        task.callback_json = dict(payload_json)
        if trace_id is not None and not _optional_str(getattr(task, "trace_id", None)):
            task.trace_id = trace_id
        if status == WorklineRackTaskStatus.IN_PROGRESS and getattr(task, "started_at", None) is None:
            task.started_at = now
        if status in {
            WorklineRackTaskStatus.SUCCEEDED,
            WorklineRackTaskStatus.FAILED,
            WorklineRackTaskStatus.CANCELLED,
            WorklineRackTaskStatus.RECONCILING,
        }:
            task.completed_at = now
        task.error_code = error_code
        task.error_message = error_message
        db.add(task)
        return task


def _rack_task_type(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized not in {item.value for item in WorklineRackTaskType}:
        raise ValueError(f"不支持的 rack task 类型: {value}")
    return normalized


def _callback_status(payload_json: Mapping[str, Any]) -> tuple[WorklineRackTaskStatus, str | None, str | None]:
    callback_type = _optional_str(payload_json.get("callback_type"))
    raw_status = _optional_str(payload_json.get("status") or payload_json.get("exchange_status"))
    status = raw_status.upper() if raw_status is not None else None

    if callback_type == "WMS_RACK_ARRIVED":
        return WorklineRackTaskStatus.SUCCEEDED, None, None
    if callback_type == "WMS_RACK_EXCHANGE_FAILED":
        return (
            WorklineRackTaskStatus.FAILED,
            _optional_str(payload_json.get("reason_code")) or "WMS_RACK_EXCHANGE_FAILED",
            _optional_str(payload_json.get("reason_message")) or _optional_str(payload_json.get("message")),
        )
    if callback_type == "WMS_RACK_EXCHANGE_PROGRESS":
        return WorklineRackTaskStatus.IN_PROGRESS, None, None

    if status in _FULL_BOX_SUCCESS_STATUSES:
        return WorklineRackTaskStatus.SUCCEEDED, None, None
    if status in _FULL_BOX_PROGRESS_STATUSES:
        return WorklineRackTaskStatus.IN_PROGRESS, None, None
    if status == "RECONCILING":
        return WorklineRackTaskStatus.RECONCILING, "EXCHANGE_RECONCILING", _optional_str(payload_json.get("message"))
    if status in _FULL_BOX_FAILED_STATUSES:
        return (
            WorklineRackTaskStatus.FAILED,
            _optional_str(payload_json.get("reason_code")) or f"FULL_BOX_EXCHANGE_{status}",
            _optional_str(payload_json.get("reason_message")) or _optional_str(payload_json.get("message")),
        )
    return WorklineRackTaskStatus.IN_PROGRESS, None, None


def _source_event_id(payload_json: Mapping[str, Any]) -> str | None:
    return (
        _optional_str(payload_json.get("source_event_id"))
        or _optional_str(payload_json.get("event_id"))
        or _optional_str(payload_json.get("request_id"))
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _required_int(value: Any, field_name: str) -> int:
    resolved = _optional_int(value)
    if resolved is None:
        raise ValueError(f"{field_name} 缺失")
    return resolved


workline_rack_task_service = WorklineRackTaskService()


__all__ = ["WorklineRackTaskService", "workline_rack_task_service"]
