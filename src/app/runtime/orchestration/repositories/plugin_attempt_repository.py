"""平台插件 attempt 的权威锁定与原子持久化 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import noload
from sqlmodel import select

from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import (
    runtime_inbox_repository,
)
from src.app.runtime.orchestration.repositories.timeline_sequence_repository import (
    timeline_sequence_repository,
)
from src.app.runtime.system_capabilities.evidence import QueryEvidence
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet


@dataclass(frozen=True, slots=True)
class AuthoritativePluginAttempt:
    """同一事务中已 SELECT FOR UPDATE 的 Inbox 与 Session。"""

    inbox: Any
    session: WorklineSession
    execution_session: ExecutionSession | Any | None = None
    work_item: ExecutionWorkItem | Any | None = None
    plugin_binding: WorklinePluginBinding | Any | None = None


class PluginAttemptRepository:
    """Stage 3 唯一数据库入口。"""

    def __init__(
        self,
        *,
        inbox_repository: Any = runtime_inbox_repository,
        timeline_sequence_repository: Any = timeline_sequence_repository,
    ) -> None:
        self._inbox_repository = inbox_repository
        self._timeline_sequence_repository = timeline_sequence_repository

    async def lock_authoritative(  # noqa: PLR0911 - 每个不一致条件均 fail closed，禁止部分权威身份通过。
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        session_id: int,
    ) -> AuthoritativePluginAttempt | None:
        """按 Inbox row → Session row 固定顺序锁定。"""

        # RuntimeInbox 锁定由其专属 Repository 持有，避免插件仓库越过 inbound ownership 边界。
        inbox = await self._inbox_repository.get_by_id_for_update(db, inbox_id, populate_existing=True)
        if inbox is None:
            return None
        session = await db.scalar(
            select(WorklineSession)
            .where(WorklineSession.id == session_id)
            .options(noload(WorklineSession.workline))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if session is None:
            return None
        if getattr(inbox, "workline_session_id", None) != session_id:
            return None
        execution_session_id = getattr(inbox, "execution_session_id", None)
        correlation_id = getattr(inbox, "correlation_id", None)
        if not isinstance(execution_session_id, int) or not isinstance(correlation_id, str) or not correlation_id:
            return None
        execution_session = await db.scalar(
            select(ExecutionSession)
            .where(ExecutionSession.id == execution_session_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if execution_session is None:
            return None
        work_item = await db.scalar(
            select(ExecutionWorkItem)
            .where(ExecutionWorkItem.correlation_id == correlation_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if work_item is None:
            return None
        workline_id = getattr(session, "workline_id", None)
        if (
            getattr(inbox, "workline_id", None) != workline_id
            or getattr(execution_session, "workline_id", None) != workline_id
            or getattr(work_item, "execution_session_id", None) != execution_session_id
            or getattr(work_item, "correlation_id", None) != correlation_id
        ):
            return None
        session_pin = (
            getattr(session, "plugin_key", None),
            getattr(session, "plugin_binding_id", None),
            getattr(session, "plugin_binding_version", None),
            getattr(session, "plugin_config_hash", None),
            getattr(session, "plugin_index_digest", None),
        )
        if session_pin != (
            getattr(execution_session, "plugin_key", None),
            getattr(execution_session, "plugin_binding_id", None),
            getattr(execution_session, "plugin_binding_version", None),
            getattr(execution_session, "plugin_config_hash", None),
            getattr(execution_session, "plugin_index_digest", None),
        ) or session_pin != (
            getattr(work_item, "plugin_key", None),
            getattr(work_item, "plugin_binding_id", None),
            getattr(work_item, "plugin_binding_version", None),
            getattr(work_item, "plugin_config_hash", None),
            getattr(work_item, "plugin_index_digest", None),
        ):
            return None
        binding_id = getattr(session, "plugin_binding_id", None)
        plugin_binding = None
        if isinstance(binding_id, int):
            plugin_binding = await db.scalar(
                select(WorklinePluginBinding)
                .where(WorklinePluginBinding.id == binding_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if plugin_binding is None:
                return None
            if (
                getattr(plugin_binding, "id", None),
                getattr(plugin_binding, "plugin_key", None),
                getattr(plugin_binding, "contract_version", None),
                getattr(plugin_binding, "binding_version", None),
                getattr(plugin_binding, "typed_config_hash", None),
                getattr(plugin_binding, "generated_index_digest", None),
            ) != (
                binding_id,
                getattr(session, "plugin_key", None),
                getattr(session, "contract_version", None),
                getattr(session, "plugin_binding_version", None),
                getattr(session, "plugin_config_hash", None),
                getattr(session, "plugin_index_digest", None),
            ):
                return None
        return AuthoritativePluginAttempt(
            inbox=inbox,
            session=session,
            execution_session=execution_session,
            work_item=work_item,
            plugin_binding=plugin_binding,
        )

    async def persist_locked_attempt(
        self,
        db: AsyncSession,
        *,
        locked: AuthoritativePluginAttempt,
        workline_id: int,
        trace_id: str,
        snapshot: AttemptSnapshot,
        write_set: AttemptWriteSet,
    ) -> None:
        """在已锁事务写 evidence、decision/intents 与 plugin state。

        全局锁序与 reconciliation 保持兼容：Inbox row → Session row → timeline advisory。
        """

        session = locked.session
        seq_nos = iter(
            await self._timeline_sequence_repository.allocate_many(
                db,
                session_id=int(session.id),
                count=len(write_set.evidence) + 1,
                lock_already_held=False,
            )
        )
        for evidence in write_set.evidence:
            if not isinstance(evidence, QueryEvidence):
                raise TypeError("plugin attempt evidence must be QueryEvidence")
            db.add(
                WorklineTimeline(
                    session_id=int(session.id),
                    workline_id=workline_id,
                    trace_id=trace_id,
                    seq_no=next(seq_nos),
                    occurred_at=timezone.now_for_db(),
                    stage=TimelineStage.DECISION,
                    action_type=TimelineActionType.DECISION_MADE,
                    actor_type=TimelineActorType.ORCHESTRATOR,
                    actor_code="system-capability-gateway",
                    status=TimelineStatus.SUCCESS,
                    message="System Capability QUERY evidence",
                    payload_json={
                        "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                        "evidence": evidence.payload(),
                    },
                    related_inbox_id=int(locked.inbox.id),
                )
            )

        evidence_keys = [
            [
                evidence.capability_key,
                evidence.contract_version,
                evidence.input_hash,
                evidence.output_hash,
            ]
            for evidence in write_set.evidence
            if isinstance(evidence, QueryEvidence)
        ]
        attempt_anchor = (
            _json_value(write_set.recorded_attempt_anchor)
            if write_set.recorded_attempt_anchor is not None
            else {
                "source_inbox_id": int(locked.inbox.id),
                "session_version": snapshot.session_version,
                "session_status": snapshot.session_status,
                "logical_idempotency_key": _logical_idempotency_key(locked),
            }
        )
        decision_payload = {
            "record_type": "PLUGIN_DECISION",
            "definition_identity": snapshot.definition_identity,
            "binding_identity": snapshot.binding_identity,
            "index_digest": snapshot.index_digest,
            "attempt_anchor": attempt_anchor,
            "evidence_keys": evidence_keys,
            "decision": write_set.recorded_decision
            or {
                "outcome_code": write_set.outcome_code,
                "hold_reason": write_set.hold_reason,
                "intents": [_json_value(intent) for intent in write_set.intents],
                "next_state": _json_value(write_set.next_state),
            },
        }
        db.add(
            WorklineTimeline(
                session_id=int(session.id),
                workline_id=workline_id,
                trace_id=trace_id,
                seq_no=next(seq_nos),
                occurred_at=timezone.now_for_db(),
                stage=TimelineStage.DECISION,
                action_type=TimelineActionType.DECISION_MADE,
                actor_type=TimelineActorType.PLUGIN,
                # Timeline actor_code 有长度约束，完整定义身份已保存在 data_json 中用于重放校验。
                actor_code=(snapshot.definition_identity or "platform-plugin")[:100],
                status=TimelineStatus.FAILED if write_set.hold_reason else TimelineStatus.SUCCESS,
                message=write_set.hold_reason or write_set.outcome_code,
                payload_json=decision_payload,
                related_inbox_id=int(locked.inbox.id),
            )
        )
        session.plugin_state_json = dict(_json_value(write_set.next_state))
        session.plugin_state_version += 1


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _logical_idempotency_key(locked: AuthoritativePluginAttempt) -> str:
    """由 pinned plugin 与 execution work item 生成稳定的业务 attempt identity。"""

    plugin_key = str(getattr(locked.session, "plugin_key", None) or "unknown-plugin")
    object_type = str(getattr(locked.work_item, "object_type", None) or "session")
    object_key = str(
        getattr(locked.work_item, "object_key", None) or getattr(locked.session, "business_key", None) or "unknown"
    )
    return f"workline-plugin:{plugin_key}:{object_type}:{object_key}:decision"


plugin_attempt_repository = PluginAttemptRepository()

__all__ = ["AuthoritativePluginAttempt", "PluginAttemptRepository", "plugin_attempt_repository"]
