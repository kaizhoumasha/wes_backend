"""Legacy drain 的双样本 fail-closed 判定。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest


def _contract():
    try:
        from src.app.runtime.orchestration.repositories.legacy_drain_readiness_repository import (
            LEGACY_DRAIN_COUNT_KEYS,
            LegacyDrainDatabaseSnapshot,
        )
        from src.app.runtime.orchestration.services.query.legacy_drain_readiness_service import (
            CeleryLegacyTaskInspector,
            LegacyDrainReadinessQueryError,
            LegacyDrainReadinessService,
        )
    except ModuleNotFoundError:
        pytest.fail("legacy drain read-only owner is missing", pytrace=False)
    return (
        LEGACY_DRAIN_COUNT_KEYS,
        LegacyDrainDatabaseSnapshot,
        CeleryLegacyTaskInspector,
        LegacyDrainReadinessQueryError,
        LegacyDrainReadinessService,
    )


class _Session:
    def __init__(self) -> None:
        self.rollback_count = 0
        self.commit_count = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def commit(self) -> None:
        self.commit_count += 1


class _SessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session()
        self.sessions.append(session)
        return session


class _Repository:
    def __init__(self, snapshots: list[object] | None = None, error: Exception | None = None) -> None:
        self._snapshots = list(snapshots or [])
        self._error = error

    async def load_snapshot(self, _db: object, *, producer_freeze_at: datetime) -> object:
        assert producer_freeze_at == datetime(2026, 8, 29, 12, tzinfo=UTC)
        if self._error is not None:
            raise self._error
        return self._snapshots.pop(0)


class _BrokerInspector:
    def __init__(self, snapshots: list[object] | None = None, error: Exception | None = None) -> None:
        self._snapshots = list(snapshots or [])
        self._error = error

    async def inspect(
        self,
        *,
        legacy_task_names: tuple[str, ...],
        required_worker_nodes: tuple[str, ...],
    ) -> object:
        assert legacy_task_names == ("legacy.task",)
        assert required_worker_nodes == ("celery@worker-general", "celery@worker-fulfillment")
        if self._error is not None:
            raise self._error
        return self._snapshots.pop(0)


async def _no_wait(_seconds: float) -> None:
    return None


def _database_snapshot(
    *,
    counts: dict[str, int] | None = None,
    watermarks: dict[str, tuple[int, int | None]] | None = None,
    investigations: tuple[dict[str, object], ...] = (),
):
    count_keys, snapshot_type, *_ = _contract()
    values = dict.fromkeys(count_keys, 0)
    values.update(counts or {})
    return snapshot_type(
        counts=values,
        watermarks=watermarks or {"runtime_inbox": (0, None)},
        investigations=investigations,
    )


def _broker_snapshot(*, active: int = 0, reserved: int = 0, scheduled: int = 0, investigations=()):
    return SimpleNamespace(
        counts={"active": active, "reserved": reserved, "scheduled": scheduled},
        investigations=tuple(investigations),
    )


@pytest.mark.asyncio
async def test_two_stable_zero_samples_are_ready_and_every_database_transaction_rolls_back() -> None:
    *_, service_type = _contract()
    factory = _SessionFactory()
    service = service_type(
        repository=_Repository([_database_snapshot(), _database_snapshot()]),
        broker_inspector=_BrokerInspector([_broker_snapshot(), _broker_snapshot()]),
        sleep=_no_wait,
    )

    result = await service.check(
        session_factory=factory,
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        interval_seconds=0,
        legacy_task_names=("legacy.task",),
        required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
    )

    assert result.state == "READY"
    assert result.stable_zero_observations == 2
    assert result.wait_drain_total == 0
    assert result.block_total == 0
    assert len(factory.sessions) == 2
    assert all(session.rollback_count == 1 and session.commit_count == 0 for session in factory.sessions)


@pytest.mark.asyncio
async def test_processable_rows_and_legacy_broker_tasks_wait_for_drain() -> None:
    *_, service_type = _contract()
    service = service_type(
        repository=_Repository(
            [
                _database_snapshot(counts={"runtime_inbox_processable": 1}),
                _database_snapshot(counts={"runtime_inbox_processable": 1}),
            ]
        ),
        broker_inspector=_BrokerInspector([_broker_snapshot(active=1), _broker_snapshot(reserved=1, scheduled=1)]),
        sleep=_no_wait,
    )

    result = await service.check(
        session_factory=_SessionFactory(),
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        interval_seconds=0,
        legacy_task_names=("legacy.task",),
        required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
    )

    assert result.state == "WAIT_DRAIN"
    assert result.wait_drain_total == 3
    assert result.block_total == 0
    assert result.stable_zero_observations == 0


@pytest.mark.asyncio
async def test_first_nonzero_then_zero_sample_still_waits_for_second_consecutive_zero() -> None:
    *_, service_type = _contract()
    service = service_type(
        repository=_Repository(
            [
                _database_snapshot(counts={"runtime_inbox_processable": 1}),
                _database_snapshot(),
            ]
        ),
        broker_inspector=_BrokerInspector([_broker_snapshot(), _broker_snapshot()]),
        sleep=_no_wait,
    )

    result = await service.check(
        session_factory=_SessionFactory(),
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        interval_seconds=0,
        legacy_task_names=("legacy.task",),
        required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
    )

    assert result.state == "WAIT_DRAIN"
    assert result.stable_zero_observations == 1
    assert result.wait_drain_total == 1
    assert result.counts["legacy_stability_observation_wait"] == 1


@pytest.mark.asyncio
async def test_identity_conflict_blocks_and_preserves_only_original_identity_for_manual_investigation() -> None:
    *_, service_type = _contract()
    investigation = {
        "kind": "system_outbox_identity_digest_conflict",
        "dispatch_key": "dispatch-original",
        "intent_id": 41,
        "outbox_id": 73,
        "intent_operation_identity": "wms.inventory.confirm_inbound@v1",
        "outbox_operation_identity": "wms.inventory.confirm_outbound@v1",
        "intent_idempotency_key": "intent-idem-original",
        "outbox_idempotency_key": "outbox-idem-original",
        "intent_digest": "a" * 64,
        "outbox_digest": "b" * 64,
    }
    snapshot = _database_snapshot(
        counts={"system_outbox_identity_digest_conflict": 1},
        investigations=(investigation,),
    )
    service = service_type(
        repository=_Repository([snapshot, snapshot]),
        broker_inspector=_BrokerInspector([_broker_snapshot(), _broker_snapshot()]),
        sleep=_no_wait,
    )

    result = await service.check(
        session_factory=_SessionFactory(),
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        interval_seconds=0,
        legacy_task_names=("legacy.task",),
        required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
    )

    assert result.state == "BLOCK"
    assert result.block_total == 1
    assert result.manual_investigations == (investigation,)
    assert all(
        forbidden not in result.manual_investigations[0]
        for forbidden in ("resolve", "cancel", "retry", "resend", "cleanup")
    )


@pytest.mark.asyncio
async def test_new_legacy_row_between_samples_blocks_even_when_both_predicates_are_zero() -> None:
    *_, service_type = _contract()
    service = service_type(
        repository=_Repository(
            [
                _database_snapshot(watermarks={"runtime_inbox": (7, 12)}),
                _database_snapshot(watermarks={"runtime_inbox": (8, 13)}),
            ]
        ),
        broker_inspector=_BrokerInspector([_broker_snapshot(), _broker_snapshot()]),
        sleep=_no_wait,
    )

    result = await service.check(
        session_factory=_SessionFactory(),
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        interval_seconds=0,
        legacy_task_names=("legacy.task",),
        required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
    )

    assert result.state == "BLOCK"
    assert result.counts["legacy_row_watermark_growth_block"] == 1
    assert result.stable_zero_observations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_owner", ["database", "broker"])
async def test_query_or_broker_inspection_failure_is_fail_closed(failure_owner: str) -> None:
    *_, query_error, service_type = _contract()
    repository = _Repository([_database_snapshot(), _database_snapshot()])
    broker = _BrokerInspector([_broker_snapshot(), _broker_snapshot()])
    if failure_owner == "database":
        repository = _Repository(error=RuntimeError("database unavailable"))
    else:
        broker = _BrokerInspector(error=RuntimeError("broker unavailable"))
    service = service_type(repository=repository, broker_inspector=broker, sleep=_no_wait)

    with pytest.raises(query_error):
        await service.check(
            session_factory=_SessionFactory(),
            producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
            interval_seconds=0,
            legacy_task_names=("legacy.task",),
            required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
        )


@pytest.mark.asyncio
async def test_celery_inspector_filters_shared_queues_by_exact_legacy_task_identity() -> None:
    _, _, inspector_type, *_ = _contract()

    class _Inspect:
        def active(self):
            return {
                "celery@worker-general": [
                    {"id": "active-1", "name": "legacy.task"},
                    {"id": "target-1", "name": "target.task"},
                ],
                "celery@worker-fulfillment": [],
            }

        def reserved(self):
            return {
                "celery@worker-general": [{"id": "reserved-1", "name": "legacy.task"}],
                "celery@worker-fulfillment": [],
            }

        def scheduled(self):
            return {
                "celery@worker-general": [],
                "celery@worker-fulfillment": [{"request": {"id": "scheduled-1", "name": "legacy.task"}}],
            }

    app = SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout: _Inspect()))
    observed = await inspector_type(app=app, timeout_seconds=5).inspect(
        legacy_task_names=("legacy.task",),
        required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
    )

    assert observed.counts == {"active": 1, "reserved": 1, "scheduled": 1}
    assert observed.investigations == (
        {
            "kind": "celery_active",
            "task_name": "legacy.task",
            "task_id": "active-1",
            "worker": "celery@worker-general",
        },
        {
            "kind": "celery_reserved",
            "task_name": "legacy.task",
            "task_id": "reserved-1",
            "worker": "celery@worker-general",
        },
        {
            "kind": "celery_scheduled",
            "task_name": "legacy.task",
            "task_id": "scheduled-1",
            "worker": "celery@worker-fulfillment",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "empty_state",
    ["active", "reserved", "scheduled"],
)
async def test_celery_inspector_rejects_an_empty_required_worker_state(empty_state: str) -> None:
    _, _, inspector_type, *_ = _contract()
    required_nodes = ("celery@worker-general", "celery@worker-fulfillment")

    class _Inspect:
        def active(self):
            return {} if empty_state == "active" else {node: [] for node in required_nodes}

        def reserved(self):
            return {} if empty_state == "reserved" else {node: [] for node in required_nodes}

        def scheduled(self):
            return {} if empty_state == "scheduled" else {node: [] for node in required_nodes}

    app = SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout: _Inspect()))
    with pytest.raises(RuntimeError, match="required worker nodes"):
        await inspector_type(app=app, timeout_seconds=5).inspect(
            legacy_task_names=("legacy.task",),
            required_worker_nodes=required_nodes,
        )


@pytest.mark.asyncio
async def test_celery_inspector_rejects_partial_or_unrelated_worker_responses() -> None:
    _, _, inspector_type, *_ = _contract()

    class _Inspect:
        def active(self):
            return {"celery@worker-general": [], "celery@unrelated": []}

        def reserved(self):
            return {"celery@worker-general": [], "celery@worker-fulfillment": []}

        def scheduled(self):
            return {"celery@worker-general": [], "celery@worker-fulfillment": []}

    app = SimpleNamespace(control=SimpleNamespace(inspect=lambda timeout: _Inspect()))
    with pytest.raises(RuntimeError, match="required worker nodes"):
        await inspector_type(app=app, timeout_seconds=5).inspect(
            legacy_task_names=("legacy.task",),
            required_worker_nodes=("celery@worker-general", "celery@worker-fulfillment"),
        )


@pytest.mark.asyncio
async def test_repository_parses_one_read_only_sql_snapshot_without_lock_or_mutation() -> None:
    count_keys, snapshot_type, *_ = _contract()
    from src.app.runtime.orchestration.repositories.legacy_drain_readiness_repository import (
        LegacyDrainReadinessRepository,
    )

    counts = dict.fromkeys(count_keys, 0)
    counts["runtime_inbox_processable"] = 2
    payload = {
        "counts": counts,
        "watermarks": {"runtime_inbox": {"rows": 7, "max_id": 12}},
        "investigations": [
            {
                "kind": "runtime_intent_ambiguous",
                "dispatch_key": "dispatch-12",
                "operation_identity": "legacy.operation@v1",
            }
        ],
    }

    class _Result:
        def scalar_one(self) -> object:
            return payload

    class _Database:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement: object, _params: object = None) -> object:
            rendered = str(statement)
            self.statements.append(rendered)
            return _Result()

    db = _Database()
    observed = await LegacyDrainReadinessRepository().load_snapshot(
        db,
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
    )

    assert isinstance(observed, snapshot_type)
    assert observed.counts["runtime_inbox_processable"] == 2
    assert observed.watermarks == {"runtime_inbox": (7, 12)}
    assert observed.investigations[0]["dispatch_key"] == "dispatch-12"
    selects = [statement for statement in db.statements if statement.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(selects) == 1
    assert "SET TRANSACTION READ ONLY" in db.statements
    assert any("statement_timeout" in statement for statement in db.statements)
    normalized = " ".join(selects[0].upper().split())
    for table in (
        "WES_RUNTIME.RUNTIME_INBOX",
        "WES_RUNTIME.RUNTIME_INTENT_LOGS",
        "WES_BIZ.SYSTEM_OUTBOX",
        "WES_RUNTIME.RUNTIME_HOLDS",
        "WES_BIZ.RUNTIME_HOLDS",
        "WES_BIZ.NG_RETURN_ITEMS",
        "WES_RUNTIME.RECONCILIATION_CASES",
        "WES_BIZ.WORKLINE_BIN_CELL_RESERVATIONS",
    ):
        assert table in normalized
    assert not any(token in normalized for token in (" FOR UPDATE", " INSERT ", " UPDATE ", " DELETE ", " LOCK "))
