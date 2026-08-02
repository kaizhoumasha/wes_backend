"""通用 WorkLine 配置服务与运行状态投影合同。"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import WorkLineRunMode
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BusinessException, OptimisticLockException


class _Db:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


class _WorkLineRepositoryStub:
    _model_name = "WorkLine"
    model = SimpleNamespace(__name__="WorkLine")

    def __init__(self, workline_id: int = 9007199254740993) -> None:
        self.lock_events: list[str] = []
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

    async def create(self, _db: object, data: dict[str, object]) -> object:
        self.create_calls.append(dict(data))
        return self.current

    async def restore(self, _db: object, workline_id: int) -> object:
        self.restore_calls.append(workline_id)
        self.current.id = workline_id
        return self.current

    async def get_for_update(
        self,
        _db: object,
        workline_id: int,
        *,
        populate_existing: bool = False,
    ) -> object:
        _ = populate_existing
        self.lock_events.append("get_for_update")
        self.current.id = workline_id
        return self.current

    async def acquire_plugin_pin_exclusive(self, _db: object, workline_id: int) -> None:
        self.lock_events.append("exclusive")
        self.current.id = workline_id

    async def get_by_id(self, _db: object, workline_id: int) -> object:
        self.current.id = workline_id
        return self.current

    async def update(self, _db: object, workline_id: int, data: dict[str, object]) -> object:
        self.update_calls.append((workline_id, dict(data)))
        if "version" in data and data["version"] != self.current.version:
            raise OptimisticLockException(
                resource_type="WorkLine",
                resource_id=workline_id,
                current_version=self.current.version,
                provided_version=data["version"],
            )
        for key, value in data.items():
            setattr(self.current, key, value)
        if "version" in data:
            self.current.version += 1
        return self.current


class _RuntimeStatusProjectionSpy:
    def __init__(self, *, missing: bool = True, ensure_created: bool = True) -> None:
        self.missing = missing
        self.ensure_created = ensure_created
        self.snapshot_calls: list[int] = []
        self.ensure_calls: list[int] = []

    async def runtime_status_snapshot(self, _db: object, *, workline_id: int) -> object:
        self.snapshot_calls.append(workline_id)
        return SimpleNamespace(runtime_status=None if self.missing else "STOPPED")

    async def ensure_default(self, _db: object, *, workline_id: int) -> object:
        self.ensure_calls.append(workline_id)
        self.missing = False
        return SimpleNamespace(workline_id=workline_id)

    async def ensure_default_result(self, _db: object, *, workline_id: int) -> object:
        self.ensure_calls.append(workline_id)
        self.missing = False
        return SimpleNamespace(projection=SimpleNamespace(workline_id=workline_id), created=self.ensure_created)


def _prepare_activation(
    monkeypatch: pytest.MonkeyPatch,
    repository: _WorkLineRepositoryStub,
    projection: _RuntimeStatusProjectionSpy,
) -> WorkLineService:
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
    return service


@pytest.mark.asyncio
async def test_create_seeds_default_runtime_status_projection_before_commit() -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)

    workline = await service.create(db, {})

    assert workline is repository.current
    assert repository.create_calls == [{}]
    assert projection.snapshot_calls == [repository.current.id]
    assert projection.ensure_calls == [repository.current.id]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_restore_seeds_default_runtime_status_projection_before_commit() -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineService(repository=repository, runtime_status_projection_service=projection)

    workline = await service.restore(db, repository.current.id)

    assert workline is repository.current
    assert repository.restore_calls == [repository.current.id]
    assert projection.snapshot_calls == [repository.current.id]
    assert projection.ensure_calls == [repository.current.id]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_workline_can_update_non_runtime_fields() -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())

    updated = await service.update(db, repository.current.id, {"line_name": "通用工作线（更新）"})

    assert updated is repository.current
    assert repository.update_calls == [(repository.current.id, {"line_name": "通用工作线（更新）"})]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_activate_seeds_default_runtime_status_projection_before_state_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    projection = _RuntimeStatusProjectionSpy()
    service = _prepare_activation(monkeypatch, repository, projection)

    workline = await service.activate(db, repository.current.id, version=7)

    assert workline is repository.current
    assert projection.snapshot_calls == [repository.current.id]
    assert projection.ensure_calls == [repository.current.id]
    assert repository.update_calls == [(repository.current.id, {"is_active": True, "version": 7})]
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_activate_already_active_with_existing_projection_does_not_update_or_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    projection = _RuntimeStatusProjectionSpy(missing=False)
    service = _prepare_activation(monkeypatch, repository, projection)

    workline = await service.activate(db, repository.current.id, version=7)

    assert workline is repository.current
    assert projection.snapshot_calls == [repository.current.id]
    assert projection.ensure_calls == []
    assert repository.update_calls == []
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_activate_already_active_conflict_existing_projection_does_not_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    projection = _RuntimeStatusProjectionSpy(missing=True, ensure_created=False)
    service = _prepare_activation(monkeypatch, repository, projection)

    workline = await service.activate(db, repository.current.id, version=7)

    assert workline is repository.current
    assert projection.snapshot_calls == [repository.current.id]
    assert projection.ensure_calls == [repository.current.id]
    assert repository.update_calls == []
    assert db.commit_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("line_code", "LINE-B"),
        ("line_type", "MANUAL"),
        ("run_mode", WorkLineRunMode.MANUAL),
        ("runtime_config_json", {"event_bindings": {"SCAN": "OVERRIDE"}}),
    ],
)
async def test_active_workline_rejects_immediately_effective_runtime_field_update(
    field_name: str,
    field_value: object,
) -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())

    with pytest.raises(BusinessException) as exc_info:
        await service.update(_Db(), repository.current.id, {field_name: field_value, "version": 7})

    assert exc_info.value.detail == {
        "workline_id": repository.current.id,
        "fields": [field_name],
    }
    assert repository.update_calls == []
