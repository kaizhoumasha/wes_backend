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
async def test_plugin_attempt_lock_order_matches_authoritative_execution_chain() -> None:
    """Stage3 固定按 Inbox、Session、MaterialUnit、ExecutionSession、WorkItem 锁定。"""

    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import PluginAttemptRepository

    events: list[str] = []

    class TimelineOwner:
        async def acquire_lock(self, _db: object, *, session_id: int) -> None:
            raise AssertionError(f"advisory must not be acquired before row locks: {session_id}")

    class InboxRepository:
        async def get_by_id_for_update(self, *_args: object, **_kwargs: object) -> object:
            events.append("inbox-row")
            return SimpleNamespace(
                id=91,
                workline_session_id=41,
                execution_session_id=21,
                correlation_id="corr-1",
                workline_id=8,
            )

    class Db:
        async def scalar(self, _statement: object) -> object:
            rows = (
                (
                    "session-row",
                    SimpleNamespace(
                        id=41,
                        workline_id=8,
                        plugin_key="plugin",
                        current_material_unit_id=31,
                    ),
                ),
                ("material-unit-row", SimpleNamespace(id=31, version=7)),
                ("execution-session-row", SimpleNamespace(id=21, workline_id=8, plugin_key="plugin")),
                (
                    "work-item-row",
                    SimpleNamespace(
                        id=51,
                        execution_session_id=21,
                        correlation_id="corr-1",
                        plugin_key="plugin",
                    ),
                ),
            )
            name, row = rows[len(events) - 1]
            events.append(name)
            return row

    locked = await PluginAttemptRepository(
        inbox_repository=InboxRepository(),
        timeline_sequence_repository=TimelineOwner(),
    ).lock_authoritative(Db(), inbox_id=91, session_id=41)

    assert locked is not None
    assert locked.material_unit.id == 31
    assert events == ["inbox-row", "session-row", "material-unit-row", "execution-session-row", "work-item-row"]


@pytest.mark.asyncio
async def test_plugin_attempt_session_lock_reloads_stale_shared_identity_map() -> None:
    from datetime import timedelta

    from sqlalchemy import event, update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
    from src.app.runtime.orchestration.execution_session import ExecutionSession
    from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
    from src.app.runtime.orchestration.models.material_unit import MaterialUnit
    from src.app.runtime.orchestration.models.session import WorklineSession
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import PluginAttemptRepository
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
    from src.utils.timezone import timezone

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")  # type: ignore[attr-defined]
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_runtime")  # type: ignore[attr-defined]

    async with engine.begin() as connection:
        for table in (
            ExecutionSession.__table__,
            ExecutionCorrelation.__table__,
            MaterialUnit.__table__,
            WorklineSession.__table__,
            RuntimeInbox.__table__,
            ExecutionWorkItem.__table__,
        ):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as first, sessions() as second:
        execution_session = ExecutionSession(
            workline_id=8,
            manifest_version="manifest-v1",
            plugin_key="plugin.rough-sorter",
        )
        first.add(execution_session)
        await first.flush()
        first.add(
            ExecutionCorrelation(
                correlation_id="corr-stale-1",
                execution_session_id=execution_session.id,
                trace_id="trace-stale-1",
            )
        )
        material_unit = MaterialUnit(
            pkg_code="PKG-STALE",
            material_identity_key="MAT:STALE",
            six_in_one={},
            updated_at=timezone.now_for_db(),
        )
        first.add(material_unit)
        await first.flush()
        session = WorklineSession(
            session_code="SESSION-STALE",
            workline_id=8,
            plugin_key="plugin.rough-sorter",
            current_material_unit_id=material_unit.id,
        )
        first.add(session)
        await first.flush()
        first.add(
            ExecutionWorkItem(
                execution_session_id=int(execution_session.id),
                correlation_id="corr-stale-1",
                plugin_key="plugin.rough-sorter",
                object_type="material",
                object_key="material:stale",
                current_step="scan",
            )
        )
        inbox = RuntimeInbox(
            execution_session_id=execution_session.id,
            workline_session_id=session.id,
            correlation_id="corr-stale-1",
            kind="INTERNAL_EVENT",
            workline_id=8,
            provider_code="RUNTIME",
            event_type="SCAN_COMPLETED",
            source_event_id="event-stale-1",
            payload_hash="e" * 64,
            payload_json={"logical_route": "SCAN_COMPLETED"},
            payload_schema_version=1,
            claim_bucket_key="session:stale",
            received_at=timezone.now_for_db(),
        )
        first.add(inbox)
        await first.commit()
        assert session.id is not None
        stale_material_updated_at = material_unit.updated_at
        current_material_updated_at = stale_material_updated_at + timedelta(seconds=1)
        await second.execute(update(WorklineSession).where(WorklineSession.id == session.id).values(version=9))
        await second.execute(
            update(MaterialUnit)
            .where(MaterialUnit.id == material_unit.id)
            .values(updated_at=current_material_updated_at)
        )
        await second.commit()
        assert session.version == 0
        assert material_unit.updated_at == stale_material_updated_at

        locked = await PluginAttemptRepository().lock_authoritative(
            first,
            inbox_id=int(inbox.id),
            session_id=session.id,
        )

        assert locked is not None
        assert locked.session is session
        assert locked.session.version == 9
        assert locked.material_unit is material_unit
        assert locked.material_unit.updated_at == current_material_updated_at
    await engine.dispose()


@pytest.mark.asyncio
async def test_workline_session_ordinary_update_automatically_increments_version() -> None:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.app.runtime.orchestration.models.session import WorklineSession

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")  # type: ignore[attr-defined]

    async with engine.begin() as connection:
        await connection.run_sync(WorklineSession.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        session = WorklineSession(session_code="SESSION-VERSION-LIFECYCLE", workline_id=8, plugin_key="plugin")
        db.add(session)
        await db.commit()
        initial_version = session.version
        session.context_json = {"phase": "running"}
        await db.commit()
        assert session.version == initial_version + 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_workline_session_concurrent_orm_update_raises_stale_data_error() -> None:
    from sqlalchemy import event, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import noload
    from sqlalchemy.orm.exc import StaleDataError

    from src.app.runtime.orchestration.models.session import WorklineSession

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")  # type: ignore[attr-defined]

    async with engine.begin() as connection:
        await connection.run_sync(WorklineSession.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as seed:
        session = WorklineSession(session_code="SESSION-VERSION-CAS", workline_id=8, plugin_key="plugin")
        seed.add(session)
        await seed.commit()
        session_id = int(session.id)
    async with sessions() as first, sessions() as second:
        first_session = await first.scalar(
            select(WorklineSession).where(WorklineSession.id == session_id).options(noload(WorklineSession.workline))
        )
        second_session = await second.scalar(
            select(WorklineSession).where(WorklineSession.id == session_id).options(noload(WorklineSession.workline))
        )
        assert first_session is not None and second_session is not None
        first_session.context_json = {"owner": "first"}
        await first.commit()
        second_session.context_json = {"owner": "second"}
        with pytest.raises(StaleDataError):
            await second.commit()
        await second.rollback()
    await engine.dispose()


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

    class EffectApplier:
        async def apply(self, ctx: dict[str, object], intents: list[object]) -> object:
            assert ctx["session"].version == 7  # type: ignore[union-attr]
            assert ctx["workline"].id == 8  # type: ignore[union-attr]
            assert intents == ["i1"]
            events.append("effect")
            return SimpleNamespace()

    class InboxService:
        async def mark_processed(self, _db: object, *, inbox_id: int, lease_token: str) -> bool:
            assert (inbox_id, lease_token) == (91, "lease-1")
            events.append("terminal")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        intent_log_repository=IntentRepository(),
        idempotency_guard=Guard(),
        effect_applier=EffectApplier(),
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
    assert events == [
        "select-for-update",
        "intent-claim",
        "intent-ledger",
        "effect",
        "evidence-state",
        "terminal",
        "commit",
    ]


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

    class MatchingEffectApplier:
        async def apply(self, *_args: object, **_kwargs: object) -> object:
            events.append("effect")
            return SimpleNamespace()

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=PluginRepository(),
        intent_log_repository=IntentRepository(),
        idempotency_guard=Guard(),
        effect_applier=MatchingEffectApplier(),
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
    assert events == ["intent-match", "effect", "evidence-state", "terminal", "commit"]


@pytest.mark.asyncio
async def test_plugin_effect_failure_rolls_back_before_state_and_terminal() -> None:
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    events: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)

    class Db:
        async def commit(self) -> None:
            events.append("MUST_NOT_COMMIT")

        async def rollback(self) -> None:
            events.append("rollback")

    class PluginRepository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("MUST_NOT_PERSIST")

    class IntentRepository:
        def prepare_attempt_intents(self, **_kwargs: object) -> tuple[object, ...]:
            return (SimpleNamespace(model=object(), claim={}),)

        def add_prepared(self, *_args: object) -> None:
            events.append("provisional-ledger")

    class Guard:
        async def claim_or_match(self, *_args: object, **_kwargs: object) -> ClaimResult:
            return ClaimResult.NEW

    class EffectApplier:
        async def apply(self, *_args: object, **_kwargs: object) -> object:
            events.append("effect")
            raise RuntimeError("effect failed")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_TERMINAL")
            return True

    with pytest.raises(RuntimeError, match="effect failed"):
        await RuntimeInboxWriteBackService(
            plugin_attempt_repository=PluginRepository(),
            intent_log_repository=IntentRepository(),
            idempotency_guard=Guard(),
            effect_applier=EffectApplier(),
            inbox_service=InboxService(),  # type: ignore[arg-type]
        ).commit_plugin_attempt(
            Db(),
            expected_snapshot=snapshot,
            inbox_id=91,
            session_id=41,
            workline_id=8,
            trace_id="trace-1",
            write_set=AttemptWriteSet(evidence=(), next_state={}, intents=("intent",)),
        )

    assert events == ["provisional-ledger", "effect", "rollback"]


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

    assert events == ["intent-conflict", "rollback"]


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
        result_policy="COMMAND_RESULT",
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


def test_runtime_intent_ledger_admission_revalidates_model_copy_result_policy_bypass() -> None:
    from pydantic import ValidationError

    from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import RuntimeIntentLogRepository
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot

    bypassed = RuntimeIntent.command(action="MOVE", result_policy="COMMAND_RESULT").model_copy(
        update={"result_policy": None}
    )
    locked = SimpleNamespace(inbox=SimpleNamespace(id=91, execution_session_id=71, correlation_id="corr-1"))
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        definition_identity="plugin@v1:" + "a" * 64,
        binding_id=17,
        binding_version=4,
        index_digest="b" * 64,
    )

    with pytest.raises(ValidationError, match="COMMAND intent requires result_policy"):
        RuntimeIntentLogRepository().prepare_attempt_intents(
            locked=locked,
            snapshot=snapshot,
            intents=(bypassed,),
        )


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
async def test_writeback_material_fact_version_race_writes_nothing_and_rolls_back() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        material_unit_id=51,
        material_unit_version=11,
    )

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
                session=SimpleNamespace(
                    version=7,
                    plugin_state_version=3,
                    current_material_unit_id=51,
                ),
                material_unit=SimpleNamespace(id=51, version=12),
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
        workline_id=21,
        trace_id="trace-1",
        write_set=AttemptWriteSet(evidence=(), next_state={"phase": "READY"}, intents=()),
    )

    assert disposition is WriteDisposition.SAFE_RETRY
    assert events == ["select-for-update", "rollback"]


@pytest.mark.asyncio
async def test_writeback_plugin_config_drift_writes_nothing_and_rolls_back() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        plugin_config_hash="a" * 64,
    )

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
                session=SimpleNamespace(
                    version=7,
                    plugin_state_version=3,
                    plugin_config_hash="b" * 64,
                ),
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
