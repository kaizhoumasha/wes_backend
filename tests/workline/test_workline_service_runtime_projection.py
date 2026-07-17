import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import WorkLineRunMode
from src.app.workline.services.workline_service import WorkLineService


class _Db:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


class _WorkLineRepositoryStub:
    _model_name = "WorkLine"
    model = SimpleNamespace(__name__="WorkLine")

    def __init__(self, workline_id: int = 9007199254740993) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.restore_calls: list[int] = []
        self.update_calls: list[tuple[int, dict[str, object]]] = []
        self.current = SimpleNamespace(
            id=workline_id,
            is_active=False,
            version=7,
            plugin_key=None,
            contract_version=None,
            runtime_config_json=None,
            run_mode=WorkLineRunMode.AUTO,
            config={"draft": 1},
            active_plugin_binding_id=None,
            active_plugin_binding_version=None,
            active_plugin_config_hash=None,
            active_plugin_index_digest=None,
        )

    async def create(self, _db, data):
        self.create_calls.append(dict(data))
        return self.current

    async def restore(self, _db, workline_id):
        self.restore_calls.append(workline_id)
        self.current.id = workline_id
        return self.current

    async def get_for_update(self, _db, workline_id):
        self.current.id = workline_id
        return self.current

    async def get_by_id(self, _db, workline_id):
        self.current.id = workline_id
        return self.current

    async def update(self, _db, workline_id, data):
        self.update_calls.append((workline_id, dict(data)))
        for key, value in data.items():
            setattr(self.current, key, value)
        return self.current


class _RuntimeStatusProjectionSpy:
    def __init__(self, *, missing: bool = True, ensure_created: bool = True) -> None:
        self.missing = missing
        self.ensure_created = ensure_created
        self.snapshot_calls: list[int] = []
        self.ensure_calls: list[int] = []

    async def runtime_status_snapshot(self, _db, *, workline_id: int):
        self.snapshot_calls.append(workline_id)
        return SimpleNamespace(runtime_status=None if self.missing else "STOPPED")

    async def ensure_default(self, _db, *, workline_id: int):
        self.ensure_calls.append(workline_id)
        self.missing = False
        return SimpleNamespace(workline_id=workline_id)

    async def ensure_default_result(self, _db, *, workline_id: int):
        self.ensure_calls.append(workline_id)
        self.missing = False
        return SimpleNamespace(projection=SimpleNamespace(workline_id=workline_id), created=self.ensure_created)


@pytest.mark.asyncio
async def test_create_seeds_default_runtime_status_projection_before_commit():
    db = _Db()
    repository = _WorkLineRepositoryStub()
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)

    workline = await service.create(db, {})

    assert workline is repository.current
    assert repository.create_calls == [{}]
    assert projection.snapshot_calls == [9007199254740993]
    assert projection.ensure_calls == [9007199254740993]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_restore_seeds_default_runtime_status_projection_before_commit():
    db = _Db()
    repository = _WorkLineRepositoryStub()
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)

    workline = await service.restore(db, 9007199254740993)

    assert workline is repository.current
    assert repository.restore_calls == [9007199254740993]
    assert projection.snapshot_calls == [9007199254740993]
    assert projection.ensure_calls == [9007199254740993]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_activate_seeds_default_runtime_status_projection_before_state_update(monkeypatch):
    db = _Db()
    call_order: list[str] = []
    repository = _WorkLineRepositoryStub()
    projection = _RuntimeStatusProjectionSpy()

    async def ensure_default(_db, *, workline_id: int):
        call_order.append("ensure_default")
        return await _RuntimeStatusProjectionSpy.ensure_default(projection, _db, workline_id=workline_id)

    async def ensure_default_result(_db, *, workline_id: int):
        call_order.append("ensure_default_result")
        return await _RuntimeStatusProjectionSpy.ensure_default_result(projection, _db, workline_id=workline_id)

    async def runtime_status_snapshot(_db, *, workline_id: int):
        call_order.append("runtime_status_snapshot")
        return await _RuntimeStatusProjectionSpy.runtime_status_snapshot(projection, _db, workline_id=workline_id)

    async def update(_db, workline_id, data):
        call_order.append("update")
        return await _WorkLineRepositoryStub.update(repository, _db, workline_id, data)

    projection.runtime_status_snapshot = runtime_status_snapshot
    projection.ensure_default = ensure_default
    projection.ensure_default_result = ensure_default_result
    repository.update = update
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_can_activate", lambda _checks: True)

    workline = await service.activate(db, 9007199254740993, version=7)

    assert workline is repository.current
    assert projection.snapshot_calls == [9007199254740993]
    assert projection.ensure_calls == [9007199254740993]
    assert repository.update_calls == [(9007199254740993, {"is_active": True, "version": 7})]
    assert call_order == ["runtime_status_snapshot", "ensure_default_result", "update"]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_activate_already_active_with_existing_projection_does_not_update_or_commit(monkeypatch):
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    projection = _RuntimeStatusProjectionSpy(missing=False)
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_can_activate", lambda _checks: True)

    workline = await service.activate(db, 9007199254740993, version=7)

    assert workline is repository.current
    assert projection.snapshot_calls == [9007199254740993]
    assert projection.ensure_calls == []
    assert repository.update_calls == []
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_activate_already_active_conflict_existing_projection_does_not_commit(monkeypatch):
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    projection = _RuntimeStatusProjectionSpy(missing=True, ensure_created=False)
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_can_activate", lambda _checks: True)

    workline = await service.activate(db, 9007199254740993, version=7)

    assert workline is repository.current
    assert projection.snapshot_calls == [9007199254740993]
    assert projection.ensure_calls == [9007199254740993]
    assert repository.update_calls == []
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_legacy_plugin_activation_skips_empty_generated_index_transition_gate(monkeypatch):
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "legacy-plugin"
    repository.current.contract_version = "legacy-v1"
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])

    result = await service.activate(db, repository.current.id, version=7)

    assert result.is_active is True
    assert repository.update_calls == [(repository.current.id, {"is_active": True, "version": 7})]
    assert repository.current.active_plugin_binding_id is None


@pytest.mark.asyncio
async def test_active_platform_plugin_reapproval_appends_binding_and_switches_pin_atomically(monkeypatch):
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    repository.current.plugin_key = "platform-plugin"
    repository.current.contract_version = "v1"
    repository.current.active_plugin_binding_id = 8
    repository.current.active_plugin_binding_version = 1
    repository.current.active_plugin_config_hash = "a" * 64
    repository.current.active_plugin_index_digest = "b" * 64
    projection = _RuntimeStatusProjectionSpy(missing=False)

    class BindingService:
        def __init__(self) -> None:
            self.calls = 0

        def manages(self, workline: object) -> bool:
            return workline.plugin_key == "platform-plugin"

        async def activate(self, _db: object, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            workline = kwargs["workline"]
            workline.active_plugin_binding_id = 9
            workline.active_plugin_binding_version = 2
            workline.active_plugin_config_hash = "c" * 64
            workline.active_plugin_index_digest = "d" * 64
            return SimpleNamespace(id=9)

    binding_service = BindingService()
    service = WorkLineService(
        repository=repository,
        runtime_status_projection_service=projection,
        plugin_binding_service=binding_service,
    )
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])

    result = await service.activate(db, repository.current.id, version=7, actor="approver", reason="v2")

    assert result.active_plugin_binding_id == 9
    assert result.active_plugin_binding_version == 2
    assert binding_service.calls == 1
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_active_workline_allows_draft_config_edit_without_changing_active_pin() -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    repository.current.active_plugin_binding_id = 8
    repository.current.active_plugin_binding_version = 1
    repository.current.active_plugin_config_hash = "a" * 64
    repository.current.active_plugin_index_digest = "b" * 64
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())
    original_pin = (
        repository.current.active_plugin_binding_id,
        repository.current.active_plugin_binding_version,
        repository.current.active_plugin_config_hash,
        repository.current.active_plugin_index_digest,
    )

    result = await service.update(db, repository.current.id, {"config": {"draft": 2}, "version": 7})

    assert result.config == {"draft": 2}
    assert (
        result.active_plugin_binding_id,
        result.active_plugin_binding_version,
        result.active_plugin_config_hash,
        result.active_plugin_index_digest,
    ) == original_pin
