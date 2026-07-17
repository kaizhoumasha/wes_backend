"""插件 binding 在真实 Session 创建与 RuntimeInbox retry 入口的接线测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

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
    return workline


def _binding(*, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=8,
        workline_id=7,
        plugin_key="platform-test",
        contract_version="v1",
        binding_version=2,
        typed_config_hash="a" * 64,
        generated_index_digest="b" * 64,
        environment="sandbox",
        valid_from=datetime(2026, 7, 17, 8),
        valid_until=None,
        is_enabled=enabled,
    )


class BindingRepository:
    async def get_pinned(self, _db: object, binding_id: int) -> SimpleNamespace | None:
        return _binding() if binding_id == 8 else None


class RuntimeRepository:
    def __init__(self) -> None:
        self.created: tuple[ExecutionSession, ExecutionWorkItem] | None = None

    async def create_pinned_runtime_aggregate(
        self,
        _db: object,
        *,
        workline_session: WorklineSession,
        execution_session: ExecutionSession,
        work_item: ExecutionWorkItem,
    ) -> tuple[ExecutionSession, ExecutionWorkItem]:
        self.created = (execution_session, work_item)
        return self.created


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


@pytest.mark.asyncio
async def test_session_resolver_pins_real_models_and_creates_runtime_aggregate_in_same_entry() -> None:
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

    session = await resolver._resolve_device_event(object(), inbox, _workline())

    assert isinstance(session, WorklineSession)
    assert session.plugin_binding_id == 8
    assert session.plugin_state_json == {"phase": "READY"}
    assert runtime_repository.created is not None
    execution_session, work_item = runtime_repository.created
    assert isinstance(execution_session, ExecutionSession)
    assert isinstance(work_item, ExecutionWorkItem)
    for record in (execution_session, work_item):
        assert record.plugin_key == "platform-test"
        assert record.plugin_binding_id == session.plugin_binding_id
        assert record.plugin_config_hash == session.plugin_config_hash
        assert record.plugin_index_digest == session.plugin_index_digest


@pytest.mark.asyncio
async def test_runtime_inbox_context_loader_rechecks_disabled_pinned_binding_on_retry(
    monkeypatch: pytest.MonkeyPatch,
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

    class AdmissionService:
        def manages(self, _workline: object) -> bool:
            return True

        async def get_pinned(self, _db: object, *, binding_id: int) -> SimpleNamespace:
            assert binding_id == 8
            return _binding(enabled=False)

        def assert_pinned_identity(self, *, binding: object, workline: object, session: object) -> None:
            _ = binding, workline, session

        def assert_execution_admitted(self, binding: object, *, environment: str, now: datetime) -> None:
            _ = environment, now
            if not binding.is_enabled:
                raise PluginBindingAdmissionError("binding kill switch 已关闭")

    import src.app.device.repositories as device_repositories
    from src.app.device.repositories import command_repository
    from src.app.runtime.orchestration import repository_wiring
    from src.app.runtime.orchestration.repositories import session_repository

    monkeypatch.setattr(session_repository, "WorklineSessionRepository", lambda: SessionRepo())
    monkeypatch.setattr(repository_wiring, "workline_repository", WorklineRepo())
    monkeypatch.setattr(device_repositories, "DeviceRepository", lambda: DeviceRepo())
    monkeypatch.setattr(command_repository, "DeviceCommandRepository", lambda: CommandRepo())
    monkeypatch.setattr(context_loader, "workline_plugin_binding_service", AdmissionService(), raising=False)
    monkeypatch.setattr(context_loader, "build_workline_runtime_services", lambda **_kwargs: SimpleNamespace())
    inbox = SimpleNamespace(
        kind="REPLAY_REQUEST",
        workline_session_id=21,
        workline_id=7,
        payload_json={"data": {"session_id": 21}},
        command_id=None,
        device_id=None,
    )

    with pytest.raises(PluginBindingAdmissionError, match="kill switch"):
        await context_loader.load_related_entities(object(), inbox)


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
