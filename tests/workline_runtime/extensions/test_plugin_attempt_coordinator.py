from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_write_set_limit_rejection_preserves_fallback_state() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    current_state = {"phase": "WAITING_DEVICE_RESULT", "current_correlation": "CMD-1"}
    bounded = bound_attempt_write_set(
        AttemptWriteSet(evidence=(), next_state={"value": "too-large"}, intents=()),
        limits=PluginWriteSetLimits(max_next_state_bytes=1),
        fallback_state=current_state,
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.next_state == {}
    assert bounded.preserve_plugin_state is True


@pytest.mark.asyncio
async def test_writeback_rechecks_locked_binding_admission_before_persisting() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet
    from src.app.workline.services.plugin_binding_service import PluginBindingAdmissionError

    events: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)

    class Db:
        async def commit(self) -> None:
            events.append("MUST_NOT_COMMIT")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            events.append("lock")
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3, plugin_state_json={"phase": "READY"}),
                plugin_binding=SimpleNamespace(
                    is_enabled=False,
                    is_revoked=False,
                    environment="sandbox",
                    valid_from=None,
                    valid_until=None,
                ),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("MUST_NOT_PERSIST")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_MARK_TERMINAL")
            return True

    with pytest.raises(PluginBindingAdmissionError, match="kill switch"):
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
            write_set=AttemptWriteSet(evidence=(), next_state={"phase": "NEXT"}, intents=()),
        )

    assert events == ["lock", "rollback"]


@pytest.mark.asyncio
async def test_writeback_reloads_device_snapshot_under_shared_workline_lock() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet
    from src.app.workline.services.plugin_binding_service import PluginBindingAdmissionError

    events: list[str] = []
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        binding_id=17,
    )
    binding = SimpleNamespace(
        id=17,
        workline_id=8,
        typed_config_json={"device_roles": {"input_arm": "ROUGH_SORTER_INPUT_ARM"}},
        device_snapshot_json=[
            {
                "device_id": 1,
                "device_code": "RS-IN-01",
                "device_role": "ROUGH_SORTER_INPUT_ARM",
                "workline_id": 8,
                "provider_code": "ECS",
            }
        ],
        is_enabled=True,
        is_revoked=False,
        environment="sandbox",
        valid_from=None,
        valid_until=None,
    )

    class Db:
        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            events.append("authoritative-lock")
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(
                    version=7,
                    plugin_state_version=3,
                    plugin_state_json={"phase": "READY"},
                    plugin_binding_id=17,
                ),
                plugin_binding=binding,
            )

    class WorklineRepository:
        async def acquire_plugin_pin_shared(self, _db: object, workline_id: int) -> None:
            assert workline_id == 8
            events.append("topology-shared-lock")

    class DeviceRepository:
        async def get_by_work_line_id_for_update(self, _db: object, workline_id: int) -> list[SimpleNamespace]:
            assert workline_id == 8
            events.append("topology-reload")
            return [
                SimpleNamespace(
                    id=9,
                    device_code="RS-IN-REPLACEMENT",
                    device_role="ROUGH_SORTER_INPUT_ARM",
                    work_line_id=8,
                    vendor_type="ECS",
                )
            ]

    with pytest.raises(PluginBindingAdmissionError, match="device snapshot"):
        await RuntimeInboxWriteBackService(
            plugin_attempt_repository=Repository(),
            workline_repository=WorklineRepository(),
            device_repository=DeviceRepository(),
        ).commit_plugin_attempt(
            Db(),
            expected_snapshot=snapshot,
            inbox_id=91,
            session_id=41,
            workline_id=8,
            trace_id="trace-1",
            write_set=AttemptWriteSet(evidence=(), next_state={"phase": "NEXT"}, intents=()),
        )

    assert events == ["authoritative-lock", "topology-shared-lock", "topology-reload", "rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typed_config_json",
    [
        {"device_roles": {"input_arm": "ROUGH_SORTER_INPUT_ARM"}},
        {"required_device_codes": ["RS-IN-01"]},
    ],
    ids=["device-roles", "legacy-required-device-codes"],
)
async def test_writeback_device_fact_version_race_returns_safe_retry_without_writes(
    typed_config_json: dict[str, object],
) -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []
    role = "ROUGH_SORTER_INPUT_ARM"
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=3,
        binding_id=17,
        device_fact_versions=((role, 1, 5),),
    )
    binding = SimpleNamespace(
        id=17,
        workline_id=8,
        typed_config_json=typed_config_json,
        device_snapshot_json=[
            {
                "device_id": 1,
                "device_code": "RS-IN-01",
                "device_role": role,
                "workline_id": 8,
                "provider_code": "ECS",
            }
        ],
        is_enabled=True,
        is_revoked=False,
        environment="sandbox",
        valid_from=None,
        valid_until=None,
    )

    class Db:
        async def rollback(self) -> None:
            events.append("rollback")

        async def commit(self) -> None:
            events.append("MUST_NOT_COMMIT")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            events.append("authoritative-lock")
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(
                    version=7,
                    plugin_state_version=3,
                    plugin_state_json={"phase": "READY"},
                    plugin_binding_id=17,
                ),
                plugin_binding=binding,
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("MUST_NOT_PERSIST")

    class WorklineRepository:
        async def acquire_plugin_pin_shared(self, _db: object, workline_id: int) -> None:
            assert workline_id == 8
            events.append("topology-shared-lock")

    class DeviceRepository:
        async def get_by_work_line_id_for_update(self, _db: object, workline_id: int) -> list[SimpleNamespace]:
            assert workline_id == 8
            events.append("device-fact-reload")
            return [
                SimpleNamespace(
                    id=1,
                    version=6,
                    device_code="RS-IN-01",
                    device_role=role,
                    work_line_id=8,
                    vendor_type="ECS",
                )
            ]

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("MUST_NOT_TERMINAL")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
        workline_repository=WorklineRepository(),
        device_repository=DeviceRepository(),
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(evidence=(), next_state={"phase": "NEXT"}, intents=()),
    )

    assert disposition is WriteDisposition.SAFE_RETRY
    assert events == ["authoritative-lock", "topology-shared-lock", "device-fact-reload", "rollback"]


@pytest.mark.asyncio
async def test_device_repository_workline_snapshot_query_requests_row_lock() -> None:
    from src.app.device.repositories.device_repository import DeviceRepository

    devices = [SimpleNamespace(id=1)]

    class Result:
        def scalars(self) -> object:
            return SimpleNamespace(all=lambda: devices)

    class Db:
        statement: object | None = None

        async def execute(self, statement: object) -> Result:
            self.statement = statement
            return Result()

    db = Db()
    result = await DeviceRepository().get_by_work_line_id_for_update(db, 8)  # type: ignore[arg-type]

    assert result == devices
    assert getattr(db.statement, "_for_update_arg", None) is not None
    assert "work_line_id" in str(db.statement)


@pytest.mark.asyncio
async def test_device_repository_locked_snapshot_refreshes_stale_identity_map() -> None:
    from sqlalchemy import event, select, update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.app.device.models import Device, DeviceStatus
    from src.app.device.repositories.device_repository import DeviceRepository
    from src.app.workline.models.workline import WorkLine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")  # type: ignore[attr-defined]

    async with engine.begin() as connection:
        await connection.run_sync(WorkLine.__table__.create)
        await connection.run_sync(Device.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as first, sessions() as second:
        device = Device(
            device_code="RS-IN-STALE",
            device_name="Rough sorter input",
            device_role="ROUGH_SORTER_INPUT_ARM",
            work_line_id=8,
            version=5,
        )
        first.add(device)
        await first.commit()
        cached = await first.scalar(select(Device).where(Device.id == device.id))
        assert cached is device
        assert cached.version == 5

        await second.execute(
            update(Device).where(Device.id == device.id).values(version=6, device_status=DeviceStatus.RUNNING)
        )
        await second.commit()
        assert cached.version == 5

        locked = await DeviceRepository().get_by_work_line_id_for_update(first, 8)

        assert locked == [cached]
        assert locked[0].version == 6
        assert locked[0].device_status is DeviceStatus.RUNNING
    await engine.dispose()


@pytest.mark.parametrize("invalid_number", [float("nan"), float("inf"), float("-inf")])
def test_write_set_rejects_non_finite_numbers_and_preserves_fallback_state(invalid_number: float) -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    current_state = {"phase": "WAITING_DEVICE_RESULT", "current_correlation": "CMD-1"}
    bounded = bound_attempt_write_set(
        AttemptWriteSet(evidence=(), next_state={"invalid": invalid_number}, intents=()),
        limits=PluginWriteSetLimits(),
        fallback_state=current_state,
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.next_state == {}
    assert bounded.preserve_plugin_state is True


def test_write_set_rejects_recursive_container_and_preserves_fallback_state() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    recursive_state: dict[str, object] = {}
    recursive_state["self"] = recursive_state
    current_state = {"phase": "WAITING_DEVICE_RESULT", "current_correlation": "CMD-1"}

    bounded = bound_attempt_write_set(
        AttemptWriteSet(evidence=(), next_state=recursive_state, intents=()),
        limits=PluginWriteSetLimits(),
        fallback_state=current_state,
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.next_state == {}
    assert bounded.preserve_plugin_state is True


@pytest.mark.parametrize(
    ("recorded_decision", "limits"),
    [
        (
            {
                "outcome_code": "ROUTE_A",
                "hold_reason": None,
                "next_state": {"value": "x" * 100},
                "intents": [],
            },
            {"max_next_state_bytes": 40},
        ),
        (
            {
                "outcome_code": "ROUTE_A",
                "hold_reason": None,
                "next_state": {},
                "intents": [{"value": "oversized"}],
            },
            {"max_intent_bytes": 1},
        ),
        (
            {
                "outcome_code": "ROUTE_A",
                "hold_reason": None,
                "next_state": {},
                "intents": [{}, {}],
            },
            {"max_intents": 1},
        ),
        (
            {"outcome_code": "ROUTE_A", "hold_reason": None, "next_state": {"value": float("nan")}, "intents": []},
            {},
        ),
    ],
)
def test_write_set_bounds_recorded_decision_payload_and_clears_it_on_rejection(
    recorded_decision: dict[str, object],
    limits: dict[str, int],
) -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    current_state = {"phase": "WAITING_DEVICE_RESULT"}
    bounded = bound_attempt_write_set(
        AttemptWriteSet(
            evidence=(),
            next_state=current_state,
            intents=(),
            recorded_decision=recorded_decision,
        ),
        limits=PluginWriteSetLimits(**limits),
        fallback_state=current_state,
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.next_state == {}
    assert bounded.preserve_plugin_state is True
    assert bounded.recorded_decision is None
    assert bounded.recorded_attempt_anchor is None


def test_write_set_rejects_recursive_recorded_anchor_and_preserves_fallback_state() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    recursive_anchor: dict[str, object] = {}
    recursive_anchor["self"] = recursive_anchor
    current_state = {"phase": "WAITING_DEVICE_RESULT"}
    bounded = bound_attempt_write_set(
        AttemptWriteSet(
            evidence=(),
            next_state=current_state,
            intents=(),
            recorded_attempt_anchor=recursive_anchor,
        ),
        limits=PluginWriteSetLimits(),
        fallback_state=current_state,
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.next_state == {}
    assert bounded.preserve_plugin_state is True
    assert bounded.recorded_attempt_anchor is None


def test_accepted_write_set_isolated_from_mutation_after_validation() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    next_state = {"phase": "READY"}
    recorded_decision = {
        "outcome_code": "ROUTE_A",
        "hold_reason": None,
        "next_state": {"phase": "RECORDED"},
        "intents": [],
    }
    recorded_anchor = {"source_inbox_id": 91}
    bounded = bound_attempt_write_set(
        AttemptWriteSet(
            evidence=(),
            next_state=next_state,
            intents=(),
            recorded_decision=recorded_decision,
            recorded_attempt_anchor=recorded_anchor,
        ),
        limits=PluginWriteSetLimits(),
        fallback_state={"phase": "WAITING_DEVICE_RESULT"},
    )

    next_state["phase"] = "MUTATED"
    recorded_decision["next_state"] = {"value": float("nan")}
    recorded_anchor["source_inbox_id"] = -1

    assert bounded.next_state == {"phase": "READY"}
    assert bounded.recorded_decision == {
        "outcome_code": "ROUTE_A",
        "hold_reason": None,
        "next_state": {"phase": "RECORDED"},
        "intents": [],
    }
    assert bounded.recorded_attempt_anchor == {"source_inbox_id": 91}


@pytest.mark.parametrize("invalid_state", [[], "invalid", 1])
def test_write_set_rejects_non_object_next_state_without_rewriting_fallback(invalid_state: object) -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    bounded = bound_attempt_write_set(
        AttemptWriteSet(evidence=(), next_state=invalid_state, intents=()),
        limits=PluginWriteSetLimits(),
        fallback_state={"phase": "WAITING_DEVICE_RESULT"},
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.preserve_plugin_state is True
    assert bounded.next_state == {}


def test_write_set_rejection_does_not_copy_invalid_fallback_into_persisted_payload() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    recursive_fallback: dict[str, object] = {}
    recursive_fallback["self"] = recursive_fallback
    bounded = bound_attempt_write_set(
        AttemptWriteSet(evidence=(), next_state={"value": "oversized"}, intents=()),
        limits=PluginWriteSetLimits(max_next_state_bytes=1),
        fallback_state=recursive_fallback,
    )

    assert bounded.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert bounded.preserve_plugin_state is True
    assert bounded.next_state == {}


@pytest.mark.asyncio
async def test_persist_locked_attempt_skips_plugin_state_write_when_preservation_requested() -> None:
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import (
        AuthoritativePluginAttempt,
        PluginAttemptRepository,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    timelines: list[object] = []

    class TimelineOwner:
        async def allocate_many(self, *_args: object, **_kwargs: object) -> tuple[int, ...]:
            return (1,)

    class Db:
        def add(self, row: object) -> None:
            timelines.append(row)

    session = SimpleNamespace(
        id=41,
        plugin_key="rough_sorter",
        business_key="PKG-1",
        plugin_state_json={"phase": "WAITING_DEVICE_RESULT"},
        plugin_state_version=7,
    )
    await PluginAttemptRepository(timeline_sequence_repository=TimelineOwner()).persist_locked_attempt(
        Db(),  # type: ignore[arg-type]
        locked=AuthoritativePluginAttempt(inbox=SimpleNamespace(id=91), session=session),
        workline_id=8,
        trace_id="trace-preserve",
        snapshot=AttemptSnapshot(
            processor_token="lease-1",
            session_version=9,
            plugin_state_version=7,
            session_status="RUNNING",
        ),
        write_set=AttemptWriteSet(
            evidence=(),
            next_state={},
            intents=(),
            outcome_code="HOLD",
            hold_reason="PLUGIN_WRITE_SET_LIMIT_EXCEEDED",
            preserve_plugin_state=True,
        ),
    )

    assert session.plugin_state_json == {"phase": "WAITING_DEVICE_RESULT"}
    assert session.plugin_state_version == 7
    assert timelines[0].payload_json["decision"]["next_state"] == {}  # type: ignore[attr-defined]


def test_live_write_set_cannot_self_authorize_plugin_state_preservation() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    bounded = bound_attempt_write_set(
        AttemptWriteSet(
            evidence=(),
            next_state={"phase": "READY"},
            intents=(),
            outcome_code="ROUTE_A",
            preserve_plugin_state=True,
        ),
        limits=PluginWriteSetLimits(),
        fallback_state={"phase": "WAITING_DEVICE_RESULT"},
    )

    assert bounded.hold_reason is None
    assert bounded.next_state == {"phase": "READY"}
    assert bounded.preserve_plugin_state is False


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
async def test_plugin_attempt_lock_rejects_binding_owned_by_another_workline() -> None:
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import PluginAttemptRepository

    pin = {
        "plugin_key": "rough_sorter",
        "plugin_binding_id": 17,
        "plugin_binding_version": 4,
        "plugin_config_hash": "c" * 64,
        "plugin_index_digest": "d" * 64,
    }

    class InboxRepository:
        async def get_by_id_for_update(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                id=91,
                workline_session_id=41,
                execution_session_id=21,
                correlation_id="corr-1",
                workline_id=8,
            )

    class Db:
        def __init__(self) -> None:
            self.rows = iter(
                (
                    SimpleNamespace(
                        id=41,
                        workline_id=8,
                        contract_version="rough_sorter.v2",
                        current_material_unit_id=None,
                        **pin,
                    ),
                    SimpleNamespace(id=21, workline_id=8, **pin),
                    SimpleNamespace(id=51, execution_session_id=21, correlation_id="corr-1", **pin),
                    SimpleNamespace(
                        id=17,
                        workline_id=9,
                        plugin_key="rough_sorter",
                        contract_version="rough_sorter.v2",
                        binding_version=4,
                        typed_config_hash="c" * 64,
                        generated_index_digest="d" * 64,
                    ),
                )
            )

        async def scalar(self, _statement: object) -> object:
            return next(self.rows)

    locked = await PluginAttemptRepository(inbox_repository=InboxRepository()).lock_authoritative(
        Db(),
        inbox_id=91,
        session_id=41,
    )

    assert locked is None


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
    current_state = {"phase": "WAITING_DEVICE_RESULT", "current_correlation": "CMD-1"}

    class Db:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(
                    version=7,
                    plugin_state_version=3,
                    plugin_state_json=current_state,
                ),
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
    assert bounded.preserve_plugin_state is True
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
