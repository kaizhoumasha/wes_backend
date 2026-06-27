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
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository, outbox_repository
from src.app.workline.models.session import SessionStatus
from src.app.workline.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.core.logger import logger
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_str, enum_value, optional_int

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


_PROGRESS_STATUSES = {
    "ACCEPTED",
    "QUEUED",
    "IN_PROGRESS",
    "PHYSICAL_COMPLETED",
    "RESOURCE_PROJECTED",
    "WMS_CONFIRMED",
}
_SUCCESS_STATUSES = {"SUCCEEDED", "SUCCESS", "COMPLETED", "BUSINESS_COMPLETED"}
_FAILED_STATUSES = {
    "FAILED",
    "FAILED_AGV",
    "FAILED_CTU",
    "REJECTED",
    "REJECTED_EMPTY_BIN_UNAVAILABLE",
    "REJECTED_EXCHANGE_AREA_FULL",
    "WMS_REJECTED",
    "ERROR",
    "UNKNOWN",
}
_CANCELLED_STATUSES = {"CANCELLED", "CANCELED"}
_TIMEOUT_STATUSES = {"TIMEOUT", "TIMED_OUT"}
_RECONCILING_STATUSES = {
    "RECONCILING",
    "RESOURCE_RECONCILING",
    "RESOURCE_UNCONFIRMED",
    "RESOURCE_PROJECTION_UNCONFIRMED",
    "PROJECTION_UNCONFIRMED",
}


class RackTaskLifecycleService:
    """货架级任务生命周期服务。

    该服务维护单个 rack task 的创建幂等和回调状态；回调后按 operation
    派生状态更新等待中的物料 session。
    """

    def __init__(
        self,
        *,
        rack_task_repository: RackTaskRepository = rack_task_repository,
        session_repository: WorklineSessionRepository = workline_session_repository,
        outbox_repository: SystemOutboxRepository = outbox_repository,
        rack_operation_service: Any | None = None,
    ) -> None:
        self.rack_task_repository = rack_task_repository
        self.session_repository = session_repository
        self.outbox_repository = outbox_repository
        self._rack_operation_service = rack_operation_service

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

        operation_key = _required_text(operation_key, "operation_key")
        operation_type = _required_text(operation_type, "operation_type")
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

    async def record_callback_from_external_http(
        self,
        db: AsyncSession,
        *,
        payload_json: dict[str, Any],
        trace_id: str | None = None,
        **_: Any,
    ) -> RackTask | None:
        """按外部回调证据更新单个 rack task。"""

        dispatch_key = coerce_optional_str(payload_json.get("dispatch_key")) or coerce_optional_str(
            payload_json.get("request_code")
        )
        if dispatch_key is None:
            return None

        task = await self.rack_task_repository.get_by_dispatch_key(db, dispatch_key)
        if task is None:
            return None

        status, error_code, error_message = _callback_status(payload_json)
        should_sync_waiting_session = _should_sync_waiting_session_from_callback(payload_json)
        if _is_terminal_task_status(getattr(task, "task_status", None)):
            logger.warning(
                "Ignoring late rack task callback for terminal task: "
                f"dispatch_key={dispatch_key}, current_status={_task_status_value(getattr(task, 'task_status', None))}, "
                f"incoming_status={status.value}"
            )
            if trace_id is not None and not coerce_optional_str(getattr(task, "trace_id", None)):
                task.trace_id = trace_id
                db.add(task)
            await self._finish_sent_external_outbox(db, task=task, dispatch_key=dispatch_key)
            if should_sync_waiting_session:
                await self._sync_waiting_session_from_operation_status(
                    db,
                    task=task,
                    error_code=coerce_optional_str(getattr(task, "error_code", None)),
                    error_message=coerce_optional_str(getattr(task, "error_message", None)),
                )
            return task

        now = timezone.now_for_db()
        task.task_status = status
        task.callback_json = dict(payload_json)
        task.result_json = _task_result_json(status=status, error_code=error_code, error_message=error_message)
        if trace_id is not None and not coerce_optional_str(getattr(task, "trace_id", None)):
            task.trace_id = trace_id
        if status == RackTaskStatus.IN_PROGRESS and getattr(task, "started_at", None) is None:
            task.started_at = now
        if status in {
            RackTaskStatus.SUCCEEDED,
            RackTaskStatus.FAILED,
            RackTaskStatus.TIMEOUT,
            RackTaskStatus.CANCELLED,
            RackTaskStatus.RECONCILING,
        }:
            task.completed_at = now
        task.error_code = error_code
        task.error_message = error_message
        db.add(task)
        if _is_terminal_task_status(status):
            await self._finish_sent_external_outbox(db, task=task, dispatch_key=dispatch_key)
        if should_sync_waiting_session:
            await self._sync_waiting_session_from_operation_status(
                db,
                task=task,
                error_code=error_code,
                error_message=error_message,
            )
        return task

    async def _finish_sent_external_outbox(self, db: AsyncSession, *, task: RackTask, dispatch_key: str) -> None:
        if not hasattr(task, "outbox_id"):
            return
        await self.outbox_repository.finish_sent_external_by_dispatch_key(db, dispatch_key)

    def _resolve_rack_operation_service(self) -> Any:
        if self._rack_operation_service is None:
            from src.app.rack.services.operation_service import rack_operation_service

            self._rack_operation_service = rack_operation_service
        return self._rack_operation_service

    async def _sync_waiting_session_from_operation_status(
        self,
        db: AsyncSession,
        *,
        task: RackTask,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        operation_key = coerce_optional_str(getattr(task, "operation_key", None))
        workline_id = optional_int(getattr(task, "workline_id", None))
        if operation_key is None:
            return
        if self._rack_operation_service is None and not hasattr(db, "execute"):
            return

        operation_service = self._resolve_rack_operation_service()
        persist_operation_status = getattr(operation_service, "_persist_operation_status", None)
        if callable(persist_operation_status):
            operation_status = await persist_operation_status(db, operation_key=operation_key)
        else:
            operation_status = await operation_service.derive_operation_status(db, operation_key=operation_key)
        if workline_id is None:
            return
        session = await self.session_repository.get_open_session_by_waiting_rack_operation_key(
            db,
            workline_id=workline_id,
            operation_key=operation_key,
        )
        if session is None:
            logger.warning(
                "Rack operation callback derived status but no waiting material session was found: "
                f"workline_id={workline_id}, operation_key={operation_key}, operation_status={operation_status}"
            )
            return

        _apply_operation_status_to_session(
            session,
            operation_key=operation_key,
            operation_status=operation_status,
            error_code=error_code,
            error_message=error_message,
        )
        db.add(session)


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


def _task_status_value(value: Any) -> str | None:
    raw = enum_value(value)
    return raw if isinstance(raw, str) else None


def _is_terminal_task_status(value: Any) -> bool:
    return _task_status_value(value) in {
        RackTaskStatus.SUCCEEDED.value,
        RackTaskStatus.FAILED.value,
        RackTaskStatus.TIMEOUT.value,
        RackTaskStatus.CANCELLED.value,
        RackTaskStatus.RECONCILING.value,
    }


def _should_sync_waiting_session_from_callback(payload_json: Mapping[str, Any]) -> bool:
    # 到架回调还需要同一个 inbox 的插件先投影 RACK_ARRIVED/BIN_MOUNTED 资源事实。
    return coerce_optional_str(payload_json.get("callback_type")) not in {"WMS_RACK_ARRIVED", "RCS_RACK_ARRIVED"}


def _apply_operation_status_to_session(
    session: Any,
    *,
    operation_key: str,
    operation_status: str,
    error_code: str | None,
    error_message: str | None,
) -> None:
    context_json = dict(getattr(session, "context_json", None) or {})
    existing_operation = context_json.get("rack_operation")
    rack_operation = dict(existing_operation) if isinstance(existing_operation, dict) else {}
    rack_operation["operation_key"] = operation_key
    rack_operation["status"] = operation_status
    if error_code is not None:
        rack_operation["reason_code"] = error_code
    if error_message is not None:
        rack_operation["message"] = error_message
    context_json["rack_operation"] = rack_operation

    if operation_status == "SUCCEEDED":
        context_json["waiting_rack_operation_key"] = None
        session.status = SessionStatus.RUNNING
        session.current_wait_type = None
        session.waiting_since = None
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_device_command_code = None
        session.failure_domain = None
        session.failure_code = None
        session.failure_message = None
        session.ended_at = None
    elif operation_status == "PENDING":
        context_json["waiting_rack_operation_key"] = operation_key
    else:
        existing_failure_code = coerce_optional_str(getattr(session, "failure_code", None))
        existing_failure_message = coerce_optional_str(getattr(session, "failure_message", None))
        context_json["waiting_rack_operation_key"] = operation_key
        session.status = SessionStatus.MANUAL_HOLD
        session.current_wait_type = None
        session.waiting_since = None
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_device_command_code = None
        session.failure_domain = "EXTERNAL"
        session.failure_code = (
            error_code if error_code is not None else existing_failure_code or f"RACK_OPERATION_{operation_status}"
        )
        session.failure_message = (
            error_message
            if error_message is not None
            else existing_failure_message or f"Rack operation {operation_key} derived {operation_status}"
        )

    session.context_json = context_json


def _rack_task_type(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized not in {item.value for item in RackTaskType}:
        raise ValueError(f"不支持的 rack task 类型: {value}")
    return normalized


def _callback_status(payload_json: Mapping[str, Any]) -> tuple[RackTaskStatus, str | None, str | None]:
    callback_type = coerce_optional_str(payload_json.get("callback_type"))
    raw_status = coerce_optional_str(
        payload_json.get("task_status")
        or payload_json.get("status")
        or payload_json.get("result")
        or payload_json.get("external_status")
        or payload_json.get("exchange_status")
    )
    status = raw_status.upper() if raw_status is not None else None

    if callback_type in {"WMS_RACK_ARRIVED", "RCS_RACK_ARRIVED"}:
        return RackTaskStatus.SUCCEEDED, None, None
    if callback_type in {"WMS_RACK_EXCHANGE_FAILED", "RCS_RACK_EXCHANGE_FAILED"}:
        return RackTaskStatus.FAILED, _raw_error_code(payload_json), _raw_error_message(payload_json)
    if callback_type in {"WMS_RACK_TASK_PROGRESS", "RCS_RACK_TASK_PROGRESS"}:
        return RackTaskStatus.IN_PROGRESS, None, None

    task_status = _resolve_task_status(status)
    return task_status, _raw_error_code(payload_json), _raw_error_message(payload_json)


def _resolve_task_status(status: str | None) -> RackTaskStatus:
    if status in _SUCCESS_STATUSES:
        return RackTaskStatus.SUCCEEDED
    if status in _PROGRESS_STATUSES:
        return RackTaskStatus.IN_PROGRESS
    if status in _RECONCILING_STATUSES:
        return RackTaskStatus.RECONCILING
    if status in _TIMEOUT_STATUSES:
        return RackTaskStatus.TIMEOUT
    if status in _CANCELLED_STATUSES:
        return RackTaskStatus.CANCELLED
    if status in _FAILED_STATUSES:
        return RackTaskStatus.FAILED
    if status is not None and (status.startswith(("FAILED", "REJECTED")) or status.endswith("_REJECTED")):
        return RackTaskStatus.FAILED
    return RackTaskStatus.IN_PROGRESS


def _task_result_json(
    *,
    status: RackTaskStatus,
    error_code: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"task_status": status.value}
    if error_code is not None:
        result["external_error_code"] = error_code
    if error_message is not None:
        result["external_error_message"] = error_message
    return result


def _raw_error_code(payload_json: Mapping[str, Any]) -> str | None:
    return (
        coerce_optional_str(payload_json.get("reason_code"))
        or coerce_optional_str(payload_json.get("error_code"))
        or coerce_optional_str(payload_json.get("code"))
    )


def _raw_error_message(payload_json: Mapping[str, Any]) -> str | None:
    return (
        coerce_optional_str(payload_json.get("reason_message"))
        or coerce_optional_str(payload_json.get("error_message"))
        or coerce_optional_str(payload_json.get("message"))
    )


def _source_event_id(payload_json: Mapping[str, Any]) -> str | None:
    return (
        coerce_optional_str(payload_json.get("source_event_id"))
        or coerce_optional_str(payload_json.get("event_id"))
        or coerce_optional_str(payload_json.get("request_id"))
    )


def _required_text(value: Any, field_name: str) -> str:
    text = coerce_optional_str(value)
    if text is None:
        raise ValueError(f"operation {field_name} 不能为空")
    return text


def _required_int(value: Any, field_name: str) -> int:
    resolved = optional_int(value)
    if resolved is None:
        raise ValueError(f"{field_name} 缺失")
    return resolved


rack_task_lifecycle_service = RackTaskLifecycleService()


__all__ = ["RackTaskLifecycleService", "rack_task_lifecycle_service"]
