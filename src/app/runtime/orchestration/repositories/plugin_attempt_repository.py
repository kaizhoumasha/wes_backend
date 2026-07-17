"""平台插件 attempt 的权威锁定与原子持久化 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlmodel import select

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
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet


@dataclass(frozen=True, slots=True)
class AuthoritativePluginAttempt:
    """同一事务中已 SELECT FOR UPDATE 的 Inbox 与 Session。"""

    inbox: Any
    session: WorklineSession


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

    async def lock_authoritative(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        session_id: int,
    ) -> AuthoritativePluginAttempt | None:
        """按 timeline advisory → Inbox row → Session row 固定顺序锁定。"""

        # 所有 Timeline writer 先取得同一 session advisory；
        # 随后才允许获取 attempt 行锁，避免锁序反转死锁。
        await self._timeline_sequence_repository.acquire_lock(db, session_id=session_id)
        # RuntimeInbox 锁定由其专属 Repository 持有，避免插件仓库越过 inbound ownership 边界。
        inbox = await self._inbox_repository.get_by_id_for_update(db, inbox_id, populate_existing=True)
        if inbox is None:
            return None
        session = await db.scalar(select(WorklineSession).where(WorklineSession.id == session_id).with_for_update())
        if session is None:
            return None
        return AuthoritativePluginAttempt(inbox=inbox, session=session)

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
        """在已锁事务写 evidence、decision/intents 与 plugin state。"""

        session = locked.session
        seq_nos = iter(
            await self._timeline_sequence_repository.allocate_many(
                db,
                session_id=int(session.id),
                count=len(write_set.evidence) + 1,
                lock_already_held=True,
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
        decision_payload = {
            "record_type": "PLUGIN_DECISION",
            "definition_identity": snapshot.definition_identity,
            "binding_identity": snapshot.binding_identity,
            "index_digest": snapshot.index_digest,
            "evidence_keys": evidence_keys,
            "decision": {
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
        session.version += 1


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


plugin_attempt_repository = PluginAttemptRepository()

__all__ = ["AuthoritativePluginAttempt", "PluginAttemptRepository", "plugin_attempt_repository"]
