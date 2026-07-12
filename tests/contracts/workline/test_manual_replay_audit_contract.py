"""BC-XX Manual replay from dead-letter / audit chain 行为契约。

验收: DEAD_LETTER 是终态, 人工重放必须新建 inbox 记录 (不可就地重置);
       重放记录继承 trace_id 并发 causation_id = 原 source_event_id 形成因果链;
       重放请求必须有 actor + reason + 审计日志 (主计划 §9.2 RuntimeInbox 处理契约 + H5)。
mock 仅允许 `tests/support/runtime_inbox_contract.py` + RuntimeTimeline 替身。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.sys.models.audit_log import AuditLog
from src.app.sys.services import AuditLogService
from tests.support.runtime_inbox_contract import (
    LEGAL_TRANSITIONS,
    RuntimeInboxEntry,
    is_terminal,
    transition,
)

# === ManualReplay 替身 (Contract 8 范围: 仅契约, 不实现 production) ===


@dataclass
class ManualReplayRequest:
    """人工重放请求 (H5 审计最小契约)。"""

    source_event_id: str
    payload_hash: str
    actor: str
    reason: str
    replay_trace_id: str | None = None


@dataclass
class ManualReplayAuditRecord:
    """重放审计记录, 写入 RuntimeTimeline 作为因果链证据。"""

    actor: str
    reason: str
    source_event_id: str
    replay_inbox_id: int
    replay_trace_id: str
    causation_id: str
    audited_at: int


class _AuditLog:
    """RuntimeTimeline 替身, 用于断言因果链完整。"""

    def __init__(self) -> None:
        self._rows: list[RuntimeTimeline] = []

    def append_replay_audit(
        self,
        *,
        actor: str,
        reason: str,
        replay_inbox_id: int,
        replay_trace_id: str,
        causation_id: str,
        audited_at: int,
        execution_session_id: int | None = None,
    ) -> ManualReplayAuditRecord:
        rec = ManualReplayAuditRecord(
            actor=actor,
            reason=reason,
            source_event_id=causation_id,
            replay_inbox_id=replay_inbox_id,
            replay_trace_id=replay_trace_id,
            causation_id=causation_id,
            audited_at=audited_at,
        )
        self._rows.append(
            RuntimeTimeline(
                execution_session_id=execution_session_id,
                trace_id=replay_trace_id,
                correlation_id=None,
                event_type="MANUAL_REPLAY_AUDIT",
                occurred_at=audited_at,
            )
        )
        return rec


class _ManualReplayService:
    """人工重放最小替身 — 死信条目不可就地重置, 必须新建 inbox 记录 + 审计。"""

    REPLAY_MAX_RETRIES = 5

    def __init__(self, audit: _AuditLog | None = None) -> None:
        self.audit = audit or _AuditLog()
        self._replays: list[tuple[ManualReplayRequest, RuntimeInboxEntry]] = []

    def replay_from_dead_letter(
        self,
        dead_entry: RuntimeInboxEntry,
        req: ManualReplayRequest,
        *,
        now: float,
        next_inbox_id: int,
        replay_trace_id: str,
    ) -> RuntimeInboxEntry:
        if dead_entry.status != "DEAD_LETTER":
            raise ValueError(f"仅 DEAD_LETTER 可重放, 当前 status={dead_entry.status}")

        if not req.actor.strip():
            raise ValueError("actor 不能为空 (H5 审计必填)")
        if not req.reason.strip():
            raise ValueError("reason 不能为空 (H5 审计必填)")
        if not req.source_event_id:
            raise ValueError("source_event_id 不能为空")
        if not req.payload_hash:
            raise ValueError("payload_hash 不能为空")

        new_entry = RuntimeInboxEntry(
            status="RECEIVED",
            attempt_count=0,
            next_retry_at=None,
            lease_until=None,
            max_retries=self.REPLAY_MAX_RETRIES,
            payload_hash=req.payload_hash,
            source_event_id=req.source_event_id,
            metadata={
                **dead_entry.metadata,
                "replay_actor": req.actor,
                "replay_reason": req.reason,
                "replay_source_inbox_status": "DEAD_LETTER",
            },
        )
        self._replays.append((req, new_entry))

        self.audit.append_replay_audit(
            actor=req.actor,
            reason=req.reason,
            replay_inbox_id=next_inbox_id,
            replay_trace_id=replay_trace_id,
            causation_id=req.source_event_id,
            audited_at=int(now * 1000),
        )
        return new_entry


def test_dead_letter_is_terminal_state():
    """happy path: DEAD_LETTER 是终态, 不能就地重置 (主计划 §9.2)。"""
    entry = RuntimeInboxEntry(status="FAILED", attempt_count=6)
    transition(entry, "DEAD_LETTER", now=1000.0)
    assert is_terminal(entry) is True
    assert entry.status == "DEAD_LETTER"
    assert LEGAL_TRANSITIONS["DEAD_LETTER"] == set(), "DEAD_LETTER 无合法后续转移"


def test_manual_replay_creates_new_inbox_entry_not_reset():
    """happy path: 重放必须新建 inbox 记录, 原 DEAD_LETTER 条目保持终态。"""
    audit = _AuditLog()
    service = _ManualReplayService(audit=audit)
    dead = RuntimeInboxEntry(status="FAILED", attempt_count=6)
    transition(dead, "DEAD_LETTER", now=1000.0)

    req = ManualReplayRequest(
        source_event_id="evt-original-001",
        payload_hash="hash-replay-001",
        actor="ops-aaron",
        reason="修复后人工触发重放",
        replay_trace_id="trace-replay-001",
    )
    replayed = service.replay_from_dead_letter(
        dead,
        req,
        now=2000.0,
        next_inbox_id=9001,
        replay_trace_id="trace-replay-001",
    )

    assert dead.status == "DEAD_LETTER", "原 DEAD_LETTER 条目保持终态, 不可就地重置"
    assert replayed.status == "RECEIVED", "重放条目以 RECEIVED 重新进入状态机"
    assert replayed.attempt_count == 0, "重置 attempt_count"
    assert replayed.payload_hash == "hash-replay-001"
    assert replayed.metadata["replay_actor"] == "ops-aaron"
    assert replayed.metadata["replay_reason"] == "修复后人工触发重放"


def test_manual_replay_writes_audit_record_with_causation_chain():
    """happy path: 重放审计记录含 causation_id = 原 source_event_id, 形成因果链。"""
    audit = _AuditLog()
    service = _ManualReplayService(audit=audit)
    dead = RuntimeInboxEntry(status="FAILED", attempt_count=6)
    transition(dead, "DEAD_LETTER", now=1000.0)

    req = ManualReplayRequest(
        source_event_id="evt-original-001",
        payload_hash="hash-replay-001",
        actor="ops-aaron",
        reason="修复后人工触发重放",
    )
    service.replay_from_dead_letter(
        dead,
        req,
        now=2000.0,
        next_inbox_id=9001,
        replay_trace_id="trace-replay-001",
    )

    assert len(audit._rows) == 1
    audit_row = audit._rows[0]
    assert audit_row.event_type == "MANUAL_REPLAY_AUDIT"
    assert audit_row.trace_id == "trace-replay-001"
    assert audit_row.occurred_at == 2_000_000


@pytest.mark.asyncio
async def test_production_manual_replay_audit_binds_canonical_reason_and_persisted_causation(db_session) -> None:
    """生产 service + AuditLogService 写已接受 reason 与最终因果字段，same-hash ACK 不重复。"""

    source = RuntimeInbox(
        kind="INTERNAL_EVENT",
        provider_code="RUNTIME",
        event_type="INTERNAL_EVENT",
        source_event_id="contract-replay-source",
        payload_hash="contract-replay-source-hash",
        payload_json={"event_type": "SESSION_RESUME", "data": {}},
        payload_schema_version=1,
        trace_id="contract-replay-trace",
        event_id="contract-replay-event",
        status="DEAD_LETTER",
        claim_bucket_key="source:contract-replay-source",
        received_at=1_700_000_000_000,
        failed_at=1_700_000_000_001,
    )
    db_session.add(source)
    await db_session.flush()
    service = RuntimeInboxService(audit_service=AuditLogService())

    created = await service.replay_from_dead_letter(
        db_session,
        source_inbox_id=source.id,
        request_id="contract-success",
        actor="contract-operator",
        reason="  accepted canonical reason  ",
    )
    acknowledged = await service.replay_from_dead_letter(
        db_session,
        source_inbox_id=source.id,
        request_id="contract-success",
        actor="contract-operator",
        reason="accepted canonical reason",
    )

    assert acknowledged.replay_record.id == created.replay_record.id
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.object_id == str(source.id),
                    AuditLog.action == "manual_replay",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].args is not None
    assert audits[0].args["reason"] == "accepted canonical reason"
    assert audits[0].args["replay_trace_id"] == created.replay_record.trace_id
    assert audits[0].args["causation_id"] == created.replay_record.causation_id
    assert "original_payload" not in audits[0].args


def test_manual_replay_rejects_non_dead_letter_source():
    """error path: 仅 DEAD_LETTER 状态可重放, PROCESSED/FAILED 拒绝。"""
    service = _ManualReplayService()
    processed_entry = RuntimeInboxEntry(status="PROCESSED")

    with pytest.raises(ValueError, match="仅 DEAD_LETTER 可重放"):
        service.replay_from_dead_letter(
            processed_entry,
            ManualReplayRequest(
                source_event_id="evt-001",
                payload_hash="hash-001",
                actor="ops",
                reason="r",
            ),
            now=2000.0,
            next_inbox_id=9002,
            replay_trace_id="trace-001",
        )


def test_manual_replay_requires_actor_for_audit():
    """H5 审计: actor 不能为空。"""
    service = _ManualReplayService()
    dead = RuntimeInboxEntry(status="FAILED", attempt_count=6)
    transition(dead, "DEAD_LETTER", now=1000.0)

    with pytest.raises(ValueError, match="actor 不能为空"):
        service.replay_from_dead_letter(
            dead,
            ManualReplayRequest(
                source_event_id="evt-001",
                payload_hash="hash-001",
                actor="   ",
                reason="r",
            ),
            now=2000.0,
            next_inbox_id=9003,
            replay_trace_id="trace-001",
        )


def test_manual_replay_requires_reason_for_audit():
    """H5 审计: reason 不能为空 (主计划 §9.2 重放可追溯)。"""
    service = _ManualReplayService()
    dead = RuntimeInboxEntry(status="FAILED", attempt_count=6)
    transition(dead, "DEAD_LETTER", now=1000.0)

    with pytest.raises(ValueError, match="reason 不能为空"):
        service.replay_from_dead_letter(
            dead,
            ManualReplayRequest(
                source_event_id="evt-001",
                payload_hash="hash-001",
                actor="ops",
                reason="",
            ),
            now=2000.0,
            next_inbox_id=9004,
            replay_trace_id="trace-001",
        )


def test_manual_replay_uses_independent_fixed_retry_budget():
    """happy path: 重放条目使用独立固定预算，不继承来源历史预算。"""
    service = _ManualReplayService()
    dead = RuntimeInboxEntry(status="FAILED", attempt_count=6, max_retries=10)
    transition(dead, "DEAD_LETTER", now=1000.0)

    replayed = service.replay_from_dead_letter(
        dead,
        ManualReplayRequest(
            source_event_id="evt-001",
            payload_hash="hash-001",
            actor="ops",
            reason="retry",
        ),
        now=2000.0,
        next_inbox_id=9005,
        replay_trace_id="trace-001",
    )
    assert replayed.max_retries == 5


def test_manual_replay_propagates_failure_chain_to_dead_letter_again():
    """replay path: 重放条目再次失败, attempt_count 累加并最终超 max_retries
    进 DEAD_LETTER (主计划 §9.2)。"""
    service = _ManualReplayService()
    dead = RuntimeInboxEntry(status="FAILED", attempt_count=6, max_retries=2)
    transition(dead, "DEAD_LETTER", now=1000.0)

    replayed = service.replay_from_dead_letter(
        dead,
        ManualReplayRequest(
            source_event_id="evt-001",
            payload_hash="hash-001",
            actor="ops",
            reason="retry",
        ),
        now=2000.0,
        next_inbox_id=9006,
        replay_trace_id="trace-001",
    )
    assert replayed.max_retries == 5
    assert replayed.attempt_count == 0

    for attempt in range(1, 7):
        transition(replayed, "PROCESSING", now=2000.0 + attempt * 100)
        transition(replayed, "FAILED", now=2001.0 + attempt * 100)
        assert replayed.attempt_count == attempt
        if attempt <= replayed.max_retries:
            transition(replayed, "RECEIVED", now=2002.0 + attempt * 100)

    assert replayed.status == "DEAD_LETTER"
    assert is_terminal(replayed)
