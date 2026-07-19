"""RuntimeInboxWriteBackService (Task 5 三阶段 Processor 拆分).

WRITE 锁回调: stale session snapshot guard + 业务 effects + 重复/迟到检测
+ fence terminal update.

本服务只承担 write-back 职责，不关心 SCAN/ESTOP/TIMER 前置 gate。

关键约束:
- 必须在锁内执行 (OrchestratorService 已经获取 session lock).
- 必须用 lease_token fencing 写终态 (避免旧 owner 复活), 作用于
  RuntimeInbox 表 (RuntimeInboxService.mark_processed / mark_failed).
- session snapshot guard: 锁内 session state 若与锁前 snapshot 不一致,
  抛 RuntimeError, 由 Composition 层回滚并 mark_failed.
- RESOURCE_WAIT 走 mark_failed(retryable=True), RuntimeInboxService
  内部按 attempt_count 自计算指数退避, 不消耗 attempt.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.app.device.repositories import device_repository as default_device_repository
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.effect_result import (
    RuntimeIntentEffectResult,
    WriteBackDisposition,
)
from src.app.runtime.orchestration.material_fact_version import material_unit_fact_version
from src.app.runtime.orchestration.repositories.plugin_attempt_repository import (
    plugin_attempt_repository as default_plugin_attempt_repository,
)
from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
    runtime_intent_log_repository as default_runtime_intent_log_repository,
)
from src.app.runtime.orchestration.repository_wiring import workline_repository as default_workline_repository
from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.runtime.orchestration.services.idempotency_guard import (
    ClaimResult,
)
from src.app.runtime.orchestration.services.idempotency_guard import (
    idempotency_guard as default_idempotency_guard,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxService,
    runtime_inbox_service,
)
from src.app.runtime.orchestration.services.session.session_resolver import (
    reapply_pending_session_ingress_metadata,
)
from src.app.runtime.workline_plugins.attempt_coordinator import (
    AttemptSnapshot,
    AttemptWriteSet,
    PluginWriteSetLimits,
    WriteDisposition,
    bound_attempt_write_set,
)
from src.app.workline.services.plugin_binding_service import (
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
from src.app.workline.services.write_back_service import orchestrator_write_back_service
from src.app.workline.utils import payload_dict
from src.core.conf import settings
from src.utils.timezone import timezone
from src.utils.value_normalization import (
    canonical_event_type,
    optional_int,
    string_value,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import PluginAttemptRepository


@dataclass
class WriteBackState:
    """Write-back 阶段 state, 在 write_callback 闭包内外共享."""

    write_effects_applied: bool = False
    enqueue_outbox_dispatch: bool = False
    disposition: WriteBackDisposition | None = None


class RuntimeInboxLeaseLostError(RuntimeError):
    """RuntimeInbox 终态 fencing 更新未命中，当前 processor lease 已失效。"""


def _require_fenced_update(updated: bool, *, action: str, inbox_id: int) -> None:
    """拒绝继续提交已失去 RuntimeInbox lease 的事务。"""
    if not updated:
        raise RuntimeInboxLeaseLostError(f"RuntimeInbox {inbox_id} lease lost before {action}")


def _session_write_snapshot(session: Any) -> tuple[Any, Any]:
    """提取写入前的最小 session 快照, 用于锁内防止 stale write."""
    return (
        getattr(session, "status", None),
        getattr(session, "awaiting_device_command_code", None),
    )


def _authoritative_snapshot_matches(locked: Any, expected: AttemptSnapshot) -> bool:
    """锁后比较 lease、乐观版本与全部 immutable pin。"""

    inbox = locked.inbox
    session = locked.session
    material_unit = getattr(locked, "material_unit", None)
    session_definition_identity = getattr(session, "plugin_identity", None)
    if session_definition_identity is None:
        plugin_key = getattr(session, "plugin_key", None)
        contract_version = getattr(session, "contract_version", None)
        if isinstance(plugin_key, str) and plugin_key and isinstance(contract_version, str) and contract_version:
            from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX

            definition = WORKLINE_PLUGIN_INDEX.get((plugin_key, contract_version))
            session_definition_identity = (
                definition.identity if definition is not None else f"{plugin_key}@{contract_version}"
            )
    return (
        getattr(inbox, "processor_token", None) == expected.processor_token
        and getattr(session, "version", None) == expected.session_version
        and getattr(session, "plugin_state_version", None) == expected.plugin_state_version
        and (expected.session_status is None or _session_status_value(session) == expected.session_status)
        and (
            expected.material_unit_id is None
            or (
                getattr(session, "current_material_unit_id", None) == expected.material_unit_id
                and getattr(material_unit, "id", None) == expected.material_unit_id
                and material_unit_fact_version(material_unit) == expected.material_unit_version
            )
        )
        and (expected.definition_identity is None or session_definition_identity == expected.definition_identity)
        and (expected.binding_id is None or getattr(session, "plugin_binding_id", None) == expected.binding_id)
        and (
            expected.binding_version is None
            or getattr(session, "plugin_binding_version", None) == expected.binding_version
        )
        and (
            expected.plugin_config_hash is None
            or getattr(session, "plugin_config_hash", None) == expected.plugin_config_hash
        )
        and (expected.index_digest is None or getattr(session, "plugin_index_digest", None) == expected.index_digest)
    )


def _device_fact_versions(devices_by_role: dict[str, list[Any]]) -> tuple[tuple[str, int, int], ...]:
    """把 Stage 3 重载设备归一为可与 Stage 1 immutable snapshot 比较的事实版本。"""

    facts: list[tuple[str, int, int]] = []
    for role, devices in devices_by_role.items():
        for device in devices:
            device_id = getattr(device, "id", None)
            version = getattr(device, "version", None)
            if (
                isinstance(role, str)
                and role
                and isinstance(device_id, int)
                and not isinstance(device_id, bool)
                and isinstance(version, int)
                and not isinstance(version, bool)
                and version >= 0
            ):
                facts.append((role, device_id, version))
    return tuple(sorted(facts))


def _session_status_value(session: Any) -> str | None:
    value = getattr(getattr(session, "status", None), "value", getattr(session, "status", None))
    return value if isinstance(value, str) and value else None


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


def _command_status_value(command: Any) -> str | None:
    value = getattr(getattr(command, "status", None), "value", getattr(command, "status", None))
    return value if isinstance(value, str) else None


def _is_current_wait_command_result(*, session: Any, command: Any, payload: dict[str, Any]) -> bool:
    """判断 COMMAND_RESULT 是否仍对应 session 当前声明的等待锚点."""
    _ = payload
    command_code = getattr(command, "command_code", None)
    awaiting_command_code = getattr(session, "awaiting_device_command_code", None)
    return isinstance(command_code, str) and command_code == awaiting_command_code


def _is_late_or_duplicate_command_result_for_session(
    *,
    inbox: Any,
    payload: dict[str, Any],
    session: Any | None,
    command: Any | None,
) -> bool:
    """识别已消费过或迟到的 COMMAND_RESULT。"""
    kind = _kind_value(inbox)
    declared_route = payload.get("logical_route") or payload.get("callback_type")
    event_type = getattr(getattr(inbox, "event_type", None), "value", getattr(inbox, "event_type", None))
    callback_route = declared_route or event_type
    is_pick_result_callback = kind in {"INTERNAL_EVENT", "EXTERNAL_HTTP"} and callback_route == "PICK_AND_PUT_RESULT"
    if kind != "COMMAND_RESULT" and not is_pick_result_callback:
        return False
    if session is None:
        return False
    awaiting_command_code = getattr(session, "awaiting_device_command_code", None)
    callback_command_code = payload.get("command_code") or getattr(command, "command_code", None)
    if not isinstance(callback_command_code, str) or not callback_command_code:
        return True
    if not isinstance(awaiting_command_code, str) or callback_command_code != awaiting_command_code:
        return True
    if command is None:
        return True
    terminal_command_statuses = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
    command_status = _command_status_value(command)
    if command_status not in terminal_command_statuses:
        return False
    terminal_session_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
    if _session_status_value(session) in terminal_session_statuses:
        return True
    return not _is_current_wait_command_result(session=session, command=command, payload=payload)


def _result_requires_outbox_dispatch(result: "OrchestratorResult") -> bool:  # noqa: UP037
    """判断 OrchestratorResult 是否需要 outbox dispatch."""
    for intent in result.intents or []:
        if intent.kind in {
            RuntimeIntentKind.COMMAND,
            RuntimeIntentKind.EXTERNAL_REQUEST,
            RuntimeIntentKind.RACK_OPERATION_REQUEST,
        }:
            return True
        if intent.kind == RuntimeIntentKind.CONTINUE_NEXT and intent.action:
            return True
    return False


async def _record_late_command_result_archive_timeline(
    db: Any,
    *,
    session: Any,
    workline: Any,
    inbox: Any,
    command: Any,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """为迟到/重复 COMMAND_RESULT 归档留一条显式 timeline 证据."""
    from src.app.runtime.orchestration.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
        WorklineTimeline,
    )
    from src.app.runtime.orchestration.services.trace.timeline_sequence_service import (
        add_timeline_with_sequence,
    )
    from src.utils.timezone import timezone
    from src.utils.value_normalization import optional_str, resolve_entity_id

    session_id = resolve_entity_id(session)
    workline_id = resolve_entity_id(workline) or optional_int(getattr(session, "workline_id", None))
    if session_id is None or workline_id is None:
        return
    timeline = WorklineTimeline(
        session_id=session_id,
        workline_id=workline_id,
        trace_id=optional_str(getattr(inbox, "trace_id", None)) or optional_str(getattr(session, "trace_id", None)),
        seq_no=0,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_PROCESSED,
        actor_type=TimelineActorType.ORCHESTRATOR,
        actor_code="runtime-inbox-writeback",
        status=TimelineStatus.SUCCESS,
        message="LATE_COMMAND_RESULT_ARCHIVED",
        payload_json={
            "reason": reason,
            "command_code": getattr(command, "command_code", None),
            "command_status": _command_status_value(command),
            "inbox_id": resolve_entity_id(inbox),
            "session_status": _session_status_value(session),
            "awaiting_device_command_code": getattr(session, "awaiting_device_command_code", None),
            "current_wait_type": string_value(getattr(session, "current_wait_type", None)),
        },
        related_inbox_id=resolve_entity_id(inbox),
        related_command_id=resolve_entity_id(command),
    )
    try:
        _ = await add_timeline_with_sequence(db, timeline)
    except Exception as exc:
        logger.warning(f"迟到命令结果归档 timeline 记录失败: {exc}")


async def _record_duplicate_entry_archive_timeline(
    db: Any,
    *,
    session: Any,
    workline: Any,
    inbox: Any,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """为重复入口事件归档显式 timeline 证据。"""
    from src.app.runtime.orchestration.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
        WorklineTimeline,
    )
    from src.app.runtime.orchestration.services.trace.timeline_sequence_service import (
        add_timeline_with_sequence,
    )
    from src.utils.timezone import timezone
    from src.utils.value_normalization import optional_str, resolve_entity_id

    session_id = resolve_entity_id(session)
    workline_id = resolve_entity_id(workline) or optional_int(getattr(session, "workline_id", None))
    if session_id is None or workline_id is None:
        return
    timeline = WorklineTimeline(
        session_id=session_id,
        workline_id=workline_id,
        trace_id=optional_str(getattr(inbox, "trace_id", None)) or optional_str(getattr(session, "trace_id", None)),
        seq_no=0,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_PROCESSED,
        actor_type=TimelineActorType.ORCHESTRATOR,
        actor_code="runtime-inbox-writeback",
        status=TimelineStatus.SUCCESS,
        message="DUPLICATE_ENTRY_ARCHIVED",
        payload_json={
            "reason": reason,
            "event_type": canonical_event_type(payload),
            "inbox_id": resolve_entity_id(inbox),
            "session_status": _session_status_value(session),
            "awaiting_device_command_code": getattr(session, "awaiting_device_command_code", None),
        },
        related_inbox_id=resolve_entity_id(inbox),
    )
    try:
        _ = await add_timeline_with_sequence(db, timeline)
    except Exception as exc:
        logger.warning(f"重复入口归档 timeline 记录失败: {exc}")


def _payload_for_inbox(inbox: Any) -> dict[str, Any]:
    return payload_dict(getattr(inbox, "payload_json", None))


def _payload_for_session(session: Any) -> dict[str, Any]:
    raw = getattr(session, "context_json", None)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _build_runtime_session_updated_event_payload(*, workline_id: int | None, session_id: int | None) -> dict[str, Any]:
    """构建工作线运行会话更新事件 payload。"""
    return {
        "domain": "workline_runtime",
        "entity": "session",
        "action": "updated",
        "keys": {
            "workline_id": workline_id,
            "session_id": session_id,
        },
    }


class RuntimeInboxWriteBackService:
    """RuntimeInbox write-back 服务.

    单一职责: 在 OrchestratorService 锁内回调时, 执行 stale session guard
    + 业务 effects + 重复/迟到检测 + fence terminal update.
    """

    def __init__(
        self,
        *,
        write_back_service: Any = None,
        inbox_service: RuntimeInboxService | None = None,
        plugin_attempt_repository: PluginAttemptRepository | Any | None = None,
        intent_log_repository: Any | None = None,
        idempotency_guard: Any | None = None,
        effect_applier: Any | None = None,
        plugin_write_set_limits: PluginWriteSetLimits | None = None,
        workline_repository: Any | None = None,
        device_repository: Any | None = None,
    ) -> None:
        self._write_back_service = write_back_service
        self._inbox_service = inbox_service
        self._plugin_attempt_repository = plugin_attempt_repository or default_plugin_attempt_repository
        self._intent_log_repository = intent_log_repository or default_runtime_intent_log_repository
        self._idempotency_guard = idempotency_guard or default_idempotency_guard
        self._effect_applier = effect_applier
        self._plugin_write_set_limits = plugin_write_set_limits or PluginWriteSetLimits()
        self._workline_repository = workline_repository or default_workline_repository
        self._device_repository = device_repository or default_device_repository

    @property
    def write_back_service(self) -> Any:
        if self._write_back_service is None:
            return orchestrator_write_back_service
        return self._write_back_service

    @property
    def inbox_service(self) -> RuntimeInboxService:
        """RuntimeInboxService 实例.

        终态写回 (mark_processed / mark_failed) 一律走 RuntimeInboxService,
        作用于 RuntimeInbox 表的 lease_token fencing.
        """
        if self._inbox_service is None:
            return runtime_inbox_service
        return self._inbox_service

    @property
    def effect_applier(self) -> Any:
        if self._effect_applier is None:
            from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier

            self._effect_applier = RuntimeIntentEffectApplier()
        return self._effect_applier

    async def commit_plugin_attempt(
        self,
        db: Any,
        *,
        expected_snapshot: AttemptSnapshot,
        inbox_id: int,
        session_id: int,
        workline_id: int,
        trace_id: str,
        write_set: AttemptWriteSet,
        workline: Any | None = None,
        devices_by_role: dict[str, list[Any]] | None = None,
        trusted_state_preservation: bool = False,
    ) -> WriteDisposition:
        """锁定权威行后重校验，并在同一事务落完整 attempt 结果。"""

        locked = await self._plugin_attempt_repository.lock_authoritative(
            db,
            inbox_id=inbox_id,
            session_id=session_id,
        )
        if locked is None or not _authoritative_snapshot_matches(locked, expected_snapshot):
            await db.rollback()
            return WriteDisposition.SAFE_RETRY
        try:
            locked_binding = getattr(locked, "plugin_binding", None)
            if locked_binding is not None:
                # Stage 1 与 Stage 3 之间允许执行外部 QUERY；必须基于锁内 binding
                # 再检查可变准入事实，避免 kill switch、撤权或有效期变化后继续写 effect/state。
                workline_plugin_binding_service.assert_execution_admitted(
                    locked_binding,
                    environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
                    now=timezone.now_utc(),
                )
                typed_config = getattr(locked_binding, "typed_config_json", {}) or {}
                device_roles = typed_config.get("device_roles") if isinstance(typed_config, dict) else None
                required_device_codes = (
                    typed_config.get("required_device_codes") if isinstance(typed_config, dict) else None
                )
                has_device_dependency = (
                    isinstance(device_roles, dict)
                    or isinstance(required_device_codes, (list, tuple))
                    or bool(expected_snapshot.device_fact_versions)
                )
                if has_device_dependency:
                    # Stage 1 对新旧设备 binding 都固定事实；Stage 3 必须与设备拓扑 CRUD 共享锁，
                    # 并锁定重载行直至提交，防止 QUERY 期间新增、换绑或状态更新后提交陈旧决策。
                    await self._workline_repository.acquire_plugin_pin_shared(db, workline_id)
                    current_devices = await self._device_repository.get_by_work_line_id_for_update(db, workline_id)
                    current_devices_by_role: dict[str, list[Any]] = {}
                    for current_device in current_devices:
                        role = getattr(current_device, "device_role", None)
                        if isinstance(role, str) and role:
                            current_devices_by_role.setdefault(role, []).append(current_device)
                    if isinstance(device_roles, dict):
                        workline_plugin_binding_service.assert_device_snapshot(
                            locked_binding,
                            devices_by_role=current_devices_by_role,
                        )
                    current_device_fact_versions = _device_fact_versions(current_devices_by_role)
                    if current_device_fact_versions != tuple(sorted(expected_snapshot.device_fact_versions)):
                        await db.rollback()
                        return WriteDisposition.SAFE_RETRY
        except Exception:
            await db.rollback()
            raise
        # Stage 3 仍执行防御性边界校验；拒绝时必须保留锁内权威状态，
        # 不能把 fail-closed Hold 误写成空插件状态。
        write_set = bound_attempt_write_set(
            write_set,
            limits=self._plugin_write_set_limits,
            fallback_state=dict(getattr(locked.session, "plugin_state_json", {}) or {}),
            allow_state_preservation=trusted_state_preservation,
        )
        try:
            if write_set.intents:
                prepared_intents = self._intent_log_repository.prepare_attempt_intents(
                    locked=locked,
                    snapshot=expected_snapshot,
                    intents=write_set.intents,
                )
                for prepared in prepared_intents:
                    claim_result = await self._idempotency_guard.claim_or_match(
                        db,
                        **prepared.claim,
                        now_ms=int(timezone.now_utc().timestamp() * 1000),
                    )
                    if claim_result is ClaimResult.NEW:
                        self._intent_log_repository.add_prepared(db, prepared)
                effect_result = await self.effect_applier.apply(
                    {
                        "db": db,
                        "session": locked.session,
                        "workline": workline or SimpleNamespace(id=workline_id),
                        "inbox": locked.inbox,
                        "work_item": getattr(locked, "work_item", None),
                        "plugin_binding": getattr(locked, "plugin_binding", None),
                        "devices_by_role": devices_by_role or {},
                        "trace_id": trace_id,
                        "correlation_id": getattr(locked.inbox, "correlation_id", None) or trace_id,
                        "orch_result": SimpleNamespace(),
                    },
                    list(write_set.intents),
                )
                business_reject_evidence = getattr(effect_result, "business_reject_evidence", None)
                if isinstance(business_reject_evidence, dict):
                    reject_source = {
                        "kind": _kind_value(locked.inbox),
                        "payload_json": dict(getattr(locked.inbox, "payload_json", {}) or {}),
                        "event_id": getattr(locked.inbox, "event_id", None),
                        "trace_id": getattr(locked.inbox, "trace_id", None) or trace_id,
                        "execution_session_id": getattr(locked.inbox, "execution_session_id", None),
                        "correlation_id": getattr(locked.inbox, "correlation_id", None),
                    }
                    await db.rollback()
                    if reject_source["payload_json"].get("logical_route") == "CAPABILITY_EFFECT_RESULT":
                        terminal_updated = await self.inbox_service.mark_failed(
                            db,
                            inbox_id=inbox_id,
                            lease_token=expected_snapshot.processor_token,
                            error_code="CAPABILITY_EFFECT_REDECISION_REJECTED",
                            error_message="capability result redecision rejected without recursive feedback",
                            retryable=False,
                        )
                    else:
                        feedback_digest = sha256_digest(business_reject_evidence)
                        await self.inbox_service.accept_internal_event(
                            db,
                            event_type="CAPABILITY_EFFECT_RESULT",
                            payload_json={
                                "logical_route": "CAPABILITY_EFFECT_RESULT",
                                "data": {
                                    "session_id": session_id,
                                    "effect_evidence": business_reject_evidence,
                                },
                            },
                            trace_id=str(reject_source["trace_id"]),
                            event_id=f"capability-effect-reject:{inbox_id}:{feedback_digest[:32]}",
                            causation_id=(
                                str(reject_source["event_id"]) if reject_source["event_id"] is not None else None
                            ),
                            workline_id=workline_id,
                            execution_session_id=(
                                int(reject_source["execution_session_id"])
                                if isinstance(reject_source["execution_session_id"], int)
                                else None
                            ),
                            correlation_id=(
                                str(reject_source["correlation_id"])
                                if isinstance(reject_source["correlation_id"], str)
                                else None
                            ),
                            auto_commit=False,
                        )
                        terminal_updated = await self.inbox_service.mark_processed(
                            db,
                            inbox_id=inbox_id,
                            lease_token=expected_snapshot.processor_token,
                        )
                    _require_fenced_update(
                        terminal_updated,
                        action="plugin_effect_business_reject_terminal",
                        inbox_id=inbox_id,
                    )
                    await db.commit()
                    return WriteDisposition.COMMITTED
            await self._plugin_attempt_repository.persist_locked_attempt(
                db,
                locked=locked,
                workline_id=workline_id,
                trace_id=trace_id,
                snapshot=expected_snapshot,
                write_set=write_set,
            )
            if write_set.hold_reason is None:
                terminal_updated = await self.inbox_service.mark_processed(
                    db,
                    inbox_id=inbox_id,
                    lease_token=expected_snapshot.processor_token,
                )
            else:
                terminal_updated = await self.inbox_service.mark_failed(
                    db,
                    inbox_id=inbox_id,
                    lease_token=expected_snapshot.processor_token,
                    error_code=write_set.hold_reason,
                    error_message="recorded replay failed closed to Hold",
                    retryable=False,
                )
            _require_fenced_update(terminal_updated, action="plugin_attempt_terminal", inbox_id=inbox_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return WriteDisposition.COMMITTED

    def build_write_callback(
        self,
        db: Any,
        *,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        device: Any | None,
        command: Any | None,
        inbox_pk: int,
        session_snapshot: tuple[Any, Any],
        sse_workline_id: int | None,
        sse_session_id: int | None,
        processor_token: str,
        state: WriteBackState,
    ) -> "Callable[[OrchestratorResult], Awaitable[None]]":  # noqa: UP037
        """构造 OrchestratorService 写回调闭包."""

        async def _write_callback(write_result: "OrchestratorResult") -> None:  # noqa: UP037
            try:
                await db.refresh(session)
                payload = _payload_for_inbox(inbox)
                if _is_late_or_duplicate_command_result_for_session(
                    inbox=inbox,
                    payload=payload,
                    session=session,
                    command=command,
                ):
                    await _record_late_command_result_archive_timeline(
                        db,
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        command=command,
                        payload=payload,
                        reason="COMMAND_RESULT_BECAME_STALE_BEFORE_WRITE",
                    )
                    _require_fenced_update(
                        await self.inbox_service.mark_processed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                        ),
                        action="mark_processed",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
                    state.disposition = WriteBackDisposition.PROCESSED
                    state.write_effects_applied = True
                    state.enqueue_outbox_dispatch = False
                    return

                if _session_write_snapshot(session) != session_snapshot:
                    raise RuntimeError("Session state changed before WRITE apply; refusing stale orchestrator effects")

                _ = reapply_pending_session_ingress_metadata(session)

                effect_result: RuntimeIntentEffectResult = await self.write_back_service.write_back(
                    db,
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    devices_by_role=devices_by_role,
                    source_device=device,
                    orch_result=write_result,
                )
                write_disposition = effect_result.disposition
                state.disposition = write_disposition
                if write_disposition == WriteBackDisposition.RESOURCE_RETRY:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_code="RESOURCE_WAIT",
                            error_message="RESOURCE_WAIT",
                            retryable=True,
                            consume_attempt=False,
                        ),
                        action="mark_failed",
                        inbox_id=inbox_pk,
                    )
                elif write_disposition == WriteBackDisposition.PROCESSED:
                    _require_fenced_update(
                        await self.inbox_service.mark_processed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                        ),
                        action="mark_processed",
                        inbox_id=inbox_pk,
                    )
                    session_context = dict(_payload_for_session(session))
                    resource_wait_context = payload_dict(session_context.get("resource_wait"))
                    if optional_int(resource_wait_context.get("inbox_id")) == inbox_pk and resource_wait_context.get(
                        "subject_key"
                    ):
                        from src.app.workline.services.diagnostic_service import (
                            workline_diagnostic_service,
                        )

                        _ = await workline_diagnostic_service.resolve_resource_wait_diagnostics(
                            db,
                            inbox_id=inbox_pk,
                            subject_type=str(resource_wait_context["subject_type"]),
                            subject_key=str(resource_wait_context["subject_key"]),
                            projection_type=str(resource_wait_context["projection_type"]),
                            auto_commit=False,
                        )
                        session_context.pop("resource_wait", None)
                        session.context_json = session_context
                else:
                    raise RuntimeError(f"Unsupported write-back disposition: {write_disposition}")

                await db.commit()
                state.write_effects_applied = True
                state.enqueue_outbox_dispatch = (
                    write_disposition == WriteBackDisposition.PROCESSED
                    and _result_requires_outbox_dispatch(write_result)
                )

                from src.app.sys.services.event_stream_service import (
                    WORKLINE_RUNTIME_CHANGED_EVENT,
                    defer_sse_event,
                )

                defer_sse_event(
                    db,
                    WORKLINE_RUNTIME_CHANGED_EVENT,
                    _build_runtime_session_updated_event_payload(
                        workline_id=sse_workline_id,
                        session_id=sse_session_id,
                    ),
                )
            except Exception:
                with suppress(Exception):
                    await db.rollback()
                raise

        return _write_callback


__all__ = [
    "RuntimeInboxLeaseLostError",
    "RuntimeInboxWriteBackService",
    "WriteBackState",
    "_is_late_or_duplicate_command_result_for_session",
    "_record_duplicate_entry_archive_timeline",
    "_record_late_command_result_archive_timeline",
    "_result_requires_outbox_dispatch",
    "_session_status_value",
    "_session_write_snapshot",
]
