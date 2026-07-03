"""系统级 Handling operation 生命周期服务。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.handling.models import BinTransitQueue, HandlingMoveStatus, HandlingOperationStatus, HandlingStepStatus
from src.app.handling.repositories import (
    HandlingMoveRepository,
    HandlingOperationRepository,
    HandlingStepRepository,
    handling_move_repository,
    handling_operation_repository,
    handling_step_repository,
)
from src.app.handling.services.bin_transit_membership_service import (
    BinTransitMembershipService,
    bin_transit_membership_service,
)
from src.app.handling.services.completion_policy import is_reconciled_exchange_operation_type
from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.core.logger import logger
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_str, optional_int

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.ext.asyncio import AsyncSession


class _AsyncTransactionContext(Protocol):
    async def __aenter__(self) -> Any: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


class _BeginNestedCallable(Protocol):
    def __call__(self) -> _AsyncTransactionContext: ...


_FULL_BOX_EXCHANGE_CALLBACK_TYPES = {"WMS_FULL_BOX_EXCHANGE_RESULT", "RCS_FULL_BOX_EXCHANGE_RESULT"}
_POST_EXCHANGE_RELATIONS_REQUIRED_STATUSES = {"PHYSICAL_COMPLETED", "RESOURCE_PROJECTED"}
_PROGRESS_STATUSES = {
    "ACCEPTED",
    "QUEUED",
    "IN_PROGRESS",
    "PHYSICAL_COMPLETED",
    "RESOURCE_PROJECTED",
    "WMS_CONFIRMED",
}
_SUCCESS_STATUSES = {"SUCCEEDED", "SUCCESS", "COMPLETED", "BUSINESS_COMPLETED"}
_FAILED_STATUSES = {"FAILED", "FAILED_CTU", "WMS_REJECTED", "REJECTED", "ERROR", "UNKNOWN"}
_CANCELLED_STATUSES = {"CANCELLED", "CANCELED"}
_TIMEOUT_STATUSES = {"TIMEOUT", "TIMED_OUT"}
_RECONCILING_STATUSES = {"RECONCILING", "RESOURCE_UNCONFIRMED", "PROJECTION_UNCONFIRMED"}


class HandlingOperationLifecycleService:
    """维护 Handling step/operation 状态，并同步等待中的工作线 session。"""

    def __init__(
        self,
        *,
        operation_repository: HandlingOperationRepository = handling_operation_repository,
        move_repository: HandlingMoveRepository = handling_move_repository,
        step_repository: HandlingStepRepository = handling_step_repository,
        session_repository: WorklineSessionRepository = workline_session_repository,
        membership_service: BinTransitMembershipService = bin_transit_membership_service,
    ) -> None:
        self.operation_repository = operation_repository
        self.move_repository = move_repository
        self.step_repository = step_repository
        self.session_repository = session_repository
        self.membership_service = membership_service

    async def record_callback_from_external_http(
        self,
        db: AsyncSession,
        *,
        payload_json: dict[str, Any],
        trace_id: str | None = None,
        **_: Any,
    ) -> Any | None:
        """按 WMS/RCS 回调证据更新 handling step。"""

        dispatch_key = (
            coerce_optional_str(payload_json.get("dispatch_key"))
            or coerce_optional_str(payload_json.get("exchange_request_code"))
            or coerce_optional_str(payload_json.get("request_code"))
        )
        if dispatch_key is None:
            return None

        step = await self.step_repository.get_by_dispatch_key(db, dispatch_key)
        if step is None:
            return None

        status, error_code, error_message = _callback_step_status(payload_json)
        operation_key = coerce_optional_str(getattr(step, "operation_key", None))
        operation = await self.operation_repository.get_by_operation_key(db, operation_key) if operation_key else None
        post_exchange_relations_error = _post_exchange_relations_error(operation, payload_json)
        if status != HandlingStepStatus.RECONCILING and post_exchange_relations_error is not None:
            status = HandlingStepStatus.RECONCILING
            error_code, error_message = post_exchange_relations_error
        if _source_version_is_stale(getattr(step, "callback_json", None), payload_json):
            logger.warning(
                "Ignoring stale handling step callback: "
                f"dispatch_key={dispatch_key}, current_version={_callback_source_version(getattr(step, 'callback_json', None))}, "
                f"incoming_version={_callback_source_version(payload_json)}"
            )
            return step

        if _is_terminal_step_status(getattr(step, "step_status", None)) and not _allows_terminal_override(
            getattr(step, "step_status", None),
            status,
        ):
            logger.warning(
                "Ignoring late handling step callback for terminal step: "
                f"dispatch_key={dispatch_key}, current_status={_step_status_value(getattr(step, 'step_status', None))}, "
                f"incoming_status={status.value}"
            )
            return step

        now = timezone.now_for_db()
        step.step_status = status
        step.callback_json = dict(payload_json)
        step.result_json = _step_result_json(status=status, error_code=error_code, error_message=error_message)
        if trace_id is not None and not coerce_optional_str(getattr(step, "trace_id", None)):
            step.trace_id = trace_id
        if status == HandlingStepStatus.IN_PROGRESS and getattr(step, "started_at", None) is None:
            step.started_at = now
        if status in {
            HandlingStepStatus.SUCCEEDED,
            HandlingStepStatus.FAILED,
            HandlingStepStatus.TIMEOUT,
            HandlingStepStatus.CANCELLED,
            HandlingStepStatus.RECONCILING,
        }:
            step.completed_at = now
        step.error_code = error_code
        step.error_message = error_message
        db.add(step)
        await self._sync_move_for_step(db, step=step, step_status=status)
        final_status, final_error_code = await self._sync_operation_and_session(
            db,
            step=step,
            payload_json=payload_json,
            error_code=error_code,
            error_message=error_message,
        )
        await self._project_queue_membership_best_effort(
            db,
            step=step,
            step_status=final_status,
            payload_json=payload_json,
            trace_id=trace_id,
            reason_code=final_error_code,
        )
        return step

    async def _project_queue_membership_best_effort(
        self,
        db: AsyncSession,
        *,
        step: Any,
        step_status: HandlingStepStatus,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
        reason_code: str | None = None,
    ) -> None:
        dispatch_key = coerce_optional_str(getattr(step, "dispatch_key", None))
        begin_nested = getattr(db, "begin_nested", None)
        has_nested_transaction = callable(begin_nested)
        if has_nested_transaction:
            await db.flush()
        try:
            if has_nested_transaction:
                nested_transaction = cast("_BeginNestedCallable", begin_nested)
                async with nested_transaction():
                    await self._project_queue_membership_from_callback(
                        db,
                        step=step,
                        step_status=step_status,
                        payload_json=payload_json,
                        trace_id=trace_id,
                        reason_code=reason_code,
                    )
            else:
                await self._project_queue_membership_from_callback(
                    db,
                    step=step,
                    step_status=step_status,
                    payload_json=payload_json,
                    trace_id=trace_id,
                    reason_code=reason_code,
                )
        except Exception as exc:
            logger.warning(
                "Handling queue membership projection failed; lifecycle state update is kept for reconciliation: "
                f"dispatch_key={dispatch_key}, "
                f"step_status={step_status.value}, error={exc}"
            )

    async def _sync_move_for_step(self, db: AsyncSession, *, step: Any, step_status: HandlingStepStatus) -> None:
        move_id = optional_int(getattr(step, "move_id", None))
        if move_id is None:
            return
        move = await self.move_repository.get_by_id(db, move_id)
        if move is None:
            return
        move.move_status = _move_status_for_step(step_status)
        db.add(move)

    async def _project_queue_membership_from_callback(
        self,
        db: AsyncSession,
        *,
        step: Any,
        step_status: HandlingStepStatus,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
        reason_code: str | None = None,
    ) -> None:
        move_id = optional_int(getattr(step, "move_id", None))
        if move_id is None:
            return
        move = await self.move_repository.get_by_id(db, move_id)
        if move is None:
            return

        target_queue = _callback_target_queue(payload_json) or _move_target_queue(move)

        bin_code = coerce_optional_str(getattr(move, "bin_code", None))
        placeholder_key = coerce_optional_str(getattr(move, "placeholder_key", None))
        if bin_code is None and placeholder_key is None:
            logger.debug(
                "Skipping handling queue membership projection without bin_code/placeholder_key: "
                f"move_id={move_id}, target_queue={target_queue.value if target_queue is not None else None}"
            )
            return

        operation_key = coerce_optional_str(getattr(step, "operation_key", None))
        operation = await self.operation_repository.get_by_operation_key(db, operation_key) if operation_key else None
        common_kwargs = {
            "bin_code": bin_code,
            "placeholder_key": placeholder_key,
            "handling_operation_id": optional_int(getattr(operation, "id", None)) if operation is not None else None,
            "handling_move_id": move_id,
            "trace_id": trace_id,
            "reason_code": reason_code or f"HANDLING_CALLBACK_{step_status.value}",
            "source_event_id": f"{coerce_optional_str(getattr(step, 'dispatch_key', None)) or move_id}:{step_status.value}",
            "evidence_json": dict(payload_json),
            "auto_commit": False,
        }

        if step_status == HandlingStepStatus.RECONCILING:
            await self.membership_service.mark_reconciling(db, ignore_missing=True, **common_kwargs)
            return

        if step_status in {
            HandlingStepStatus.SUCCEEDED,
            HandlingStepStatus.FAILED,
            HandlingStepStatus.TIMEOUT,
            HandlingStepStatus.CANCELLED,
        }:
            if target_queue is not None:
                await self.membership_service.switch_queue(
                    db,
                    to_queue=target_queue,
                    workline_id=optional_int(getattr(operation, "workline_id", None))
                    if operation is not None
                    else None,
                    workline_code=coerce_optional_str(getattr(operation, "workline_code", None))
                    if operation is not None
                    else None,
                    workline_session_id=(
                        optional_int(getattr(operation, "material_session_id", None)) if operation is not None else None
                    ),
                    **common_kwargs,
                )
            await self.membership_service.leave_queue(db, ignore_missing=True, **common_kwargs)
            return

        if target_queue is None:
            return

        await self.membership_service.switch_queue(
            db,
            to_queue=target_queue,
            workline_id=optional_int(getattr(operation, "workline_id", None)) if operation is not None else None,
            workline_code=coerce_optional_str(getattr(operation, "workline_code", None))
            if operation is not None
            else None,
            workline_session_id=(
                optional_int(getattr(operation, "material_session_id", None)) if operation is not None else None
            ),
            **common_kwargs,
        )

    async def derive_operation_status(self, db: AsyncSession, *, operation_key: str) -> str:
        operation = await self.operation_repository.get_by_operation_key(db, operation_key)
        operation_id = getattr(operation, "id", None)
        if not isinstance(operation_id, int):
            return HandlingOperationStatus.REQUESTED.value
        steps = await self.step_repository.list_by_operation_id(db, operation_id)
        return _derive_operation_status(steps).value

    async def _sync_operation_and_session(
        self,
        db: AsyncSession,
        *,
        step: Any,
        payload_json: Mapping[str, Any],
        error_code: str | None,
        error_message: str | None,
    ) -> tuple[HandlingStepStatus, str | None]:
        operation_key = coerce_optional_str(getattr(step, "operation_key", None))
        if operation_key is None:
            return _as_step_status(getattr(step, "step_status", None)), error_code

        operation = await self.operation_repository.get_by_operation_key(db, operation_key)
        if operation is None:
            return _as_step_status(getattr(step, "step_status", None)), error_code

        workline_id = optional_int(getattr(operation, "workline_id", None))
        session = None
        if workline_id is not None:
            session = await self.session_repository.get_open_session_by_waiting_handling_operation_key(
                db,
                workline_id=workline_id,
                operation_key=operation_key,
            )
        business_error = _business_context_error(session, payload_json)
        if business_error is not None:
            step.step_status = HandlingStepStatus.RECONCILING
            step.error_code = business_error[0]
            step.error_message = business_error[1]
            step.result_json = _step_result_json(
                status=HandlingStepStatus.RECONCILING,
                error_code=business_error[0],
                error_message=business_error[1],
            )
            db.add(step)
            await self._sync_move_for_step(db, step=step, step_status=HandlingStepStatus.RECONCILING)
            error_code, error_message = business_error

        operation_id = getattr(operation, "id", None)
        steps = (
            await self.step_repository.list_by_operation_id(db, operation_id) if isinstance(operation_id, int) else []
        )
        operation_status = _derive_operation_status(steps)
        operation.operation_status = operation_status
        operation.error_code = error_code
        operation.error_message = error_message
        if operation_status in {
            HandlingOperationStatus.SUCCEEDED,
            HandlingOperationStatus.FAILED,
            HandlingOperationStatus.TIMEOUT,
            HandlingOperationStatus.CANCELLED,
            HandlingOperationStatus.RECONCILING,
        }:
            operation.completed_at = timezone.now_for_db()
        db.add(operation)

        if session is None:
            return _as_step_status(getattr(step, "step_status", None)), error_code

        _apply_operation_status_to_session(
            session,
            operation_key=operation_key,
            operation_status=operation_status.value,
            error_code=error_code,
            error_message=error_message,
        )
        db.add(session)
        return _as_step_status(getattr(step, "step_status", None)), error_code


def _derive_operation_status(steps: list[Any]) -> HandlingOperationStatus:
    if not steps:
        return HandlingOperationStatus.REQUESTED
    statuses = {_step_status_value(getattr(step, "step_status", None)) for step in steps}
    if any(status in {HandlingStepStatus.FAILED.value} for status in statuses):
        return HandlingOperationStatus.FAILED
    if any(status in {HandlingStepStatus.TIMEOUT.value} for status in statuses):
        return HandlingOperationStatus.TIMEOUT
    if any(status in {HandlingStepStatus.CANCELLED.value} for status in statuses):
        return HandlingOperationStatus.CANCELLED
    if any(status in {HandlingStepStatus.RECONCILING.value} for status in statuses):
        return HandlingOperationStatus.RECONCILING
    if all(status == HandlingStepStatus.SUCCEEDED.value for status in statuses):
        return HandlingOperationStatus.SUCCEEDED
    if any(status == HandlingStepStatus.IN_PROGRESS.value for status in statuses):
        return HandlingOperationStatus.IN_PROGRESS
    return HandlingOperationStatus.REQUESTED


def _move_status_for_step(step_status: HandlingStepStatus) -> HandlingMoveStatus:
    try:
        return HandlingMoveStatus(step_status.value)
    except ValueError:
        if step_status == HandlingStepStatus.READY:
            return HandlingMoveStatus.PLANNED
        return HandlingMoveStatus.REQUESTED


def _as_step_status(value: Any) -> HandlingStepStatus:
    if isinstance(value, HandlingStepStatus):
        return value
    try:
        return HandlingStepStatus(str(value))
    except ValueError:
        return HandlingStepStatus.IN_PROGRESS


def _allows_terminal_override(current_status: Any, incoming_status: HandlingStepStatus) -> bool:
    """允许可对账状态被后续可信终态推进。"""

    return _step_status_value(current_status) == HandlingStepStatus.RECONCILING.value and incoming_status in {
        HandlingStepStatus.SUCCEEDED,
        HandlingStepStatus.FAILED,
        HandlingStepStatus.TIMEOUT,
        HandlingStepStatus.CANCELLED,
    }


def _source_version_is_stale(existing_callback_json: Any, incoming_payload_json: Mapping[str, Any]) -> bool:
    existing_version = _callback_source_version(existing_callback_json)
    incoming_version = _callback_source_version(incoming_payload_json)
    if existing_version is None or incoming_version is None:
        return False
    return _version_sort_key(incoming_version) <= _version_sort_key(existing_version)


def _callback_source_version(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return coerce_optional_str(value.get("source_version"))


def _version_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _business_context_error(session: Any | None, payload_json: Mapping[str, Any]) -> tuple[str, str] | None:
    if session is None:
        return None
    callback_type = coerce_optional_str(payload_json.get("callback_type"))
    if callback_type not in _FULL_BOX_EXCHANGE_CALLBACK_TYPES:
        return None
    incoming_rack_release_id = coerce_optional_str(payload_json.get("rack_release_id"))
    if incoming_rack_release_id is None:
        return None

    context_json = dict(getattr(session, "context_json", None) or {})
    handling_operation = context_json.get("handling_operation")
    expected_rack_release_id = None
    if isinstance(handling_operation, Mapping):
        expected_rack_release_id = coerce_optional_str(handling_operation.get("rack_release_id"))
    expected_rack_release_id = expected_rack_release_id or coerce_optional_str(context_json.get("rack_release_id"))
    if expected_rack_release_id is not None and incoming_rack_release_id != expected_rack_release_id:
        return (
            "RACK_RELEASE_ID_MISMATCH",
            f"满箱交换回调 rack_release_id={incoming_rack_release_id} 与等待上下文 {expected_rack_release_id} 不一致",
        )
    return None


def _post_exchange_relations_error(operation: Any | None, payload_json: Mapping[str, Any]) -> tuple[str, str] | None:
    if operation is None:
        return None
    raw_status = coerce_optional_str(
        payload_json.get("exchange_status")
        or payload_json.get("task_status")
        or payload_json.get("status")
        or payload_json.get("result")
        or payload_json.get("external_status")
    )
    status = raw_status.upper() if raw_status is not None else None
    if status not in _POST_EXCHANGE_RELATIONS_REQUIRED_STATUSES or _has_post_exchange_relations(payload_json):
        return None
    operation_type = coerce_optional_str(getattr(operation, "operation_type", None))
    if operation_type is None or not is_reconciled_exchange_operation_type(operation_type):
        return None
    return (
        "POST_EXCHANGE_RELATIONS_MISSING",
        "交换物理完成回调缺少 post_exchange_relations，已进入资源对账",
    )


def _callback_step_status(payload_json: Mapping[str, Any]) -> tuple[HandlingStepStatus, str | None, str | None]:
    callback_type = coerce_optional_str(payload_json.get("callback_type"))
    raw_status = coerce_optional_str(
        payload_json.get("exchange_status")
        or payload_json.get("task_status")
        or payload_json.get("status")
        or payload_json.get("result")
        or payload_json.get("external_status")
    )
    status = raw_status.upper() if raw_status is not None else None

    if (
        callback_type in _FULL_BOX_EXCHANGE_CALLBACK_TYPES
        and status in _POST_EXCHANGE_RELATIONS_REQUIRED_STATUSES
        and not _has_post_exchange_relations(payload_json)
    ):
        return (
            HandlingStepStatus.RECONCILING,
            "POST_EXCHANGE_RELATIONS_MISSING",
            "满箱交换物理完成回调缺少 post_exchange_relations，已进入资源对账",
        )

    if callback_type in {"CTU_BIN_MOVE_COMPLETED", "WMS_BIN_MOVE_COMPLETED", "RCS_BIN_MOVE_COMPLETED"}:
        return HandlingStepStatus.SUCCEEDED, None, None
    if callback_type in {"CTU_BIN_MOVE_FAILED", "WMS_BIN_MOVE_FAILED", "RCS_BIN_MOVE_FAILED"}:
        return HandlingStepStatus.FAILED, _raw_error_code(payload_json), _raw_error_message(payload_json)
    if callback_type in {"CTU_BIN_MOVE_PROGRESS", "WMS_BIN_MOVE_PROGRESS", "RCS_BIN_MOVE_PROGRESS"}:
        return HandlingStepStatus.IN_PROGRESS, None, None

    return _resolve_step_status(status), _raw_error_code(payload_json), _raw_error_message(payload_json)


def _callback_target_queue(payload_json: Mapping[str, Any]) -> BinTransitQueue | None:
    raw_queue = (
        coerce_optional_str(payload_json.get("target_queue"))
        or coerce_optional_str(payload_json.get("current_queue"))
        or coerce_optional_str(payload_json.get("queue"))
    )
    if raw_queue is None:
        return None
    try:
        return BinTransitQueue(raw_queue)
    except ValueError:
        logger.warning(f"Ignoring unknown handling target queue from callback: target_queue={raw_queue}")
        return None


def _move_target_queue(move: Any) -> BinTransitQueue | None:
    raw_queue = coerce_optional_str(getattr(move, "target_code", None))
    if raw_queue is None:
        return None
    try:
        return BinTransitQueue(raw_queue)
    except ValueError:
        return None


def _resolve_step_status(status: str | None) -> HandlingStepStatus:
    if status in _SUCCESS_STATUSES:
        return HandlingStepStatus.SUCCEEDED
    if status in _PROGRESS_STATUSES:
        return HandlingStepStatus.IN_PROGRESS
    if status in _RECONCILING_STATUSES:
        return HandlingStepStatus.RECONCILING
    if status in _TIMEOUT_STATUSES:
        return HandlingStepStatus.TIMEOUT
    if status in _CANCELLED_STATUSES:
        return HandlingStepStatus.CANCELLED
    if status in _FAILED_STATUSES or (status is not None and status.startswith(("FAILED", "REJECTED"))):
        return HandlingStepStatus.FAILED
    return HandlingStepStatus.IN_PROGRESS


def _apply_operation_status_to_session(
    session: Any,
    *,
    operation_key: str,
    operation_status: str,
    error_code: str | None,
    error_message: str | None,
) -> None:
    context_json = dict(getattr(session, "context_json", None) or {})
    existing_operation = context_json.get("handling_operation")
    handling_operation = dict(existing_operation) if isinstance(existing_operation, dict) else {}
    handling_operation["operation_key"] = operation_key
    handling_operation["status"] = operation_status
    if error_code is not None:
        handling_operation["reason_code"] = error_code
    else:
        handling_operation.pop("reason_code", None)
    if error_message is not None:
        handling_operation["message"] = error_message
    else:
        handling_operation.pop("message", None)
    context_json["handling_operation"] = handling_operation

    if operation_status == HandlingOperationStatus.SUCCEEDED.value:
        context_json["waiting_handling_operation_key"] = None
        session.status = SessionStatus.RUNNING.value
        session.current_wait_type = None
        session.waiting_since = None
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_device_command_code = None
        session.failure_domain = None
        session.failure_code = None
        session.failure_message = None
        session.ended_at = None
    elif operation_status in {
        HandlingOperationStatus.PLANNED.value,
        HandlingOperationStatus.REQUESTED.value,
        HandlingOperationStatus.IN_PROGRESS.value,
    }:
        context_json["waiting_handling_operation_key"] = operation_key
    else:
        context_json["waiting_handling_operation_key"] = operation_key
        session.status = SessionStatus.MANUAL_HOLD.value
        session.current_wait_type = None
        session.waiting_since = None
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_device_command_code = None
        session.failure_domain = "EXTERNAL"
        session.failure_code = error_code or f"HANDLING_OPERATION_{operation_status}"
        session.failure_message = error_message or f"Handling operation {operation_key} derived {operation_status}"

    session.context_json = context_json


def _step_result_json(
    *,
    status: HandlingStepStatus,
    error_code: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"step_status": status.value}
    if error_code is not None:
        result["external_error_code"] = error_code
    if error_message is not None:
        result["external_error_message"] = error_message
    return result


def _is_terminal_step_status(value: Any) -> bool:
    return _step_status_value(value) in {
        HandlingStepStatus.SUCCEEDED.value,
        HandlingStepStatus.FAILED.value,
        HandlingStepStatus.TIMEOUT.value,
        HandlingStepStatus.CANCELLED.value,
        HandlingStepStatus.RECONCILING.value,
    }


def _step_status_value(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else None


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


def _has_post_exchange_relations(payload_json: Mapping[str, Any]) -> bool:
    relations = payload_json.get("post_exchange_relations")
    if isinstance(relations, Mapping):
        return bool(relations)
    if isinstance(relations, list):
        return bool(relations)
    return False


handling_operation_lifecycle_service = HandlingOperationLifecycleService()


__all__ = ["HandlingOperationLifecycleService", "handling_operation_lifecycle_service"]
