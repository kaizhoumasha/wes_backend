"""工作线调试、重放和人工操作 Service。"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import suppress
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from src.app.runtime.orchestration.effect_state_contract import transition_system_outbox
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
from src.core.base_service import BaseService
from src.core.task_queue_gateway import OutboxDispatchTarget, TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_str

if TYPE_CHECKING:
    from src.app.runtime.orchestration.models.operation import ResolveRuntimeReconciliationRequest

_SANDBOX_CALLBACK_OUTBOX_STATUSES = {
    SystemOutboxStatus.NEW.value,
    SystemOutboxStatus.DISPATCHING.value,
    SystemOutboxStatus.SENT.value,
}


class WorklineOperationService(BaseService[Any, Any]):
    """封装 sandbox / replay / manual 的状态前置条件。"""

    def __init__(
        self,
        *,
        inbox_repo: RuntimeInboxRepository | None = None,
        session_repo: WorklineSessionRepository | None = None,
        outbox_repo: SystemOutboxRepository | None = None,
        workline_repo: WorkLineRepository | None = None,
        runtime_hold_repo: RuntimeHoldRepository | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        super().__init__(inbox_repo or runtime_inbox_repository, enable_cache=False)
        self.inbox_repo = inbox_repo or runtime_inbox_repository
        self.runtime_inbox_service = RuntimeInboxService(repository=self.inbox_repo)
        self.session_repo = session_repo or workline_session_repository
        self.outbox_repo = outbox_repo or system_outbox_repository
        self.workline_repo = workline_repo or workline_repository
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
            workline_session_id=workline_session_id,
            now_ms=received_at_ms,
        )

    async def get_sandbox_pending(
        self,
        db: Any,
        *,
        limit: int = 50,
        workline_id: int | None = None,
    ) -> list[Any]:
        """查询 SIMULATION 模式下等待调试人员处理的 outbox。"""

        outboxes = await self.outbox_repo.get_sandbox_pending_messages(db, limit=limit, workline_id=workline_id)
        return [await self._project_sandbox_pending_outbox(db, outbox) for outbox in outboxes]

    async def get_sandbox_completed(
        self,
        db: Any,
        *,
        limit: int = 50,
        workline_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询 SIMULATION 模式下已完成的 outbox，按 Session 分组。"""

        groups = await self.outbox_repo.get_sandbox_completed_messages(
            db,
            inbox_query=runtime_inbox_query,
            limit=limit,
            workline_id=workline_id,
        )
        for group in groups:
            await self._enrich_sandbox_history_group(db, group)
        return groups

    async def _project_sandbox_pending_outbox(self, db: Any, outbox: Any) -> Any:
        """将沙箱 EXTERNAL_HTTP outbox 投影为前端动作状态。"""

        raw_payload = outbox.payload_json
        payload = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
        status = enum_str(outbox.status)
        is_current_action = await self._is_current_sandbox_external_outbox(db, outbox)
        runtime_hold = await self._find_projection_runtime_hold(db, outbox=outbox)
        runtime_hold_id = runtime_hold.id if runtime_hold is not None else None
        is_actionable = (
            is_current_action
            and enum_str(outbox.status) not in {SystemOutboxStatus.RETRY_WAIT.value, SystemOutboxStatus.FAILED.value}
            and status == SystemOutboxStatus.SENT.value
        )
        failure_summary = self._build_projection_failure_summary(outbox=outbox, hold=runtime_hold)

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
            is_current_action=is_current_action,
            is_actionable=is_actionable,
            runtime_hold_id=runtime_hold_id,
            failure_summary=failure_summary,
            history_group_key=self._history_group_key(outbox),
        )

    async def _find_projection_runtime_hold(self, db: Any, *, outbox: Any) -> Any | None:
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

        session_id = outbox.session_id if isinstance(outbox.session_id, int) else None
        outbox_id = outbox.id if isinstance(outbox.id, int) else None
        return await self.runtime_hold_repo.find_latest_for_projection(
            db,
            workline_id=workline_id,
            session_id=session_id,
            source_outbox_id=outbox_id,
        )

    def _build_projection_failure_summary(self, *, outbox: Any, hold: Any | None) -> dict[str, Any] | None:
        outbox_status = enum_str(outbox.status)
        failure_outbox_statuses = {
            SystemOutboxStatus.RETRY_WAIT.value,
            SystemOutboxStatus.FAILED.value,
            SystemOutboxStatus.CANCELLED.value,
        }
        if outbox_status not in failure_outbox_statuses:
            return None

        code = None
        if hold is not None:
            code = hold.source_reason
        if code is None:
            code = getattr(outbox, "last_error", None)
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
        outbox = SimpleNamespace(
            id=item.get("id"),
            session_id=session_id or item.get("session_id"),
            workline_id=item.get("workline_id"),
            status=item.get("status"),
            last_error=item.get("last_error"),
            blocked_by_runtime_hold_id=item.get("runtime_hold_id"),
        )
        hold = await self._find_projection_runtime_hold(db, outbox=outbox)
        runtime_hold_id = hold.id if hold is not None else item.get("runtime_hold_id")
        item["is_actionable"] = False
        item["runtime_hold_id"] = runtime_hold_id
        item["history_group_key"] = history_group_key or self._history_group_key(outbox)

        failure_summary = self._build_projection_failure_summary(outbox=outbox, hold=hold)
        existing_summary = item.get("failure_summary")
        if failure_summary is None and isinstance(existing_summary, dict):
            failure_summary = {**existing_summary, "runtime_hold_id": runtime_hold_id}
        item["failure_summary"] = failure_summary

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
        if enum_str(outbox.status) not in _SANDBOX_CALLBACK_OUTBOX_STATUSES:
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
