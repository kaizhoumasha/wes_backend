"""插件 attempt 三阶段协调与乐观重校验合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_three_stage_attempt_queries_without_db_then_commits_atomically() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptCoordinator,
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)
    events: list[str] = []

    async def query_phase() -> tuple[str, ...]:
        events.append("query:no-db")
        return ("evidence",)

    async def current_snapshot() -> AttemptSnapshot:
        events.append("revalidate:short-tx")
        return snapshot

    async def writeback(write_set: AttemptWriteSet) -> None:
        assert write_set.evidence == ("evidence",)
        events.append("writeback:atomic")

    disposition = await AttemptCoordinator(snapshot).execute(
        query_phase=query_phase,
        current_snapshot=current_snapshot,
        build_write_set=lambda evidence: AttemptWriteSet(evidence=evidence, next_state={"step": 2}, intents=()),
        writeback=writeback,
    )

    assert disposition is WriteDisposition.COMMITTED
    assert events == ["query:no-db", "revalidate:short-tx", "writeback:atomic"]


@pytest.mark.asyncio
async def test_plugin_attempt_lock_order_matches_session_then_timeline_writers() -> None:
    """Stage3 先锁 Inbox、Session；timeline advisory 只能在两者之后取得。"""

    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import PluginAttemptRepository

    events: list[str] = []

    class TimelineOwner:
        async def acquire_lock(self, _db: object, *, session_id: int) -> None:
            raise AssertionError(f"advisory must not be acquired before row locks: {session_id}")

    class InboxRepository:
        async def get_by_id_for_update(self, *_args: object, **_kwargs: object) -> object:
            events.append("inbox-row")
            return SimpleNamespace(id=91)

    class Db:
        async def scalar(self, _statement: object) -> object:
            events.append("session-row")
            return SimpleNamespace(id=41)

    locked = await PluginAttemptRepository(
        inbox_repository=InboxRepository(),
        timeline_sequence_repository=TimelineOwner(),
    ).lock_authoritative(Db(), inbox_id=91, session_id=41)

    assert locked is not None
    assert events == ["inbox-row", "session-row"]


@pytest.mark.asyncio
async def test_plugin_attempt_persistence_uses_reserved_timeline_sequence_instead_of_local_max() -> None:
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import (
        AuthoritativePluginAttempt,
        PluginAttemptRepository,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    events: list[str] = []
    rows: list[object] = []

    class TimelineOwner:
        async def allocate_many(
            self,
            _db: object,
            *,
            session_id: int,
            count: int,
            lock_already_held: bool,
        ) -> tuple[int, ...]:
            assert (session_id, count, lock_already_held) == (41, 1, False)
            events.append("timeline-reserve")
            return (73,)

    class Db:
        def add(self, row: object) -> None:
            rows.append(row)

    session = SimpleNamespace(id=41, plugin_state_json={}, plugin_state_version=0, version=0)
    await PluginAttemptRepository(
        timeline_sequence_repository=TimelineOwner(),
    ).persist_locked_attempt(
        Db(),
        locked=AuthoritativePluginAttempt(inbox=SimpleNamespace(id=91), session=session),
        workline_id=8,
        trace_id="trace-1",
        snapshot=AttemptSnapshot(processor_token="lease-1", session_version=0, plugin_state_version=0),
        write_set=AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=(), outcome_code="ROUTE_A"),
    )

    assert events == ["timeline-reserve"]
    assert [row.seq_no for row in rows] == [73]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["processor_token", "session_version", "plugin_state_version"])
async def test_revalidation_change_discards_query_result_without_any_write(changed_field: str) -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptCoordinator,
        AttemptSnapshot,
        WriteDisposition,
    )

    original = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)
    changed = {"processor_token": "lease-1", "session_version": 7, "plugin_state_version": 3}
    changed[changed_field] = "lease-2" if changed_field == "processor_token" else changed[changed_field] + 1
    writes: list[object] = []

    async def query_phase() -> tuple[str, ...]:
        return ("evidence",)

    async def current_snapshot() -> AttemptSnapshot:
        return AttemptSnapshot(**changed)

    async def writeback(value: object) -> None:
        writes.append(value)

    disposition = await AttemptCoordinator(original).execute(
        query_phase=query_phase,
        current_snapshot=current_snapshot,
        build_write_set=lambda evidence: (_ for _ in ()).throw(AssertionError(f"must discard {evidence}")),
        writeback=writeback,
    )

    assert disposition is WriteDisposition.SAFE_RETRY
    assert writes == []


@pytest.mark.asyncio
async def test_writeback_persists_evidence_state_intents_and_terminal_in_one_commit() -> None:
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        definition_identity="plugin@v1:" + "a" * 64,
        binding_id=17,
        binding_version=4,
        index_digest="b" * 64,
    )
    write_set = AttemptWriteSet(
        evidence=("e1",),
        next_state={"step": 2},
        intents=("i1",),
        outcome_code="ROUTE_A",
    )
    events: list[str] = []

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, _db: object, *, inbox_id: int, session_id: int) -> object:
            events.append("select-for-update")
            assert (inbox_id, session_id) == (91, 41)
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(
                    version=7,
                    plugin_state_version=3,
                    plugin_identity=snapshot.definition_identity,
                    plugin_binding_id=17,
                    plugin_binding_version=4,
                    plugin_index_digest="b" * 64,
                ),
            )

        async def persist_locked_attempt(self, _db: object, **kwargs: object) -> None:
            assert kwargs["write_set"] == write_set
            events.append("evidence-state")

    class IntentRepository:
        def prepare_attempt_intents(self, **kwargs: object) -> tuple[object, ...]:
            assert kwargs["locked"].inbox.processor_token == "lease-1"
            assert kwargs["snapshot"] == snapshot
            assert kwargs["intents"] == ("i1",)
            return (SimpleNamespace(model="ledger-row", claim={"idempotency_key": "intent-1"}),)

        def add_prepared(self, _db: object, prepared: object) -> None:
            assert prepared.model == "ledger-row"
            events.append("intent-ledger")

    class Guard:
        async def claim_or_match(self, _db: object, **kwargs: object) -> ClaimResult:
            now_ms = kwargs.pop("now_ms")
            assert isinstance(now_ms, int)
            assert kwargs == {"idempotency_key": "intent-1"}
            events.append("intent-claim")
            return ClaimResult.NEW

    class InboxService:
        async def mark_processed(self, _db: object, *, inbox_id: int, lease_token: str) -> bool:
            assert (inbox_id, lease_token) == (91, "lease-1")
            events.append("terminal")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        intent_log_repository=IntentRepository(),
        idempotency_guard=Guard(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=write_set,
    )

    assert disposition is WriteDisposition.COMMITTED
    assert events == ["select-for-update", "evidence-state", "intent-claim", "intent-ledger", "terminal", "commit"]


@pytest.mark.asyncio
async def test_matching_intent_claim_skips_duplicate_ledger_but_commits_terminal() -> None:
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class PluginRepository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("evidence-state")

    class IntentRepository:
        def prepare_attempt_intents(self, **_kwargs: object) -> tuple[object, ...]:
            return (SimpleNamespace(model="MUST_NOT_ADD", claim={"idempotency_key": "intent-1"}),)

        def add_prepared(self, *_args: object) -> None:
            events.append("MUST_NOT_ADD")

    class Guard:
        async def claim_or_match(self, _db: object, **_kwargs: object) -> ClaimResult:
            events.append("intent-match")
            return ClaimResult.MATCH

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("terminal")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=PluginRepository(),
        intent_log_repository=IntentRepository(),
        idempotency_guard=Guard(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(evidence=(), next_state={}, intents=("i1",)),
    )

    assert disposition is WriteDisposition.COMMITTED
    assert events == ["evidence-state", "intent-match", "terminal", "commit"]


@pytest.mark.asyncio
async def test_writeback_bounds_oversized_plugin_payload_before_timeline_and_ledger() -> None:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        PluginWriteSetLimits,
    )

    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)
    persisted: list[object] = []

    class Db:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, _db: object, **kwargs: object) -> None:
            persisted.append(kwargs["write_set"])

    class IntentRepository:
        def prepare_attempt_intents(self, **_kwargs: object) -> tuple[object, ...]:
            raise AssertionError("oversized intent must not reach ledger preparation")

    class InboxService:
        async def mark_failed(self, *_args: object, **kwargs: object) -> bool:
            assert kwargs["error_code"] == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
            return True

    await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        intent_log_repository=IntentRepository(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
        plugin_write_set_limits=PluginWriteSetLimits(max_intent_bytes=1),
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(
            evidence=(),
            next_state={},
            intents=(RuntimeIntent.continue_next(payload={"secret": "must-not-persist"}),),
        ),
    )

    assert len(persisted) == 1
    bounded = persisted[0]
    assert bounded.intents == ()
    assert bounded.next_state == {}
    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert "must-not-persist" not in repr(bounded)


@pytest.mark.asyncio
async def test_recorded_legal_hold_uses_failed_terminal() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)
    terminals: list[str] = []

    class Db:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            pass

    class InboxService:
        async def mark_failed(self, *_args: object, **kwargs: object) -> bool:
            terminals.append(str(kwargs["error_code"]))
            return True

    await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(
            evidence=(),
            next_state={"step": 2},
            intents=(),
            outcome_code="HOLD",
            hold_reason="BUSINESS_RULE_HOLD",
        ),
    )

    assert terminals == ["BUSINESS_RULE_HOLD"]


@pytest.mark.asyncio
async def test_intent_ledger_failure_rolls_back_before_terminal() -> None:
    from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    events: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("evidence-state")

    class IntentRepository:
        def prepare_attempt_intents(self, **_kwargs: object) -> tuple[object, ...]:
            return (SimpleNamespace(model="MUST_NOT_ADD", claim={"idempotency_key": "intent-1"}),)

        def add_prepared(self, *_args: object) -> None:
            events.append("MUST_NOT_ADD")

    class Guard:
        async def claim_or_match(self, _db: object, **_kwargs: object) -> object:
            events.append("intent-conflict")
            raise IdempotencyConflict(
                provider_code="workline-plugin",
                operation_kind="plugin_intent",
                idempotency_key="intent-1",
            )

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_TERMINAL")
            return True

    with pytest.raises(IdempotencyConflict):
        await RuntimeInboxWriteBackService(
            plugin_attempt_repository=Repository(),
            intent_log_repository=IntentRepository(),
            idempotency_guard=Guard(),
            inbox_service=InboxService(),  # type: ignore[arg-type]
        ).commit_plugin_attempt(
            Db(),
            expected_snapshot=snapshot,
            inbox_id=91,
            session_id=41,
            workline_id=8,
            trace_id="trace-1",
            write_set=AttemptWriteSet(evidence=(), next_state={}, intents=("i1",)),
        )

    assert events == ["evidence-state", "intent-conflict", "rollback"]


def test_runtime_intent_owner_builds_stable_ledger_rows_bound_to_attempt_pins() -> None:
    from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
        RuntimeIntentLogRepository,
    )
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot

    class Db:
        def __init__(self) -> None:
            self.rows: list[object] = []

        def add(self, row: object) -> None:
            self.rows.append(row)

    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        definition_identity="plugin@v1:" + "a" * 64,
        binding_id=17,
        binding_version=4,
        index_digest="b" * 64,
    )
    locked = SimpleNamespace(
        inbox=SimpleNamespace(id=91, execution_session_id=71, correlation_id="corr-1"),
    )
    intent = RuntimeIntent(
        kind=RuntimeIntentKind.COMMAND,
        action="MOVE",
        idempotency_key="operation-1",
        payload_json={"target": "A-01"},
    )
    repository = RuntimeIntentLogRepository()
    first_prepared = repository.prepare_attempt_intents(locked=locked, snapshot=snapshot, intents=(intent,))[0]
    second_prepared = repository.prepare_attempt_intents(locked=locked, snapshot=snapshot, intents=(intent,))[0]
    first_db = Db()
    repository.add_prepared(first_db, first_prepared)

    first = first_prepared.model
    second = second_prepared.model
    assert isinstance(first, RuntimeIntentLog)
    assert first.execution_session_id == 71
    assert first.correlation_id == "corr-1"
    assert first.provider_code == "workline-plugin"
    assert first.target_domain == "device"
    assert first.target_action == "MOVE"
    assert first.idempotency_key == "plugin-attempt:binding:17:4:operation-1"
    assert len(first.request_hash) == 64
    assert (first.idempotency_key, first.request_hash) == (second.idempotency_key, second.request_hash)
    assert first_db.rows == [first]
    assert first_prepared.claim["idempotency_key"] == first.idempotency_key
    assert first_prepared.claim["request_hash"] == first.request_hash
    assert first_prepared.claim["execution_correlation_id"] == "corr-1"


@pytest.mark.asyncio
async def test_writeback_version_race_writes_nothing_and_rolls_back() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            events.append("select-for-update")
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=8, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("MUST_NOT_WRITE")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_TERMINAL")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(evidence=("e1",), next_state={}, intents=()),
    )

    assert disposition is WriteDisposition.SAFE_RETRY
    assert events == ["select-for-update", "rollback"]


@pytest.mark.asyncio
async def test_writeback_persistence_error_rolls_back_before_terminal() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    events: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("persist")
            raise RuntimeError("write failed")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_TERMINAL")
            return True

    with pytest.raises(RuntimeError, match="write failed"):
        await RuntimeInboxWriteBackService(
            plugin_attempt_repository=Repository(),
            inbox_service=InboxService(),  # type: ignore[arg-type]
        ).commit_plugin_attempt(
            Db(),
            expected_snapshot=snapshot,
            inbox_id=91,
            session_id=41,
            workline_id=8,
            trace_id="trace-1",
            write_set=AttemptWriteSet(evidence=(), next_state={}, intents=()),
        )

    assert events == ["persist", "rollback"]
