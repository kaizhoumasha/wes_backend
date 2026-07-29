import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.workline.models import WorkLineRunMode
from src.app.workline.services.plugin_binding_service import (
    PluginBindingAdmissionError,
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
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
        self.workload_calls: list[int] = []
        self.workload_count = 0
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

    async def get_for_update(self, _db, workline_id, *, populate_existing=False):
        self.lock_events.append("get_for_update")
        _ = populate_existing
        self.current.id = workline_id
        return self.current

    async def acquire_plugin_pin_exclusive(self, _db, workline_id):
        self.lock_events.append("exclusive")
        self.current.id = workline_id

    async def get_by_id(self, _db, workline_id):
        self.current.id = workline_id
        return self.current

    async def update(self, _db, workline_id, data):
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

    async def get_unfinished_workload_summary(self, _db, workline_id):
        self.workload_calls.append(workline_id)
        return {"count": self.workload_count, "sample": None, "by_type": {}}


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
async def test_deactivate_acquires_plugin_pin_exclusive_before_workline_row_lock():
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())

    await service.deactivate(db, repository.current.id, version=7)

    assert repository.lock_events == ["exclusive", "get_for_update"]


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
async def test_generated_smt_workline_can_update_non_plugin_fields() -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = DEFINITION.plugin_key
    repository.current.contract_version = DEFINITION.contract_version
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())

    updated = await service.update(db, repository.current.id, {"line_name": "SMT 入库线（更新）"})

    assert updated is repository.current
    assert repository.update_calls == [(repository.current.id, {"line_name": "SMT 入库线（更新）"})]
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
async def test_generated_smt_activation_creates_pinned_binding(monkeypatch):
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = DEFINITION.plugin_key
    repository.current.contract_version = DEFINITION.contract_version
    repository.current.config = {
        "provider_profile": "wms.material-flow.sandbox",
        "source_arm_role": "SORTING_SOURCE_ARM",
        "ctu_basket_capacity": 6,
        "conveyor_entry_queue": {
            "code": "SMT-CONVEYOR-ENTRY",
            "role": "ENTRY",
            "capacity": 8,
            "order_policy": "FIFO",
        },
        "return_queue": {
            "code": "SMT-RETURN",
            "role": "RETURN_QUEUE",
            "order_policy": "FIFO",
        },
    }
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineService(
        repository=repository,
        runtime_status_projection_service=projection,
        plugin_binding_service=_PlatformBindingServiceStub(),
    )
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(
            get_by_work_line_id=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        id=101,
                        device_code="SMT-SOURCE-ARM-01",
                        device_role="SORTING_SOURCE_ARM",
                        role_index=1,
                        upstream_device_id=None,
                        capabilities_json={"supports_command_types": ["SORTING_SOURCE_PICK"]},
                        host="127.0.0.1",
                        port=8001,
                        protocol="HTTP",
                    )
                ]
            )
        ),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])

    result = await service.activate(db, repository.current.id, version=7)

    assert result.is_active is True
    assert repository.update_calls == [
        (
            repository.current.id,
            {
                "active_plugin_binding_id": 9,
                "active_plugin_binding_version": 1,
                "active_plugin_config_hash": "c" * 64,
                "active_plugin_index_digest": "d" * 64,
                "active_plugin_provider_requirements_json": [],
                "version": 7,
                "is_active": True,
            },
        )
    ]
    assert repository.current.active_plugin_binding_id == 9


def test_retired_smt_identity_remains_activation_blocker() -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "SMT_SORTING_" + "INBOUND"
    repository.current.contract_version = "2026-06-21" + ".p1"
    service = WorkLineService(repository=repository)

    checks = service._build_configuration_checks(repository.current, [], [])

    assert [(check.code, check.status, check.severity) for check in checks] == [
        ("PLUGIN_CONFIGURED", "FAIL", "BLOCKER")
    ]


def test_unknown_plugin_identity_remains_activation_blocker() -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "unknown-plugin"
    repository.current.contract_version = "unknown-v1"
    service = WorkLineService(repository=repository)

    checks = service._build_configuration_checks(repository.current, [], [])

    assert [(check.code, check.status, check.severity) for check in checks] == [
        ("PLUGIN_CONFIGURED", "FAIL", "BLOCKER")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"extra": True},
        {
            "device_roles": {
                "input_arm": "ROUGH_SORTER_INPUT_ARM",
                "conveyor": "ROUGH_SORTER_CONVEYOR",
                "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
            },
            "pipeline_input_location": "PIPELINE-IN-01",
            "pipeline_output_location": "PIPELINE-OUT-01",
            "ng_location": "NG-01",
            "warehouse_code": "WH-01",
            "owner_code": "OWNER-01",
            "provider_profile": "wms.unknown.sandbox",
        },
    ],
)
async def test_configuration_status_blocks_typed_binding_admission_failure(monkeypatch, config) -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "rough_sorter"
    repository.current.contract_version = "rough_sorter.v2"
    repository.current.config = config
    service = WorkLineService(repository=repository, plugin_binding_service=workline_plugin_binding_service)
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(service, "_list_rack_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_build_configuration_checks", lambda *_args, **_kwargs: [])

    status = await service.configuration_status(object(), repository.current.id)

    assert status.can_activate is False
    assert [(check.code, check.status, check.severity) for check in status.checks] == [
        ("PLUGIN_BINDING_ADMISSION", "FAIL", "BLOCKER")
    ]


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
            return SimpleNamespace(
                id=9,
                binding_version=2,
                typed_config_hash="c" * 64,
                generated_index_digest="d" * 64,
                provider_profile_snapshot_json=[],
            )

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
    assert result.version == 8
    assert binding_service.calls == 1
    assert repository.workload_calls == []
    assert repository.update_calls == [
        (
            repository.current.id,
            {
                "active_plugin_binding_id": 9,
                "active_plugin_binding_version": 2,
                "active_plugin_config_hash": "c" * 64,
                "active_plugin_index_digest": "d" * 64,
                "active_plugin_provider_requirements_json": [],
                "version": 7,
            },
        )
    ]
    assert db.commit_count == 1
    assert repository.lock_events == ["exclusive", "get_for_update"]

    with pytest.raises(OptimisticLockException):
        await service.activate(db, repository.current.id, version=7, actor="stale", reason="stale-v2")
    assert binding_service.calls == 1


class _PlatformBindingServiceStub:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def manages(self, _workline: object) -> bool:
        return True

    async def activate(self, _db: object, **_kwargs: object) -> SimpleNamespace:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            id=9,
            binding_version=1,
            typed_config_hash="c" * 64,
            generated_index_digest="d" * 64,
            provider_profile_snapshot_json=[],
        )


class _ImmutableBindingRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def next_binding_version(
        self, _db: object, _workline_id: int, _plugin_key: str, _contract_version: str
    ) -> int:
        return 1

    async def create_immutable(self, _db: object, data: dict[str, object]) -> SimpleNamespace:
        self.created.append(dict(data))
        return SimpleNamespace(id=21, **data)


@pytest.mark.asyncio
async def test_real_rough_sorter_activation_pins_profile_port_and_generated_index(monkeypatch):
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "rough_sorter"
    repository.current.contract_version = "rough_sorter.v2"
    repository.current.config = {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN-01",
        "pipeline_output_location": "PIPELINE-OUT-01",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": "wms.2026-07-28.full-factory.sandbox",
    }
    binding_repository = _ImmutableBindingRepository()
    binding_service = WorklinePluginBindingService(
        repository=binding_repository,
        profile_catalog=workline_plugin_binding_service.profile_catalog,
    )
    service = _prepare_platform_activation(monkeypatch, repository, binding_service)
    devices = [
        SimpleNamespace(
            id=index,
            device_code=device_code,
            device_role=device_role,
            work_line_id=repository.current.id,
            vendor_type="ECS",
        )
        for index, (device_code, device_role) in enumerate(
            (
                ("RS-IN-01", "ROUGH_SORTER_INPUT_ARM"),
                ("RS-CONVEYOR-01", "ROUGH_SORTER_CONVEYOR"),
                ("RS-OUT-01", "ROUGH_SORTER_OUTPUT_ARM"),
            ),
            start=1,
        )
    ]
    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    monkeypatch.setattr(
        workline_service_module,
        "device_repository",
        SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=devices)),
    )

    result = await service.activate(
        _Db(),
        repository.current.id,
        version=7,
        actor="dev-operator",
        reason="task10-switch-gate",
    )

    assert len(binding_repository.created) == 1
    assert result.active_plugin_binding_id == 21
    assert result.active_plugin_binding_version == 1
    assert result.active_plugin_index_digest == WORKLINE_PLUGIN_INDEX_DIGEST
    assert result.active_plugin_provider_requirements_json == ["WMS@2026-07-28.full-factory#sandbox"]
    assert {entry["device_code"] for entry in binding_repository.created[0]["device_snapshot_json"]} == {
        "RS-IN-01",
        "RS-CONVEYOR-01",
        "RS-OUT-01",
    }


def _prepare_platform_activation(monkeypatch, repository, binding_service):
    projection = _RuntimeStatusProjectionSpy(missing=False)
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
    return service


@pytest.mark.asyncio
async def test_first_platform_binding_rejects_unfinished_legacy_workload(monkeypatch):
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "platform-plugin"
    repository.current.contract_version = "v1"
    repository.workload_count = 1
    binding_service = _PlatformBindingServiceStub()
    service = _prepare_platform_activation(monkeypatch, repository, binding_service)

    with pytest.raises(BusinessException) as exc_info:
        await service.activate(_Db(), repository.current.id, version=7)

    assert exc_info.value.code == "4001"
    assert exc_info.value.detail == {"reason_code": "LEGACY_RUNTIME_WORKLOAD_PRESENT"}
    assert binding_service.calls == 0


@pytest.mark.asyncio
async def test_first_platform_binding_allows_cutover_without_unfinished_workload(monkeypatch):
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "platform-plugin"
    repository.current.contract_version = "v1"
    binding_service = _PlatformBindingServiceStub()
    service = _prepare_platform_activation(monkeypatch, repository, binding_service)

    result = await service.activate(_Db(), repository.current.id, version=7)

    assert repository.workload_calls == [repository.current.id]
    assert result.active_plugin_binding_id == 9
    assert result.is_active is True
    assert result.version == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal_message",
    [
        "config validation failed: secret",
        "device requirement 缺失: ['PLC']",
        "provider/Port admission failed: internal",
    ],
)
async def test_platform_binding_admission_error_becomes_stable_business_error(monkeypatch, internal_message):
    repository = _WorkLineRepositoryStub()
    repository.current.plugin_key = "platform-plugin"
    repository.current.contract_version = "v1"
    binding_service = _PlatformBindingServiceStub(error=PluginBindingAdmissionError(internal_message))
    service = _prepare_platform_activation(monkeypatch, repository, binding_service)

    with pytest.raises(BusinessException) as exc_info:
        await service.activate(_Db(), repository.current.id, version=7)

    assert exc_info.value.code == "4001"
    assert exc_info.value.message == "插件绑定准入失败，请检查配置、设备和外部合同"
    assert exc_info.value.detail == {"reason_code": "PLUGIN_BINDING_ADMISSION_FAILED"}
    assert internal_message not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_active_workline_allows_draft_config_edit_without_changing_active_pin() -> None:
    db = _Db()
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    repository.current.plugin_key = "rough_sorter"
    repository.current.contract_version = "rough_sorter.v2"
    repository.current.active_plugin_binding_id = 8
    repository.current.active_plugin_binding_version = 1
    repository.current.active_plugin_config_hash = "a" * 64
    repository.current.active_plugin_index_digest = "b" * 64
    binding = SimpleNamespace(
        id=8,
        workline_id=repository.current.id,
        plugin_key=repository.current.plugin_key,
        contract_version=repository.current.contract_version,
        binding_version=1,
        typed_config_hash="a" * 64,
        generated_index_digest="b" * 64,
    )

    class BindingService:
        async def get_pinned(self, _db: object, *, binding_id: int) -> object:
            assert binding_id == 8
            return binding

    service = WorkLineService(
        repository=repository,
        runtime_status_projection_service=_RuntimeStatusProjectionSpy(),
        plugin_binding_service=BindingService(),  # type: ignore[arg-type]
    )
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


@pytest.mark.asyncio
async def test_active_workline_rejects_plugin_identity_switch_without_changing_active_pin(monkeypatch) -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    repository.current.plugin_key = "rough_sorter"
    repository.current.contract_version = "rough_sorter.v2"
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())
    # 当前 generated registry 只有一个插件；隔离下游 catalog 校验，直接证明活动态 guard
    # 必须在未来新增第二个合法插件前就阻断身份与 immutable pin 漂移。
    monkeypatch.setattr(service, "_validate_plugin_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_validate_plugin_contract_version", lambda *_args, **_kwargs: None)

    with pytest.raises(BusinessException) as exc_info:
        await service.update(
            _Db(),
            repository.current.id,
            {
                "plugin_key": DEFINITION.plugin_key,
                "contract_version": DEFINITION.contract_version,
                "version": 7,
            },
        )

    assert exc_info.value.detail == {
        "workline_id": repository.current.id,
        "fields": ["contract_version", "plugin_key"],
    }
    assert repository.update_calls == []


@pytest.mark.asyncio
async def test_active_workline_rejects_config_draft_when_binding_belongs_to_another_workline() -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    repository.current.plugin_key = "rough_sorter"
    repository.current.contract_version = "rough_sorter.v2"
    repository.current.active_plugin_binding_id = 8
    repository.current.active_plugin_binding_version = 1
    repository.current.active_plugin_config_hash = "a" * 64
    repository.current.active_plugin_index_digest = "b" * 64

    class BindingService:
        async def get_pinned(self, _db: object, *, binding_id: int) -> object:
            assert binding_id == 8
            return SimpleNamespace(
                id=8,
                workline_id=repository.current.id + 1,
                plugin_key="rough_sorter",
                contract_version="rough_sorter.v2",
                binding_version=1,
                typed_config_hash="a" * 64,
                generated_index_digest="b" * 64,
            )

    service = WorkLineService(
        repository=repository,
        runtime_status_projection_service=_RuntimeStatusProjectionSpy(),
        plugin_binding_service=BindingService(),  # type: ignore[arg-type]
    )

    with pytest.raises(BusinessException) as exc_info:
        await service.update(_Db(), repository.current.id, {"config": {"draft": 2}, "version": 7})

    assert exc_info.value.detail == {
        "workline_id": repository.current.id,
        "fields": ["config"],
    }
    assert repository.update_calls == []


@pytest.mark.asyncio
async def test_active_legacy_workline_rejects_config_update_without_binding_snapshot() -> None:
    repository = _WorkLineRepositoryStub()
    repository.current.is_active = True
    repository.current.plugin_key = "rough_sorter"
    repository.current.contract_version = "rough_sorter.v2"
    repository.current.active_plugin_binding_id = None
    service = WorkLineService(repository=repository, runtime_status_projection_service=_RuntimeStatusProjectionSpy())

    with pytest.raises(BusinessException) as exc_info:
        await service.update(_Db(), repository.current.id, {"config": {"route_roles": {"PASS": "draft"}}})

    assert exc_info.value.detail == {
        "workline_id": repository.current.id,
        "fields": ["config"],
    }
    assert repository.update_calls == []


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
