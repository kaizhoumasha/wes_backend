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
        async def persist_attempt_intents(self, _db: object, **kwargs: object) -> None:
            assert kwargs["locked"].inbox.processor_token == "lease-1"
            assert kwargs["snapshot"] == snapshot
            assert kwargs["intents"] == ("i1",)
            events.append("intent-ledger")

    class InboxService:
        async def mark_processed(self, _db: object, *, inbox_id: int, lease_token: str) -> bool:
            assert (inbox_id, lease_token) == (91, "lease-1")
            events.append("terminal")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        intent_log_repository=IntentRepository(),
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
    assert events == ["select-for-update", "evidence-state", "intent-ledger", "terminal", "commit"]


@pytest.mark.asyncio
async def test_intent_ledger_failure_rolls_back_before_terminal() -> None:
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
        async def persist_attempt_intents(self, *_args: object, **_kwargs: object) -> None:
            events.append("intent-ledger")
            raise RuntimeError("intent ledger failed")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_TERMINAL")
            return True

    with pytest.raises(RuntimeError, match="intent ledger failed"):
        await RuntimeInboxWriteBackService(
            plugin_attempt_repository=Repository(),
            intent_log_repository=IntentRepository(),
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

    assert events == ["evidence-state", "intent-ledger", "rollback"]


@pytest.mark.asyncio
async def test_runtime_intent_owner_builds_stable_ledger_rows_bound_to_attempt_pins() -> None:
    from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
        RuntimeIntentLogRepository,
    )
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
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
    first_db = Db()
    second_db = Db()
    claims: list[dict[str, object]] = []

    class Guard:
        async def claim_or_match(self, _db: object, **kwargs: object) -> ClaimResult:
            claims.append(kwargs)
            return ClaimResult.NEW

    repository = RuntimeIntentLogRepository(idempotency_guard=Guard())

    await repository.persist_attempt_intents(first_db, locked=locked, snapshot=snapshot, intents=(intent,))
    await repository.persist_attempt_intents(second_db, locked=locked, snapshot=snapshot, intents=(intent,))

    first = first_db.rows[0]
    second = second_db.rows[0]
    assert isinstance(first, RuntimeIntentLog)
    assert first.execution_session_id == 71
    assert first.correlation_id == "corr-1"
    assert first.provider_code == "workline-plugin"
    assert first.target_domain == "device"
    assert first.target_action == "MOVE"
    assert first.idempotency_key == "plugin-attempt:binding:17:4:operation-1"
    assert len(first.request_hash) == 64
    assert (first.idempotency_key, first.request_hash) == (second.idempotency_key, second.request_hash)
    assert claims[0]["idempotency_key"] == first.idempotency_key
    assert claims[0]["request_hash"] == first.request_hash
    assert claims[0]["execution_correlation_id"] == "corr-1"


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
