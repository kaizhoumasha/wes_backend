"""插件 binding 在真实 Session 创建与 RuntimeInbox retry 入口的接线测试。"""

from __future__ import annotations

import importlib
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_context_loader as context_loader
from src.app.runtime.orchestration.services.session.session_resolver import SessionResolver
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
from src.app.workline.models import LineType, WorkLine
from src.app.workline.repositories.plugin_binding_repository import WorklinePluginBindingRepository
from src.app.workline.services.plugin_binding_service import (
    PluginBindingAdmissionError,
    WorklinePluginBindingService,
)


class Config(BaseModel):
    provider_code: str = "WMS"


class State(BaseModel):
    phase: str = "READY"


def parse(payload: object) -> object:
    return payload


DEFINITION = WorklinePluginDefinition(
    plugin_key="platform-test",
    contract_version="v1",
    config_model=Config,
    state_model=State,
    routes=("SCAN",),
    allowed_capabilities=(),
    parsers={"SCAN": parse},
)


def _workline() -> WorkLine:
    workline = WorkLine(
        line_code="PLATFORM-01",
        line_name="Platform",
        line_type=LineType.AUTO,
        plugin_key="platform-test",
        contract_version="v1",
        config={},
        active_plugin_binding_id=8,
        active_plugin_binding_version=2,
        active_plugin_config_hash="a" * 64,
        active_plugin_index_digest="b" * 64,
    )
    workline.id = 7
    workline.version = 3
    workline.is_active = True
    return workline


def _binding(
    *,
    binding_id: int = 8,
    binding_version: int = 2,
    config_hash: str = "a" * 64,
    index_digest: str = "b" * 64,
    enabled: bool = True,
    revoked: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=binding_id,
        workline_id=7,
        plugin_key="platform-test",
        contract_version="v1",
        binding_version=binding_version,
        typed_config_hash=config_hash,
        generated_index_digest=index_digest,
        environment="sandbox",
        valid_from=datetime(2026, 7, 17, 8),
        valid_until=None,
        is_enabled=enabled,
        is_revoked=revoked,
    )


class BindingRepository:
    def __init__(self, binding: SimpleNamespace | None = None) -> None:
        self.binding = binding or _binding()
        self.get_calls: list[int] = []

    async def get_pinned(self, _db: object, binding_id: int) -> SimpleNamespace | None:
        self.get_calls.append(binding_id)
        return self.binding if binding_id == self.binding.id else None


class RuntimeRepository:
    def __init__(self) -> None:
        self.created: tuple[ExecutionSession, ExecutionCorrelation, ExecutionWorkItem] | None = None

    async def save_pinned_runtime_aggregate(
        self,
        _db: object,
        *,
        execution_session: ExecutionSession,
        correlation: ExecutionCorrelation,
        work_item: ExecutionWorkItem,
    ) -> tuple[ExecutionSession, ExecutionWorkItem]:
        self.created = (execution_session, correlation, work_item)
        return execution_session, work_item


class SessionRepository:
    async def get_open_session_by_business_key(self, **_kwargs: object) -> None:
        return None

    async def get_by_trace_id(self, **_kwargs: object) -> None:
        return None

    async def get_latest_session_by_business_key(self, **_kwargs: object) -> None:
        return None

    async def create(self, _db: object, data: dict[str, Any]) -> WorklineSession:
        session = WorklineSession(**data)
        session.id = 21
        return session


class WorklineRepository:
    def __init__(self, workline: WorkLine) -> None:
        self.workline = workline
        self.pin_events: list[str] = []

    async def get_for_update(self, _db: object, _workline_id: int, *, populate_existing: bool = False) -> WorkLine:
        self.pin_events.append("for_update")
        _ = populate_existing
        return self.workline

    async def acquire_plugin_pin_shared(self, _db: object, _workline_id: int) -> None:
        self.pin_events.append("shared")

    async def get_current_plugin_pin(
        self,
        _db: object,
        _workline_id: int,
        *,
        populate_existing: bool = False,
    ) -> WorkLine:
        assert populate_existing is True
        self.pin_events.append("current")
        return self.workline


@pytest.mark.asyncio
async def test_existing_session_reuse_does_not_acquire_workline_plugin_pin_lock() -> None:
    workline = _workline()
    existing = WorklineSession(
        id=21,
        session_code="SESSION-21",
        workline_id=7,
        plugin_key="platform-test",
        contract_version="v1",
        business_key="PKG-EXISTING",
        trace_id="trace-existing",
        ingress_count=1,
    )

    class ExistingSessionRepository(SessionRepository):
        async def get_open_session_by_business_key(self, **_kwargs: object) -> WorklineSession:
            return existing

    workline_repository = WorklineRepository(workline)
    binding_repository = BindingRepository()
    resolver = SessionResolver(
        session_repo=ExistingSessionRepository(),
        workline_repo=workline_repository,
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=WorklinePluginBindingService(
            repository=binding_repository,
            plugin_index={("platform-test", "v1"): DEFINITION},
            capability_index={},
            plugin_index_digest="b" * 64,
        ),
    )
    inbox = SimpleNamespace(
        payload_json={"business_key": "PKG-EXISTING"},
        device_id=1,
        trace_id="trace-inbox",
        request_id="request-existing",
        source_message_id="message-existing",
    )

    resolved = await resolver._resolve_device_event(object(), inbox, workline)

    assert resolved is existing
    assert workline_repository.pin_events == []
    assert binding_repository.get_calls == []


@pytest.mark.asyncio
async def test_new_session_acquires_shared_plugin_pin_lock_before_refreshing_current_pin() -> None:
    workline = _workline()
    workline_repository = WorklineRepository(workline)
    resolver = SessionResolver(
        session_repo=SessionRepository(),
        workline_repo=workline_repository,
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=WorklinePluginBindingService(
            repository=BindingRepository(),
            runtime_repository=RuntimeRepository(),
            plugin_index={("platform-test", "v1"): DEFINITION},
            capability_index={},
            plugin_index_digest="b" * 64,
            clock=lambda: datetime(2026, 7, 17, 9),
        ),
    )
    inbox = SimpleNamespace(
        payload_json={"business_key": "PKG-NEW"},
        device_id=1,
        trace_id="trace-new",
        request_id="request-new",
        source_message_id="message-new",
    )

    await resolver._resolve_device_event(object(), inbox, workline)

    assert workline_repository.pin_events == ["shared", "current"]


@pytest.mark.asyncio
async def test_new_session_rejects_workline_deactivated_before_shared_lock_reload() -> None:
    stale_workline = _workline()
    stale_workline.is_active = True
    current_workline = _workline()
    current_workline.is_active = False

    class DeactivatedWorklineRepository(WorklineRepository):
        async def get_current_plugin_pin(
            self,
            _db: object,
            _workline_id: int,
            *,
            populate_existing: bool = False,
        ) -> WorkLine:
            assert populate_existing is True
            self.pin_events.append("current")
            return current_workline

    class TrackingSessionRepository(SessionRepository):
        def __init__(self) -> None:
            self.create_calls = 0

        async def create(self, _db: object, data: dict[str, Any]) -> WorklineSession:
            self.create_calls += 1
            return await super().create(_db, data)

    session_repository = TrackingSessionRepository()
    workline_repository = DeactivatedWorklineRepository(stale_workline)
    resolver = SessionResolver(
        session_repo=session_repository,
        workline_repo=workline_repository,
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=WorklinePluginBindingService(
            repository=BindingRepository(),
            runtime_repository=RuntimeRepository(),
            plugin_index={("platform-test", "v1"): DEFINITION},
            capability_index={},
            plugin_index_digest="b" * 64,
            clock=lambda: datetime(2026, 7, 17, 9),
        ),
    )
    inbox = SimpleNamespace(
        payload_json={"business_key": "PKG-DEACTIVATED"},
        device_id=1,
        trace_id="trace-deactivated",
        request_id="request-deactivated",
        source_message_id="message-deactivated",
    )

    with pytest.raises(RuntimeError, match="不再接收新工作"):
        await resolver._resolve_device_event(object(), inbox, stale_workline)

    assert workline_repository.pin_events == ["shared", "current"]
    assert session_repository.create_calls == 0


@pytest.mark.asyncio
async def test_session_resolver_pins_real_models_and_creates_runtime_aggregate_in_same_entry() -> None:
    workline = _workline()
    runtime_repository = RuntimeRepository()
    binding_service = WorklinePluginBindingService(
        repository=BindingRepository(),
        runtime_repository=runtime_repository,
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
        clock=lambda: datetime(2026, 7, 17, 9),
    )
    resolver = SessionResolver(
        session_repo=SessionRepository(),
        workline_repo=WorklineRepository(workline),
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=binding_service,
    )
    inbox = SimpleNamespace(
        payload_json={"business_key": "PKG-1"},
        device_id=1,
        trace_id="trace-1",
        request_id="request-1",
        source_message_id="message-1",
    )

    session = await resolver._resolve_device_event(object(), inbox, workline)

    assert isinstance(session, WorklineSession)
    assert session.plugin_binding_id == 8
    assert session.plugin_state_json == {"phase": "READY"}
    assert runtime_repository.created is not None
    execution_session, correlation, work_item = runtime_repository.created
    assert isinstance(execution_session, ExecutionSession)
    assert isinstance(correlation, ExecutionCorrelation)
    assert isinstance(work_item, ExecutionWorkItem)
    assert correlation.correlation_id == work_item.correlation_id
    for record in (execution_session, work_item):
        assert record.plugin_key == "platform-test"
        assert record.plugin_binding_id == session.plugin_binding_id
        assert record.plugin_config_hash == session.plugin_config_hash
        assert record.plugin_index_digest == session.plugin_index_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft",
    [
        {"plugin_key": "draft-plugin"},
        {"contract_version": "draft-v2"},
        {"plugin_key": "draft-plugin", "contract_version": "draft-v2"},
    ],
)
async def test_unapproved_active_draft_identity_never_changes_new_session_binding_pin(
    draft: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_resolver_module = importlib.import_module("src.app.runtime.orchestration.services.session.session_resolver")

    monkeypatch.setattr(
        session_resolver_module,
        "resolve_workline_business_key",
        lambda plugin_key, _payload, *, contract_version=None: f"BUSINESS-{plugin_key}",
    )
    runtime_repository = RuntimeRepository()
    binding_repository = BindingRepository()
    binding_service = WorklinePluginBindingService(
        repository=binding_repository,
        runtime_repository=runtime_repository,
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
        clock=lambda: datetime(2026, 7, 17, 9),
    )
    workline = _workline()
    for field, value in draft.items():
        setattr(workline, field, value)
    resolver = SessionResolver(
        session_repo=SessionRepository(),
        workline_repo=WorklineRepository(workline),
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=binding_service,
    )
    inbox = SimpleNamespace(
        payload_json={"business_key": "PKG-DRAFT"},
        device_id=1,
        trace_id="trace-draft",
        request_id="request-draft",
        source_message_id="message-draft",
    )

    session = await resolver._resolve_device_event(object(), inbox, workline)

    assert binding_repository.get_calls == [8]
    assert (session.plugin_key, session.contract_version, session.business_key) == (
        "platform-test",
        "v1",
        "BUSINESS-platform-test",
    )
    assert session.plugin_binding_id == 8
    assert runtime_repository.created is not None
    execution_session, _, work_item = runtime_repository.created
    assert (execution_session.plugin_key, execution_session.manifest_version) == ("platform-test", "v1")
    assert work_item.plugin_key == "platform-test"


@pytest.mark.asyncio
async def test_session_resolver_uses_binding_contract_version_for_plugin_business_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_resolver_module = importlib.import_module("src.app.runtime.orchestration.services.session.session_resolver")
    seen: list[tuple[str | None, str | None]] = []

    def resolve_business_key(
        plugin_key: str | None,
        _payload: dict[str, object],
        *,
        contract_version: str | None = None,
    ) -> str | None:
        seen.append((plugin_key, contract_version))
        if plugin_key == "platform-test" and contract_version == "v1":
            return "BUSINESS-v1"
        return None

    monkeypatch.setattr(session_resolver_module, "resolve_workline_business_key", resolve_business_key)
    runtime_repository = RuntimeRepository()
    binding_service = WorklinePluginBindingService(
        repository=BindingRepository(),
        runtime_repository=runtime_repository,
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
        clock=lambda: datetime(2026, 7, 17, 9),
    )
    workline = _workline()
    resolver = SessionResolver(
        session_repo=SessionRepository(),
        workline_repo=WorklineRepository(workline),
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=binding_service,
    )
    inbox = SimpleNamespace(
        payload_json={"data": {"vendor_material": "M-1"}},
        device_id=1,
        trace_id="trace-version",
        request_id="request-version",
        source_message_id="message-version",
    )

    session = await resolver._resolve_device_event(object(), inbox, workline)

    assert session.business_key == "BUSINESS-v1"
    assert seen == [(None, None), ("platform-test", "v1"), ("platform-test", "v1")]


@pytest.mark.asyncio
async def test_stale_workline_snapshot_reloads_shared_current_binding_before_session_creation() -> None:
    stale_workline = _workline()
    current_workline = _workline()
    current_workline.active_plugin_binding_id = 9
    current_workline.active_plugin_binding_version = 3
    current_workline.active_plugin_config_hash = "c" * 64
    current_workline.active_plugin_index_digest = "d" * 64
    current_binding = _binding(
        binding_id=9,
        binding_version=3,
        config_hash="c" * 64,
        index_digest="d" * 64,
    )

    class WorklineRepository:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def acquire_plugin_pin_shared(self, _db: object, workline_id: int) -> None:
            self.calls.append(("shared", workline_id))

        async def get_current_plugin_pin(
            self,
            _db: object,
            workline_id: int,
            *,
            populate_existing: bool = False,
        ) -> WorkLine:
            self.calls.append(("current", workline_id, populate_existing))
            return current_workline

    class HistoricalBindingRepository(BindingRepository):
        async def get_pinned(self, _db: object, binding_id: int) -> SimpleNamespace | None:
            self.get_calls.append(binding_id)
            if binding_id == 8:
                return _binding()
            return current_binding if binding_id == 9 else None

    workline_repository = WorklineRepository()
    binding_repository = HistoricalBindingRepository(current_binding)
    runtime_repository = RuntimeRepository()
    binding_service = WorklinePluginBindingService(
        repository=binding_repository,
        runtime_repository=runtime_repository,
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="d" * 64,
        clock=lambda: datetime(2026, 7, 17, 9),
    )
    resolver = SessionResolver(
        session_repo=SessionRepository(),
        workline_repo=workline_repository,
        command_repo=object(),
        outbox_repo=object(),
        rack_task_repo=object(),
        handling_step_repo=object(),
        handling_operation_repo=object(),
        plugin_binding_service=binding_service,
    )
    inbox = SimpleNamespace(
        payload_json={"business_key": "PKG-RACE"},
        device_id=1,
        trace_id="trace-race",
        request_id="request-race",
        source_message_id="message-race",
    )

    session = await resolver._resolve_device_event(object(), inbox, stale_workline)

    assert workline_repository.calls == [("shared", 7), ("current", 7, True)]
    assert binding_repository.get_calls == [9]
    assert session.plugin_binding_id == 9
    assert session.plugin_binding_version == 3
    assert session.plugin_config_hash == "c" * 64
    assert session.plugin_index_digest == "d" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binding", "error_match"),
    [
        (_binding(), None),
        (_binding(revoked=True), "撤权"),
    ],
)
async def test_runtime_inbox_retry_uses_historical_pin_despite_unapproved_draft_identity(
    monkeypatch: pytest.MonkeyPatch,
    binding: SimpleNamespace,
    error_match: str | None,
) -> None:
    session = WorklineSession(
        id=21,
        session_code="SESSION-21",
        workline_id=7,
        plugin_key="platform-test",
        contract_version="v1",
        plugin_binding_id=8,
        plugin_binding_version=2,
        plugin_config_hash="a" * 64,
        plugin_index_digest="b" * 64,
    )
    workline = _workline()
    workline.plugin_key = "unapproved-draft"
    workline.contract_version = "draft-v2"

    class SessionRepo:
        async def get_by_id(self, _db: object, _id: int) -> WorklineSession:
            return session

    class WorklineRepo:
        async def get_by_id(self, _db: object, _id: int) -> WorkLine:
            return workline

    class DeviceRepo:
        async def get_by_work_line_id(self, _db: object, _id: int) -> list[object]:
            return []

        async def get_by_id(self, _db: object, _id: int) -> None:
            return None

    class CommandRepo:
        pass

    import src.app.device.repositories as device_repositories
    from src.app.device.repositories import command_repository
    from src.app.runtime.orchestration import repository_wiring
    from src.app.runtime.orchestration.repositories import session_repository

    monkeypatch.setattr(session_repository, "WorklineSessionRepository", lambda: SessionRepo())
    monkeypatch.setattr(repository_wiring, "workline_repository", WorklineRepo())
    monkeypatch.setattr(device_repositories, "DeviceRepository", lambda: DeviceRepo())
    monkeypatch.setattr(command_repository, "DeviceCommandRepository", lambda: CommandRepo())
    binding_repository = BindingRepository(binding)
    admission_service = WorklinePluginBindingService(
        repository=binding_repository,
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
    )
    monkeypatch.setattr(context_loader, "workline_plugin_binding_service", admission_service, raising=False)
    monkeypatch.setattr(context_loader, "build_workline_runtime_services", lambda **_kwargs: SimpleNamespace())
    inbox = SimpleNamespace(
        kind="REPLAY_REQUEST",
        workline_session_id=21,
        workline_id=7,
        payload_json={"data": {"session_id": 21}},
        command_id=None,
        device_id=None,
    )

    if error_match is None:
        await context_loader.load_related_entities(object(), inbox)
    else:
        with pytest.raises(PluginBindingAdmissionError, match=error_match):
            await context_loader.load_related_entities(object(), inbox)
    assert binding_repository.get_calls == [8]


@pytest.mark.asyncio
async def test_repository_only_persists_service_constructed_runtime_aggregate() -> None:
    execution_session = ExecutionSession(workline_id=7, plugin_key="platform-test", manifest_version="v1")
    correlation = ExecutionCorrelation(correlation_id="correlation-1", trace_id="trace-1")
    work_item = ExecutionWorkItem(
        execution_session_id=0,
        correlation_id="correlation-1",
        object_type="session",
        object_key="PKG-1",
        current_step="INGRESS",
    )

    class Db:
        def __init__(self) -> None:
            self.saved: list[object] = []
            self.flush_calls = 0

        def add_all(self, records: list[object]) -> None:
            self.saved.extend(records)

        async def flush(self) -> None:
            self.flush_calls += 1
            execution_session.id = 101

    db = Db()
    await WorklinePluginBindingRepository.save_pinned_runtime_aggregate(
        db,
        execution_session=execution_session,
        correlation=correlation,
        work_item=work_item,
    )

    assert db.saved == [execution_session, correlation, work_item]
    assert db.flush_calls == 2
    assert correlation.execution_session_id == execution_session.id
    assert work_item.execution_session_id == execution_session.id


@pytest.mark.asyncio
async def test_intent_inventory_reads_plugin_key_from_pinned_execution_session() -> None:
    class Result:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

    class Db:
        def __init__(self) -> None:
            self.results = iter(
                [
                    Result([]),
                    Result(
                        [
                            SimpleNamespace(
                                workline_id=7,
                                id=31,
                                plugin_key="platform-test",
                                plugin_binding_id=8,
                                plugin_binding_version=2,
                                plugin_config_hash="a" * 64,
                                plugin_index_digest="b" * 64,
                            )
                        ]
                    ),
                ]
            )

        async def execute(self, _statement: object) -> Result:
            return next(self.results)

    references = await WorklinePluginBindingRepository.list_runtime_extension_references(Db(), 7)

    assert references == [
        {
            "type": "INTENT",
            "reference": "intent:31",
            "plugin_key": "platform-test",
            "plugin_binding_id": 8,
            "plugin_binding_version": 2,
            "plugin_config_hash": "a" * 64,
            "plugin_index_digest": "b" * 64,
        }
    ]
