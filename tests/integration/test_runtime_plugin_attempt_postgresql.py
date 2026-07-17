"""平台插件 Stage3 在真实 PostgreSQL 上的 stale snapshot 并发合同。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, update
from sqlmodel import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.runtime.orchestration.repositories.timeline_sequence_repository import TimelineSequenceRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    _write_set_from_recorded_replay,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
)
from src.app.runtime.system_capabilities.replay import RecordedReplayResolution
from src.app.runtime.workline_plugins.attempt_coordinator import (
    AttemptSnapshot,
    AttemptWriteSet,
    WriteDisposition,
)
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    seed_scan_flow,
    with_temporary_runtime_database,
)


async def _seed_pinned_attempt(session_factory, *, token: str):  # type: ignore[no-untyped-def]
    service = RuntimeInboxService()
    async with session_factory() as db:
        seeded = await seed_scan_flow(db)
        execution_session = ExecutionSession(
            workline_id=seeded.workline_id,
            plugin_key="rough_sorter",
            manifest_version="it-manifest-v1",
            state="RUNNING",
        )
        binding = WorklinePluginBinding(
            workline_id=seeded.workline_id,
            plugin_key="rough_sorter",
            contract_version="rough_sorter.v2",
            binding_version=1,
            typed_config_hash="a" * 64,
            generated_index_digest="b" * 64,
            environment="test",
            activated_at=timezone.now_for_db(),
            activated_by="integration-test",
            activated_reason="plugin attempt atomicity",
        )
        db.add_all([execution_session, binding])
        await db.flush()
        session = await db.get(WorklineSession, seeded.session_id)
        inbox = await db.get(RuntimeInbox, seeded.inbox_id)
        correlation = await db.scalar(
            select(ExecutionCorrelation).where(ExecutionCorrelation.correlation_id == "it-runtime-inbox-correlation")
        )
        assert session is not None and inbox is not None and correlation is not None
        assert execution_session.id is not None and binding.id is not None
        session.plugin_binding_id = binding.id
        session.plugin_binding_version = binding.binding_version
        session.plugin_index_digest = binding.generated_index_digest
        inbox.execution_session_id = execution_session.id
        inbox.correlation_id = correlation.correlation_id
        correlation.execution_session_id = execution_session.id
        await db.commit()
        claimed = await claim(db, service, token=token)
        await db.refresh(session)
        snapshot = AttemptSnapshot(
            processor_token=str(claimed["processor_token"]),
            session_version=session.version,
            plugin_state_version=session.plugin_state_version,
            definition_identity=f"{session.plugin_key}@{session.contract_version}",
            binding_id=session.plugin_binding_id,
            binding_version=session.plugin_binding_version,
            index_digest=session.plugin_index_digest,
        )
    return seeded, snapshot, service


def test_stale_plugin_state_version_cannot_write_evidence_state_intents_or_terminal() -> None:
    """另一个事务推进 plugin state 后，旧 attempt 必须锁后 SAFE_RETRY 且零写。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as snapshot_db:
            seeded = await seed_scan_flow(snapshot_db)
            claimed = await claim(snapshot_db, service, token="plugin-attempt-owner")
            session = await snapshot_db.get(WorklineSession, seeded.session_id)
            assert session is not None
            snapshot = AttemptSnapshot(
                processor_token=str(claimed["processor_token"]),
                session_version=session.version,
                plugin_state_version=session.plugin_state_version,
            )

        async with session_factory() as concurrent_db:
            await concurrent_db.execute(
                update(WorklineSession)
                .where(WorklineSession.id == seeded.session_id)
                .values(plugin_state_version=snapshot.plugin_state_version + 1)
            )
            await concurrent_db.commit()

        async with session_factory() as writeback_db:
            disposition = await RuntimeInboxWriteBackService(inbox_service=service).commit_plugin_attempt(
                writeback_db,
                expected_snapshot=snapshot,
                inbox_id=seeded.inbox_id,
                session_id=seeded.session_id,
                workline_id=seeded.workline_id,
                trace_id=seeded.trace_id,
                write_set=AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=()),
            )
            assert disposition is WriteDisposition.SAFE_RETRY
            inbox = await writeback_db.get(RuntimeInbox, seeded.inbox_id)
            assert inbox is not None and inbox.status == "PROCESSING"
            timeline_count = await writeback_db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(WorklineTimeline.related_inbox_id == seeded.inbox_id)
            )
            assert timeline_count == 0

    asyncio.run(with_temporary_runtime_database(scenario))


def test_non_empty_plugin_intent_persists_ledger_before_terminal_in_same_transaction() -> None:
    """成功 attempt 必须原子提交 decision/state、RuntimeIntentLog 与 Inbox terminal。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        seeded, snapshot, service = await _seed_pinned_attempt(session_factory, token="plugin-ledger-owner")
        intent = RuntimeIntent(
            kind=RuntimeIntentKind.COMMAND,
            action="MOVE",
            idempotency_key="operation-1",
            payload_json={"target": "A-01"},
        )
        replay_write_set = _write_set_from_recorded_replay(
            RecordedReplayResolution(
                decision={
                    "outcome_code": "ROUTE_A",
                    "next_state": {"step": 2},
                    "intents": [intent.model_dump(mode="json")],
                }
            ),
            fallback_state={},
        )
        assert isinstance(replay_write_set.intents[0], RuntimeIntent)
        async with session_factory() as db:
            disposition = await RuntimeInboxWriteBackService(inbox_service=service).commit_plugin_attempt(
                db,
                expected_snapshot=snapshot,
                inbox_id=seeded.inbox_id,
                session_id=seeded.session_id,
                workline_id=seeded.workline_id,
                trace_id=seeded.trace_id,
                write_set=replay_write_set,
            )
            assert disposition is WriteDisposition.COMMITTED

        async with session_factory() as verify_db:
            inbox = await verify_db.get(RuntimeInbox, seeded.inbox_id)
            session = await verify_db.get(WorklineSession, seeded.session_id)
            assert inbox is not None
            ledger = await verify_db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.execution_session_id == inbox.execution_session_id)
            )
            timeline_count = await verify_db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(WorklineTimeline.related_inbox_id == seeded.inbox_id)
            )
            assert inbox.status == "PROCESSED"
            assert session is not None and session.plugin_state_json == {"step": 2}
            assert timeline_count == 1
            assert ledger is not None
            assert ledger.idempotency_key == f"plugin-attempt:binding:{snapshot.binding_id}:1:operation-1"
            assert ledger.dispatch_status == "PENDING"
            assert len(ledger.request_hash) == 64

    asyncio.run(with_temporary_runtime_database(scenario))


def test_intent_ledger_failure_rolls_back_decision_state_ledger_and_terminal() -> None:
    """ledger owner 失败后，decision/state/ledger/terminal 四者均不得部分提交。"""

    class FailingIdempotencyGuard:
        async def claim_or_match(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("intent ledger failed")

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        seeded, snapshot, service = await _seed_pinned_attempt(session_factory, token="plugin-ledger-failure")
        intent = RuntimeIntent(
            kind=RuntimeIntentKind.COMMAND,
            action="MOVE",
            idempotency_key="operation-1",
            payload_json={"target": "A-01"},
        )
        async with session_factory() as db:
            with pytest.raises(RuntimeError, match="intent ledger failed"):
                await RuntimeInboxWriteBackService(
                    inbox_service=service,
                    idempotency_guard=FailingIdempotencyGuard(),
                ).commit_plugin_attempt(
                    db,
                    expected_snapshot=snapshot,
                    inbox_id=seeded.inbox_id,
                    session_id=seeded.session_id,
                    workline_id=seeded.workline_id,
                    trace_id=seeded.trace_id,
                    write_set=AttemptWriteSet(
                        evidence=(),
                        next_state={"step": 2},
                        intents=(intent,),
                        outcome_code="ROUTE_A",
                    ),
                )

        async with session_factory() as verify_db:
            inbox = await verify_db.get(RuntimeInbox, seeded.inbox_id)
            session = await verify_db.get(WorklineSession, seeded.session_id)
            ledger_count = await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog))
            timeline_count = await verify_db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(WorklineTimeline.related_inbox_id == seeded.inbox_id)
            )
            assert inbox is not None and inbox.status == "PROCESSING"
            assert session is not None and session.plugin_state_json == {}
            assert session.plugin_state_version == snapshot.plugin_state_version
            assert timeline_count == 0
            assert ledger_count == 0

    asyncio.run(with_temporary_runtime_database(scenario))


def test_timeline_advisory_owner_serializes_concurrent_sequence_ranges() -> None:
    """两个 PostgreSQL 事务并发写同一 session 时，seq_no 区间不得重叠。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as seed_db:
            seeded = await seed_scan_flow(seed_db)
            await seed_db.commit()

        owner = TimelineSequenceRepository()

        async def write_pair(actor_code: str) -> tuple[int, ...]:
            async with session_factory() as db:
                seq_nos = await owner.allocate_many(db, session_id=seeded.session_id, count=2)
                for seq_no in seq_nos:
                    db.add(
                        WorklineTimeline(
                            session_id=seeded.session_id,
                            workline_id=seeded.workline_id,
                            trace_id=seeded.trace_id,
                            seq_no=seq_no,
                            occurred_at=timezone.now_for_db(),
                            stage=TimelineStage.DECISION,
                            action_type=TimelineActionType.DECISION_MADE,
                            actor_type=TimelineActorType.PLUGIN,
                            actor_code=actor_code,
                            status=TimelineStatus.SUCCESS,
                            message="concurrent sequence contract",
                            payload_json={"record_type": "TEST_SEQUENCE"},
                        )
                    )
                await asyncio.sleep(0.05)
                await db.commit()
                return seq_nos

        first, second = await asyncio.gather(write_pair("owner-a"), write_pair("owner-b"))
        assert set(first).isdisjoint(second)
        async with session_factory() as verify_db:
            stored = (
                await verify_db.scalars(
                    select(WorklineTimeline.seq_no).where(
                        WorklineTimeline.session_id == seeded.session_id,
                        WorklineTimeline.actor_code.in_(("owner-a", "owner-b")),
                    )
                )
            ).all()
            assert len(stored) == len(set(stored))
            assert sorted(stored) == list(range(min(stored), max(stored) + 1))

    asyncio.run(with_temporary_runtime_database(scenario))
