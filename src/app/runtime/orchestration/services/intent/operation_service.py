"""工作线调试、重放和人工操作 Service。"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import suppress
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from src.app.device.models.command import CommandResult, CommandStatus
from src.app.device.repositories import (
    DeviceCommandRepository,
    DeviceRepository,
    device_command_repository,
    device_repository,
)
from src.app.runtime.orchestration.effect_state_contract import transition_system_outbox
from src.app.runtime.orchestration.models.operation import (
    ResolveRuntimeReconciliationRequest,
    SandboxTemplatesResponse,
)
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldType
from src.app.runtime.orchestration.models.runtime_hold_api import ResolveRuntimeHoldRequest
from src.app.runtime.orchestration.models.session import RuntimeReconciliationResolution, SessionStatus
from src.app.runtime.orchestration.repositories import (
    runtime_hold_repository,
    runtime_inbox_repository,
    workline_session_repository,
)
from src.app.runtime.orchestration.repositories.runtime_hold_repository import RuntimeHoldRepository  # noqa: TC001
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository  # noqa: TC001
from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository  # noqa: TC001
from src.app.runtime.orchestration.repository_wiring import runtime_inbox_query, workline_repository
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxAuditPersistenceFailed,
    RuntimeInboxConflict,
    RuntimeInboxNotFound,
    RuntimeInboxReplayNotAllowed,
    RuntimeInboxService,
)
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.workline.models.workline import WorkLineRunMode
from src.app.workline.repositories.workline_repository import WorkLineRepository  # noqa: TC001
from src.app.workline.trace_context import TraceContext
from src.core.base_service import BaseService
from src.core.task_queue_gateway import OutboxDispatchTarget, TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_str

_OPEN_SESSION_STATUSES = {
    SessionStatus.NEW,
    SessionStatus.RUNNING,
    SessionStatus.WAITING_DEVICE_RESULT,
    SessionStatus.WAITING_EXTERNAL,
    SessionStatus.MANUAL_HOLD,
}

_RESULT_WAIT_SESSION_STATUS = SessionStatus.WAITING_DEVICE_RESULT

_ACK_WAIT_OUTBOX_STATUSES = {
    SystemOutboxStatus.NEW.value,
    SystemOutboxStatus.DISPATCHING.value,
    SystemOutboxStatus.SENT.value,
}

_TERMINAL_COMMAND_STATUSES = {
    CommandStatus.COMPLETED.value,
    CommandStatus.FAILED.value,
    CommandStatus.TIMEOUT.value,
    CommandStatus.CANCELLED.value,
}

_MANUAL_OPERATIONS = frozenset({"HOLD", "RESUME", "CANCEL"})


class WorklineOperationService(BaseService[Any, Any]):
    """封装 sandbox / replay / manual 的状态前置条件。"""

    def __init__(
        self,
        *,
        inbox_repo: RuntimeInboxRepository | None = None,
        session_repo: WorklineSessionRepository | None = None,
        outbox_repo: SystemOutboxRepository | None = None,
        workline_repo: WorkLineRepository | None = None,
        device_repo: DeviceRepository | None = None,
        command_repo: DeviceCommandRepository | None = None,
        runtime_hold_repo: RuntimeHoldRepository | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        super().__init__(inbox_repo or runtime_inbox_repository, enable_cache=False)
        self.inbox_repo = inbox_repo or runtime_inbox_repository
        self.runtime_inbox_service = RuntimeInboxService(repository=self.inbox_repo)
        self.session_repo = session_repo or workline_session_repository
        self.outbox_repo = outbox_repo or system_outbox_repository
        self.workline_repo = workline_repo or workline_repository
        self.device_repo = device_repo or device_repository
        self.command_repo = command_repo or device_command_repository
        self.runtime_hold_repo = runtime_hold_repo or runtime_hold_repository
        self._queue_gateway = queue_gateway

    def _enqueue_outbox_dispatch(self) -> None:
        self._queue_gateway.enqueue_outbox(targets=(OutboxDispatchTarget.SYSTEM,), limit=50)

    async def _accept_runtime_message(
        self,
        db: Any,
        *,
        kind: str,
        event_type: str,
        payload: dict[str, Any],
        source_event_id: str | None,
        workline_id: int | None = None,
        device_id: int | None = None,
        command_id: int | None = None,
        workline_session_id: int | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        received_at: Any | None = None,
    ) -> Any:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        received_at_ms = None
        if received_at is not None:
            received_at_ms = int(timezone.to_utc(received_at).timestamp() * 1000)
        return await self.runtime_inbox_service.accept_received(
            db,
            provider_code="MANUAL",
            event_type=event_type,
            source_event_id=source_event_id,
            payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            kind=kind,
            payload_json=payload,
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            workline_id=workline_id,
            device_id=device_id,
            command_id=command_id,
            workline_session_id=workline_session_id,
            now_ms=received_at_ms,
        )

    async def get_sandbox_pending(
        self,
        db: Any,
        *,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[Any]:
        """查询 SIMULATION 模式下等待调试人员处理的 outbox。"""

        outboxes = await self.outbox_repo.get_sandbox_pending_messages(
            db, limit=limit, workline_id=workline_id, device_id=device_id
        )
        return [await self._project_sandbox_pending_outbox(db, outbox) for outbox in outboxes]

    async def get_sandbox_completed(
        self,
        db: Any,
        *,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询 SIMULATION 模式下已完成的 outbox，按 Session 分组。"""

        groups = await self.outbox_repo.get_sandbox_completed_messages(
            db,
            inbox_query=runtime_inbox_query,
            limit=limit,
            workline_id=workline_id,
            device_id=device_id,
        )
        for group in groups:
            await self._enrich_sandbox_history_group(db, group)
        return groups

    async def _project_sandbox_pending_outbox(self, db: Any, outbox: Any) -> Any:
        """将沙箱 outbox 投影为前端动作状态。

        Sandbox 人工推进以 DeviceCommand 为操作对象。Outbox 即使尚未被后台
        dispatcher 标记为 SENT，也已经代表一条待 ACK 的设备命令。
        """

        raw_payload = outbox.payload_json
        payload = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
        status = enum_str(outbox.status)
        is_current_action = True
        command_status: str | None = None
        command: Any | None = None
        dispatch_type = enum_str(outbox.dispatch_type)
        if dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND.value:
            command_code = payload.get("command_code")
            if isinstance(command_code, str) and command_code:
                command = await self.command_repo.get_by_command_code(db, command_code)
                if command is not None:
                    command_status = enum_str(getattr(command, "status", None))
                    is_current_action = await self._is_current_sandbox_command_outbox(db, outbox, command)
                    if command_status in _TERMINAL_COMMAND_STATUSES:
                        status = command_status
                    elif command_status == CommandStatus.ACK_RECEIVED.value:
                        status = "ACKED"
                    elif enum_str(outbox.status) in _ACK_WAIT_OUTBOX_STATUSES:
                        status = SystemOutboxStatus.SENT.value
        elif dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP.value:
            is_current_action = await self._is_current_sandbox_external_outbox(db, outbox)

        runtime_hold = await self._find_projection_runtime_hold(db, outbox=outbox, command=command)
        runtime_hold_id = runtime_hold.id if runtime_hold is not None else None
        is_actionable = (
            is_current_action
            and enum_str(outbox.status) not in {SystemOutboxStatus.RETRY_WAIT.value, SystemOutboxStatus.FAILED.value}
            and status in {SystemOutboxStatus.SENT.value, "ACKED"}
        )
        failure_summary = self._build_projection_failure_summary(outbox=outbox, command=command, hold=runtime_hold)

        return SimpleNamespace(
            id=outbox.id,
            session_id=outbox.session_id,
            workline_id=outbox.workline_id,
            dispatch_key=outbox.dispatch_key,
            dispatch_type=enum_str(outbox.dispatch_type),
            target_type=enum_str(outbox.target_type),
            target_code=outbox.target_code,
            status=status,
            payload_json=payload,
            source_device=None,
            last_error=getattr(outbox, "last_error", None),
            command_status=command_status,
            is_current_action=is_current_action,
            is_actionable=is_actionable,
            runtime_hold_id=runtime_hold_id,
            failure_summary=failure_summary,
            history_group_key=self._history_group_key(outbox),
        )

    async def _find_projection_runtime_hold(self, db: Any, *, outbox: Any, command: Any | None) -> Any | None:
        direct_hold_id = getattr(outbox, "blocked_by_runtime_hold_id", None)
        if isinstance(direct_hold_id, int):
            if self.runtime_hold_repo is runtime_hold_repository and not hasattr(db, "execute"):
                return None
            return await self.runtime_hold_repo.get_by_id(db, direct_hold_id)

        if self.runtime_hold_repo is runtime_hold_repository and not hasattr(db, "execute"):
            return None

        workline_id = outbox.workline_id
        if not isinstance(workline_id, int):
            return None

        command_id = command.id if command is not None and isinstance(getattr(command, "id", None), int) else None
        device_id = (
            command.device_id if command is not None and isinstance(getattr(command, "device_id", None), int) else None
        )
        session_id = outbox.session_id if isinstance(outbox.session_id, int) else None
        outbox_id = outbox.id if isinstance(outbox.id, int) else None
        return await self.runtime_hold_repo.find_latest_for_projection(
            db,
            workline_id=workline_id,
            session_id=session_id,
            source_outbox_id=outbox_id,
            source_command_id=command_id,
            source_device_id=device_id,
        )

    def _build_projection_failure_summary(
        self, *, outbox: Any, command: Any | None, hold: Any | None
    ) -> dict[str, Any] | None:
        outbox_status = enum_str(outbox.status)
        command_status = enum_str(command.status) if command is not None else None
        failure_outbox_statuses = {
            SystemOutboxStatus.RETRY_WAIT.value,
            SystemOutboxStatus.FAILED.value,
            SystemOutboxStatus.CANCELLED.value,
        }
        if outbox_status not in failure_outbox_statuses and command_status not in _TERMINAL_COMMAND_STATUSES:
            return None

        error_detail = (
            command.error_detail
            if command is not None and isinstance(getattr(command, "error_detail", None), dict)
            else {}
        )
        code = None
        if hold is not None:
            code = hold.source_reason
        if code is None:
            detail_code = error_detail.get("code")
            code = detail_code if isinstance(detail_code, str) and detail_code else getattr(outbox, "last_error", None)
        message = error_detail.get("message")
        if not isinstance(message, str) or not message:
            message = getattr(outbox, "last_error", None) or code

        runtime_hold_id = hold.id if hold is not None else None
        return {
            "code": code,
            "message": message,
            "runtime_hold_id": runtime_hold_id,
        }

    def _history_group_key(self, outbox: Any) -> str:
        if isinstance(outbox.session_id, int):
            return f"session:{outbox.session_id}"
        return f"outbox:{outbox.id}"

    async def _enrich_sandbox_history_group(self, db: Any, group: dict[str, Any]) -> None:
        session = group.get("session")
        outbox_items = group.get("outbox_items")
        if not isinstance(session, dict) or not isinstance(outbox_items, list):
            return
        session_id = session.get("id")
        history_group_key = group.get("history_group_key")
        if not isinstance(history_group_key, str):
            history_group_key = f"session:{session_id}" if isinstance(session_id, int) else None
        if history_group_key is not None:
            group["history_group_key"] = history_group_key

        for item in outbox_items:
            if not isinstance(item, dict):
                continue
            await self._enrich_sandbox_history_item(
                db,
                item,
                session_id=session_id if isinstance(session_id, int) else None,
                history_group_key=history_group_key,
            )

    async def _enrich_sandbox_history_item(
        self,
        db: Any,
        item: dict[str, Any],
        *,
        session_id: int | None,
        history_group_key: str | None,
    ) -> None:
        payload = item.get("payload_json")
        command_code = payload.get("command_code") if isinstance(payload, dict) else None
        command = None
        command_status: str | None = None
        if isinstance(command_code, str) and command_code:
            command = await self.command_repo.get_by_command_code(db, command_code)
            if command is not None:
                command_status = enum_str(getattr(command, "status", None))
                item["command_status"] = command_status
                if command_status in _TERMINAL_COMMAND_STATUSES:
                    item["status"] = command_status
                elif command_status == CommandStatus.ACK_RECEIVED.value:
                    item["status"] = "ACKED"

        outbox = SimpleNamespace(
            id=item.get("id"),
            session_id=session_id or item.get("session_id"),
            workline_id=item.get("workline_id"),
            status=item.get("status"),
            last_error=item.get("last_error"),
            blocked_by_runtime_hold_id=item.get("runtime_hold_id"),
        )
        hold = await self._find_projection_runtime_hold(db, outbox=outbox, command=command)
        runtime_hold_id = hold.id if hold is not None else item.get("runtime_hold_id")
        if command_status == CommandStatus.COMPLETED.value:
            runtime_hold_id = None
        item["is_actionable"] = False
        item["runtime_hold_id"] = runtime_hold_id
        item["history_group_key"] = history_group_key or self._history_group_key(outbox)

        failure_summary = (
            None
            if command_status == CommandStatus.COMPLETED.value
            else self._build_projection_failure_summary(outbox=outbox, command=command, hold=hold)
        )
        existing_summary = item.get("failure_summary")
        if (
            command_status != CommandStatus.COMPLETED.value
            and failure_summary is None
            and isinstance(existing_summary, dict)
        ):
            failure_summary = {**existing_summary, "runtime_hold_id": runtime_hold_id}
        item["failure_summary"] = failure_summary

    async def _is_current_sandbox_command_outbox(self, db: Any, outbox: Any, command: Any) -> bool:
        """判断 outbox 是否是当前 session 正在等待人工推进的命令。"""

        if enum_str(getattr(command, "status", None)) in _TERMINAL_COMMAND_STATUSES:
            return False

        command_id = getattr(command, "id", None)
        if not isinstance(command_id, int):
            return True

        session_id = getattr(outbox, "session_id", None)
        if not isinstance(session_id, int):
            return True

        session = await self.session_repo.get_by_id(db, session_id)
        if session is None or enum_str(getattr(session, "status", None)) != SessionStatus.WAITING_DEVICE_RESULT.value:
            return True

        awaiting_command_code = getattr(session, "awaiting_device_command_code", None)
        command_code = getattr(command, "command_code", None)
        return not isinstance(awaiting_command_code, str) or awaiting_command_code == command_code

    async def _is_current_sandbox_external_outbox(self, db: Any, outbox: Any) -> bool:
        """判断 EXTERNAL_HTTP outbox 是否仍是当前 session 等待的外部回调。"""

        session_id = getattr(outbox, "session_id", None)
        if not isinstance(session_id, int):
            return True
        if self.session_repo is workline_session_repository and not hasattr(db, "execute"):
            return True

        session = await self.session_repo.get_by_id(db, session_id)
        if session is None:
            return True
        if enum_str(getattr(session, "status", None)) != SessionStatus.WAITING_EXTERNAL.value:
            return False

        current_wait_type = enum_str(getattr(session, "current_wait_type", None))
        if current_wait_type not in {"EXTERNAL_HTTP", "RACK_OPERATION"}:
            return False
        if current_wait_type != "RACK_OPERATION":
            return True

        return self._external_outbox_matches_waiting_rack_operation(outbox, session)

    def _external_outbox_matches_waiting_rack_operation(self, outbox: Any, session: Any) -> bool:
        """判断 rack operation outbox 是否对应当前等待的 operation。"""

        dispatch_key = getattr(outbox, "dispatch_key", None)
        context_json = getattr(session, "context_json", None)
        context = context_json if isinstance(context_json, dict) else {}
        rack_operation = context.get("rack_operation")
        rack_operation_data = rack_operation if isinstance(rack_operation, dict) else {}
        waiting_operation_key = context.get("waiting_rack_operation_key")

        dispatch_keys: set[str] = set()
        for field_name in ("task_dispatch_keys", "required_task_dispatch_keys"):
            raw_keys = rack_operation_data.get(field_name)
            if isinstance(raw_keys, list):
                dispatch_keys.update(key for key in raw_keys if isinstance(key, str) and key)
        if isinstance(dispatch_key, str) and dispatch_key in dispatch_keys:
            return True

        raw_payload = getattr(outbox, "payload_json", None)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        operation_key = payload.get("operation_key")
        if isinstance(waiting_operation_key, str) and waiting_operation_key:
            return operation_key == waiting_operation_key

        return not dispatch_keys

    def _resolve_sandbox_external_callback_type(
        self,
        *,
        callback_type: str | None,
        raw_payload: dict[str, Any],
        outbox_payload: dict[str, Any],
        session: Any,
        current_wait_type: str,
        dispatch_key: str,
    ) -> str:
        del current_wait_type
        resolved = self._first_non_empty_text(
            callback_type,
            raw_payload.get("callback_type"),
            outbox_payload.get("resume_callback_type"),
        )
        if resolved is not None:
            return resolved

        session_context = getattr(session, "context_json", None)
        rack_operation = session_context.get("rack_operation") if isinstance(session_context, dict) else None
        if isinstance(rack_operation, dict):
            resolved = self._first_non_empty_text(rack_operation.get("resume_callback_type"))
            if resolved is not None:
                return resolved

        raise ValueError(f"Outbox 缺少外部回调类型: dispatch_key={dispatch_key}")

    @staticmethod
    def _first_non_empty_text(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def replay_inbox(
        self,
        db: Any,
        *,
        inbox_id: int,
        request_id: str,
        actor: str,
        reason: str,
        auto_commit: bool = True,
    ) -> Any:
        """执行安全前置并委托 replay；auto_commit=False 时仅 stage，事务由外层负责。"""

        original = await self.inbox_repo.get_by_id(db, inbox_id)
        if original is None:
            raise RuntimeInboxNotFound(inbox_id=inbox_id)
        expected_ownership = (
            getattr(original, "workline_session_id", None),
            getattr(original, "workline_id", None),
        )
        session = None
        if original.workline_session_id is not None:
            # 与 reconciliation 写路径保持 Session→WorkLine 锁序，避免锁等待后继续使用旧快照。
            session = await self.session_repo.get_for_update(
                db,
                original.workline_session_id,
                populate_existing=True,
            )
            if session is None:
                raise RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_OWNERSHIP_UNAVAILABLE")

        source_workline_id = original.workline_id
        session_workline_id = getattr(session, "workline_id", None)
        if source_workline_id is None:
            if not isinstance(session_workline_id, int):
                raise RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_OWNERSHIP_UNAVAILABLE")
            source_workline_id = session_workline_id
        elif isinstance(session_workline_id, int) and session_workline_id != source_workline_id:
            raise RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_OWNERSHIP_UNAVAILABLE")

        workline = await self.workline_repo.get_for_update(db, source_workline_id, populate_existing=True)
        if workline is None:
            raise RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_NOT_FOUND")
        if not bool(getattr(workline, "is_active", False)):
            raise RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_INACTIVE")

        if session is not None:
            from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
                workline_runtime_reconciliation_service,
            )

            try:
                workline_runtime_reconciliation_service.assert_not_pending_reconciliation(session)
            except ValueError as exc:
                raise RuntimeInboxReplayNotAllowed(reason_code="SOURCE_RECONCILIATION_PENDING") from exc

        try:
            replay_result = await self.runtime_inbox_service.replay_from_dead_letter(
                db,
                source_inbox_id=inbox_id,
                request_id=request_id,
                actor=actor,
                reason=reason,
                expected_ownership=expected_ownership,
            )
        except RuntimeInboxAuditPersistenceFailed:
            # 自动事务必须 fail closed；外层事务模式只传播 typed error，由 Unit of Work 回滚。
            if auto_commit:
                rollback = getattr(db, "rollback", None)
                if rollback is not None:
                    await rollback()
            raise
        except RuntimeInboxConflict:
            # API 捕获冲突并返回正常响应，需在返回前提交同事务内的受限冲突审计。
            if auto_commit:
                await self._commit_replay_audit_boundary(
                    db,
                    audit_event_type="RUNTIME_INBOX_MANUAL_REPLAY_CONFLICT",
                )
            raise
        if auto_commit:
            await self._commit_replay_audit_boundary(
                db,
                audit_event_type="RUNTIME_INBOX_MANUAL_REPLAY",
            )
        return replay_result.replay_record

    async def _commit_replay_audit_boundary(self, db: Any, *, audit_event_type: str) -> None:
        """提交 replay 与审计的共同事务；commit 失败时回滚并保留原始原因。"""

        try:
            await self._commit_mutation(db)
        except Exception as commit_error:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                # commit error 是 API typed 503 的主因，rollback failure 不得覆盖它。
                with suppress(Exception):
                    await rollback()
            raise RuntimeInboxAuditPersistenceFailed(
                audit_event_type=audit_event_type,
                original_error=commit_error,
            ) from commit_error

    async def create_manual_operation(
        self,
        db: Any,
        *,
        session_id: int,
        operation: str,
        operator_id: str,
        reason: str,
        auto_commit: bool = True,
    ) -> Any:
        """创建人工操作 inbox。

        实际状态迁移和 timeline 由 runtime 消费该 inbox 后统一写入，避免 API 层提前写入
        与真实编排结果不一致的时间线。
        """

        session = await self.session_repo.get_by_id(db, session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id}")
        if session.status not in _OPEN_SESSION_STATUSES:
            raise ValueError(f"当前会话状态不允许人工操作: session_id={session_id}")
        from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
            workline_runtime_reconciliation_service,
        )

        workline_runtime_reconciliation_service.assert_not_pending_reconciliation(session)
        _ = await self._lock_active_workline_for_runtime_write(db, session.workline_id)

        normalized_operation = operation.upper()
        if normalized_operation not in _MANUAL_OPERATIONS:
            raise ValueError(f"不支持的人工操作: {operation}")
        event_type = f"MANUAL_{normalized_operation}"

        trace = TraceContext.from_runtime(session=session)
        payload: dict[str, Any] = {
            "message_type": "MANUAL_OPERATION",
            "operation": normalized_operation,
            "operator_id": operator_id,
            "reason": reason,
            "session_id": session_id,
        }
        inbox_result = await self._accept_runtime_message(
            db,
            kind="INTERNAL_EVENT",
            event_type=event_type,
            source_event_id=f"manual:{session_id}:{normalized_operation}:{uuid.uuid4().hex}",
            workline_session_id=session_id,
            workline_id=session.workline_id,
            trace_id=trace.trace_id,
            payload=payload,
        )

        if auto_commit:
            await self._commit_mutation(db)
        return inbox_result.record

    async def submit_sandbox_event(
        self,
        db: Any,
        *,
        workline_id: int,
        device_id: int,
        event_type: str,
        trace_id: str | None = None,
        session_id: int | None = None,
        payload: dict[str, Any] | None = None,
        timestamp: Any | None = None,
        auto_commit: bool = True,
    ) -> Any:
        """沙箱模式主动发送 Event。

        仅允许 SIMULATION 工作线，写入 inbox 后触发编排处理。
        """

        _ = await self._lock_simulation_workline_for_runtime_write(db, workline_id)

        event_trace_id = trace_id or f"sandbox:{uuid.uuid4().hex}"
        event_payload = dict(payload or {})
        event_payload["event_type"] = event_type
        event_payload["sandbox_mode"] = True

        sandbox_event_id = f"sandbox:{event_type}:{uuid.uuid4().hex}"
        inbox_result = await self._accept_runtime_message(
            db,
            kind="DEVICE_EVENT",
            event_type=event_type,
            source_event_id=sandbox_event_id,
            workline_id=workline_id,
            device_id=device_id,
            workline_session_id=session_id,
            trace_id=event_trace_id,
            event_id=sandbox_event_id,
            received_at=timestamp,
            payload=event_payload,
        )
        if auto_commit:
            await self._commit_mutation(db)
        return inbox_result.record

    async def submit_sandbox_external_callback(
        self,
        db: Any,
        *,
        dispatch_key: str,
        callback_type: str | None = None,
        payload: dict[str, Any] | None = None,
        source_system: str = "WMS",
        source_event_id: str | None = None,
        source_version: str = "1",
        request_id: str | None = None,
        occurred_at: datetime | None = None,
        timestamp: datetime | None = None,
        signature: str = "sandbox",
        auto_commit: bool = True,
    ) -> Any:
        """沙箱模式模拟 External HTTP 回调。

        只在 SIMULATION 工作线开放，把调试人员输入转成运行时统一消费的
        EXTERNAL_HTTP inbox，避免前端直接依赖 callback ingress 的验签/来源契约。
        """

        outbox = await self.outbox_repo.get_by_dispatch_key(db, dispatch_key)
        if outbox is None:
            raise ValueError(f"Outbox 不存在: {dispatch_key}")

        if outbox.workline_id is None:
            raise ValueError(f"Outbox 未关联工作线: dispatch_key={dispatch_key}")
        _ = await self._lock_simulation_workline_for_runtime_write(db, outbox.workline_id)

        if enum_str(outbox.dispatch_type) != SystemOutboxDispatchType.EXTERNAL_HTTP.value:
            raise ValueError(f"仅允许 EXTERNAL_HTTP Outbox 模拟外部回调: dispatch_key={dispatch_key}")
        if enum_str(outbox.status) not in _ACK_WAIT_OUTBOX_STATUSES:
            raise ValueError(
                f"当前 Outbox 状态不允许模拟外部回调: dispatch_key={dispatch_key}, status={enum_str(outbox.status)}"
            )
        if outbox.session_id is None:
            raise ValueError(f"Outbox 未关联会话: dispatch_key={dispatch_key}")

        session = await self.session_repo.get_by_id(db, outbox.session_id)
        if session is None:
            raise ValueError(f"会话不存在: {outbox.session_id}")
        current_wait_type = enum_str(getattr(session, "current_wait_type", None))
        if session.status != SessionStatus.WAITING_EXTERNAL or current_wait_type not in {
            "EXTERNAL_HTTP",
            "RACK_OPERATION",
        }:
            raise ValueError(
                f"当前会话状态不允许模拟外部回调: session_id={session.id}, "
                f"status={enum_str(session.status)}, wait_type={getattr(session, 'current_wait_type', None)}"
            )

        raw_outbox_payload = outbox.payload_json
        outbox_payload = cast("dict[str, Any]", raw_outbox_payload) if isinstance(raw_outbox_payload, dict) else {}
        raw_payload = dict(payload or {})
        resolved_callback_type = self._resolve_sandbox_external_callback_type(
            callback_type=callback_type,
            raw_payload=raw_payload,
            outbox_payload=outbox_payload,
            session=session,
            current_wait_type=current_wait_type,
            dispatch_key=dispatch_key,
        )

        if source_system not in {"WMS", "RCS"}:
            raise ValueError(f"外部来源系统不支持: source_system={source_system}")
        from src.app.callback.contracts.external_callbacks import validate_external_callback_type

        resolved_callback_type = validate_external_callback_type(
            raw_payload,
            declared_callback_type=resolved_callback_type,
            declared_source_system=source_system,
        )

        trace_id = getattr(session, "trace_id", None) or outbox_payload.get("trace_id") or raw_payload.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError(f"会话缺少 trace_id: session_id={session.id}")
        trace_id = trace_id.strip()

        callback_received_at = timestamp if isinstance(timestamp, datetime) else timezone.now_for_db()
        # 接收时间用于 RuntimeInbox 排序；canonical payload 只使用显式时间或稳定业务时间，
        # 避免同一 dispatch_key 重试因当前时间变化而产生 payload_hash 冲突。
        stable_message_at = next(
            (
                value
                for value in (
                    timestamp,
                    occurred_at,
                    getattr(outbox, "created_at", None),
                    getattr(session, "created_at", None),
                )
                if isinstance(value, datetime)
            ),
            timezone.to_utc(0),
        )
        event_occurred_at = occurred_at if isinstance(occurred_at, datetime) else stable_message_at
        resolved_source_event_id = source_event_id or raw_payload.get("source_event_id")
        if not isinstance(resolved_source_event_id, str) or not resolved_source_event_id.strip():
            dispatch_event_hash = uuid.uuid5(uuid.NAMESPACE_URL, dispatch_key).hex
            resolved_source_event_id = f"sandbox:{resolved_callback_type}:{dispatch_event_hash}"
        resolved_source_event_id = resolved_source_event_id.strip()
        resolved_request_id = request_id or raw_payload.get("request_id")
        if not isinstance(resolved_request_id, str) or not resolved_request_id.strip():
            resolved_request_id = f"sandbox:external:{resolved_source_event_id}"
        resolved_request_id = resolved_request_id.strip()

        inbox_payload = {
            **raw_payload,
            "message_type": "EXTERNAL_HTTP",
            "callback_type": resolved_callback_type,
            "trace_id": trace_id,
            "dispatch_key": dispatch_key,
            "source_system": source_system,
            "source_event_id": resolved_source_event_id,
            "source_version": source_version,
            "occurred_at": event_occurred_at.isoformat(),
            "request_id": resolved_request_id,
            "timestamp": stable_message_at.isoformat(),
            "signature": signature,
            "sandbox_mode": True,
        }
        inbox_result = await self._accept_runtime_message(
            db,
            kind="EXTERNAL_HTTP",
            event_type=resolved_callback_type,
            source_event_id=resolved_source_event_id,
            workline_id=session.workline_id,
            workline_session_id=session.id,
            trace_id=trace_id,
            event_id=resolved_source_event_id,
            received_at=callback_received_at,
            payload=inbox_payload,
        )

        _transition_sandbox_outbox_to_sent(outbox)
        if getattr(outbox, "sent_at", None) is None:
            outbox.sent_at = callback_received_at
        if hasattr(outbox, "next_retry_at"):
            outbox.next_retry_at = None
        if hasattr(outbox, "last_error"):
            outbox.last_error = None

        if auto_commit:
            await self._commit_mutation(db)
        return inbox_result.record

    async def submit_sandbox_ack(
        self,
        db: Any,
        *,
        dispatch_key: str,
        auto_commit: bool = True,
    ) -> Any:
        """沙箱模式模拟 Command ACK。

        ACK 事实写 DeviceCommand.status/ack_received_at；如果后台 dispatcher 尚未运行，
        同步把 Sandbox outbox 收敛到 SENT，避免人工调试依赖派发轮询。
        """

        outbox = await self.outbox_repo.get_by_dispatch_key(db, dispatch_key)
        if outbox is None:
            raise ValueError(f"Outbox 不存在: {dispatch_key}")

        if outbox.workline_id is None:
            raise ValueError(f"Outbox 未关联工作线: dispatch_key={dispatch_key}")
        _ = await self._lock_simulation_workline_for_runtime_write(db, outbox.workline_id)
        await self._validate_ack_target(db, outbox)

        raw_payload = outbox.payload_json
        outbox_payload = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
        command_code = outbox_payload.get("command_code")
        if not isinstance(command_code, str) or not command_code:
            raise ValueError(f"Outbox 缺少 command_code: dispatch_key={dispatch_key}")
        command = await self.command_repo.get_by_command_code(db, command_code)
        if command is None or command.id is None:
            raise ValueError(f"Command 不存在: {command_code}")
        command_status = enum_str(getattr(command, "status", None))
        if command_status in _TERMINAL_COMMAND_STATUSES:
            raise ValueError(f"Command 已终态，不能模拟 ACK: {command_code}")
        if command_status == CommandStatus.ACK_RECEIVED.value or command.ack_received_at is not None:
            raise ValueError(f"Command 已 ACK，不能重复模拟 ACK: {command_code}")

        ack_received_at = timezone.now_for_db()
        _transition_sandbox_outbox_to_sent(outbox)
        if outbox.sent_at is None:
            outbox.sent_at = ack_received_at
        outbox.next_retry_at = None
        outbox.last_error = None
        command.status = CommandStatus.ACK_RECEIVED
        command.sent_at = command.sent_at or ack_received_at
        command.ack_received_at = ack_received_at
        command.ack_code = 200
        command.ack_message = "SANDBOX_ACK"

        from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
            workline_runtime_reconciliation_service,
        )

        _ = await workline_runtime_reconciliation_service.activate_execution_deadline_after_ack(
            db,
            command_id=command.id,
            ack_received_at=ack_received_at,
        )

        from src.app.sys.services.event_stream_service import defer_command_status_changed_event

        defer_command_status_changed_event(
            db,
            command=command,
            action="acked",
            workline_id=getattr(command, "workline_id", None),
            device_id=getattr(command, "device_id", None),
            session_id=getattr(outbox, "session_id", None),
        )

        if auto_commit:
            await self._commit_mutation(db)
        return outbox

    async def resolve_runtime_reconciliation(
        self,
        db: Any,
        *,
        session_id: int,
        request: ResolveRuntimeReconciliationRequest,
        operator_id: int,
        auto_commit: bool = True,
    ) -> dict[str, Any]:
        """解除 runtime reconciliation 隔离并释放对应 parked outbox。"""

        from src.app.runtime.orchestration.repositories.runtime_hold_repository import runtime_hold_repository
        from src.app.runtime.orchestration.services.hold.runtime_hold_release_service import (
            runtime_hold_release_service,
        )

        session = await self.session_repo.get_by_id(db, session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id}")

        active_holds = await runtime_hold_repository.get_active_blocking_by_workline(db, session.workline_id)
        hold = next(
            (
                item
                for item in active_holds
                if item.session_id == session_id and item.hold_type == RuntimeHoldType.RUNTIME_RECONCILIATION
            ),
            None,
        )
        if hold is None:
            raise ValueError(f"未找到 active RuntimeHold: session_id={session_id}")

        release_request = ResolveRuntimeHoldRequest(
            resolution=RuntimeReconciliationResolution(request.resolution).value,
            checks=request.checks,
            operator_note=request.operator_note,
            material_disposition="CONTINUE",
            result_payload=request.result_payload,
            hold_version=hold.version,
            latest_evidence_hash=runtime_hold_release_service.build_latest_evidence_hash(hold, session=session),
        )
        result = await runtime_hold_release_service.resolve_hold(db, cast("int", hold.id), release_request, operator_id)
        if auto_commit:
            await self._commit_mutation(db)
        return result

    async def submit_sandbox_result(
        self,
        db: Any,
        *,
        command_code: str,
        device_code: str,
        result: str,
        payload: dict[str, Any] | None = None,
        error_detail: str | None = None,
        timestamp: Any | None = None,
        auto_commit: bool = True,
    ) -> Any:
        """沙箱模式模拟 Command Result。

        查找对应的 Command 和工作线，验证 SIMULATION 模式，
        写入 inbox (COMMAND_RESULT) 触发编排处理。
        """

        device = await self.device_repo.get_by_device_code(db, device_code)
        if device is None:
            raise ValueError(f"设备不存在: {device_code}")

        command = await self.command_repo.get_by_command_code(db, command_code)
        if command is None:
            raise ValueError(f"Command 不存在: {command_code}")

        workline_id = command.workline_id
        if workline_id is None:
            raise ValueError(f"Command 未关联工作线: {command_code}")

        _ = await self._lock_simulation_workline_for_runtime_write(db, workline_id)

        if command.id is None:
            raise ValueError(f"Command 缺少主键: {command_code}")
        device_id = device.id
        if device_id is None:
            raise ValueError(f"设备缺少主键: {device_code}")
        if device_id != command.device_id:
            raise ValueError(
                f"Result 设备与 Command 不匹配: command_code={command_code}, "
                f"expected_device_id={command.device_id}, actual_device_id={device_id}"
            )

        session = await self._load_session_waiting_for_command(
            db,
            command,
            command_code,
            action_label="提交 Result",
        )

        command_task_type = enum_str(command.task_type)
        sandbox_completed_at = timestamp if isinstance(timestamp, datetime) else timezone.now_for_db()
        result_payload: dict[str, Any] = {
            "command_code": command.command_code,
            "device_code": device.device_code,
            "task_type": command_task_type,
            "result": result,
            "finish_time": timezone.to_utc_timestamp(sandbox_completed_at) * 1000,
            "sandbox_mode": True,
            "data": dict(payload or {}),
        }
        if error_detail:
            result_payload["error_detail"] = {"error_message": error_detail}

        sandbox_success = enum_str(result) == CommandResult.SUCCESS.value
        command.status = CommandStatus.COMPLETED if sandbox_success else CommandStatus.FAILED
        command.result = CommandResult.SUCCESS if sandbox_success else CommandResult.FAILED
        command.completed_at = sandbox_completed_at
        command.result_data = dict(payload or {})
        command.error_detail = {"error_message": error_detail} if error_detail else None

        from src.app.device.services import device_service

        updated_device = await device_service.mark_command_finished(
            db,
            device_id=device_id,
            command_id=command.id,
            success=sandbox_success,
            error_code="SANDBOX_RESULT_FAILED" if not sandbox_success else None,
            auto_commit=False,
        )
        device_status = getattr(getattr(updated_device, "device_status", None), "value", None) or getattr(
            updated_device,
            "device_status",
            None,
        )
        if (
            updated_device is not None
            and device_status == "IDLE"
            and getattr(updated_device, "current_command_id", None) is None
        ):
            # 本地设备投影仅用于诊断；blocked outbox 放行必须由下一轮 ECS admission probe 决定。
            pass

        result_event_id = f"sandbox:result:{command_code}:{uuid.uuid4().hex}"
        inbox_result = await self._accept_runtime_message(
            db,
            kind="COMMAND_RESULT",
            event_type="COMMAND_RESULT",
            source_event_id=result_event_id,
            workline_id=workline_id,
            device_id=device_id,
            command_id=command.id,
            workline_session_id=session.id,
            trace_id=command.trace_id,
            event_id=result_event_id,
            received_at=timestamp,
            payload=result_payload,
        )

        from src.app.sys.services.event_stream_service import defer_command_status_changed_event

        defer_command_status_changed_event(
            db,
            command=command,
            action="updated",
            workline_id=workline_id,
            device_id=device_id,
            session_id=session.id,
        )

        if auto_commit:
            await self._commit_mutation(db)
        return inbox_result.record

    def _require_simulation_workline(self, workline: Any) -> None:
        if workline.run_mode != WorkLineRunMode.SIMULATION:
            raise ValueError(
                f"仅允许 SIMULATION 工作线使用沙箱功能: workline_id={workline.id}, run_mode={workline.run_mode}"
            )

    async def _lock_active_workline_for_runtime_write(self, db: Any, workline_id: int) -> Any:
        workline = await self.workline_repo.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: {workline_id}")
        if not bool(getattr(workline, "is_active", False)):
            raise ValueError(f"工作线未启用，不能写入运行数据: workline_id={workline_id}")
        return workline

    async def _lock_simulation_workline_for_runtime_write(self, db: Any, workline_id: int) -> Any:
        workline = await self._lock_active_workline_for_runtime_write(db, workline_id)
        self._require_simulation_workline(workline)
        return workline

    async def _load_session_waiting_for_command(
        self,
        db: Any,
        command: Any,
        command_code: str,
        *,
        action_label: str,
    ) -> Any:
        session = await self.session_repo.get_open_session_by_awaiting_device_command_code(db, command.command_code)
        if session is None:
            raise ValueError(f"未找到等待 Command 的会话: {command_code}")
        session_id = session.id

        if session.status != _RESULT_WAIT_SESSION_STATUS:
            raise ValueError(
                f"当前会话状态不允许{action_label}: session_id={session_id}, status={enum_str(session.status)}"
            )
        if session.awaiting_device_command_code != command.command_code:
            raise ValueError(
                f"当前会话等待的 Command 不匹配: session_id={session_id}, "
                f"awaiting_device_command_code={session.awaiting_device_command_code}, command_code={command.command_code}"
            )

        return session

    async def _validate_ack_target(self, db: Any, outbox: Any) -> None:
        if enum_str(outbox.dispatch_type) != SystemOutboxDispatchType.DEVICE_COMMAND.value:
            raise ValueError(f"仅允许 ACK 设备指令 Outbox: dispatch_key={outbox.dispatch_key}")
        if enum_str(outbox.status) not in _ACK_WAIT_OUTBOX_STATUSES:
            raise ValueError(
                f"当前 Outbox 状态不允许 ACK: dispatch_key={outbox.dispatch_key}, status={enum_str(outbox.status)}"
            )

        raw_payload = outbox.payload_json
        payload = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
        command_code = payload.get("command_code")
        if not isinstance(command_code, str) or not command_code:
            raise ValueError(f"Outbox 缺少 command_code: dispatch_key={outbox.dispatch_key}")

        command = await self.command_repo.get_by_command_code(db, command_code)
        if command is None:
            raise ValueError(f"Command 不存在: {command_code}")

        session = await self._load_session_waiting_for_command(
            db,
            command,
            command_code,
            action_label="模拟 ACK",
        )
        if outbox.session_id != session.id:
            raise ValueError(
                f"Outbox 会话与 Command 会话不匹配: dispatch_key={outbox.dispatch_key}, "
                f"outbox_session_id={outbox.session_id}, command_session_id={session.id}"
            )

    async def get_sandbox_templates(
        self,
        db: Any,
        *,
        workline_id: int,
        device_id: int | None = None,
    ) -> SandboxTemplatesResponse:
        """当前核心不安装业务插件，因此不存在可执行的业务沙箱模板。"""

        workline = await self.workline_repo.get_by_id(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: {workline_id}")

        del device_id
        return SandboxTemplatesResponse()


def _transition_sandbox_outbox_to_sent(outbox: Any) -> None:
    """沙箱同步回调仍遵循真实 transport 的合法状态边。"""

    if enum_str(outbox.status) == SystemOutboxStatus.SENT.value:
        return
    if enum_str(outbox.status) == SystemOutboxStatus.NEW.value:
        transition_system_outbox(outbox, SystemOutboxStatus.DISPATCHING)
    transition_system_outbox(outbox, SystemOutboxStatus.SENT)
    # owner token 作为历史审计证据保留；离开 DISPATCHING 后只清理活跃租约期限。
    outbox.lease_expires_at = None


workline_operation_service = WorklineOperationService()


__all__ = ["WorklineOperationService", "workline_operation_service"]
