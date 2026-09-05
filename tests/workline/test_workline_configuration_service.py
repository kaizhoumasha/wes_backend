"""WorkLine 插件配置与设备全集保存合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.workline import LineType, WorkLineConfigurationUpdate, WorkLineCreate, WorkLineUpdate
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BusinessException


class _Factory:
    async def build(self, _db: object, fact: object) -> object:
        return fact


def _plugin(
    *,
    blocker: object | None = None,
    checker: object | None = None,
    compatibility_checker: object | None = None,
) -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        display_name="Example",
        runtime_binding=PluginRuntimeBinding(
            plugin_key="example_plugin",
            plugin_version="1.0",
            handlers=(),
            fact_factory=_Factory(),
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO,),
        business_blocker=blocker,
        compatibility_checker=compatibility_checker,
        configuration_checker=checker,
    )


class _Db:
    def __init__(self) -> None:
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _WorkLines:
    def __init__(self, workline: object, *, unfinished: bool = False) -> None:
        self.workline = workline
        self.unfinished = unfinished
        self.updates: list[dict[str, object]] = []
        self.inactive_writes = 0

    async def get_for_update(self, _db: object, workline_id: int) -> object:
        assert workline_id == self.workline.id  # type: ignore[attr-defined]
        return self.workline

    async def get_by_id(self, _db: object, workline_id: int) -> object:
        assert workline_id == self.workline.id  # type: ignore[attr-defined]
        return self.workline

    async def get_unfinished_workload_summary(self, _db: object, _workline_id: int) -> dict[str, object]:
        return {
            "count": int(self.unfinished),
            "by_type": {"transport_tasks": self.unfinished},
            "sample": {"type": "transport_task", "identity": "T-1"} if self.unfinished else None,
        }

    async def update(self, _db: object, _id: int, data: dict[str, object]) -> object:
        self.updates.append(data)
        for key, value in data.items():
            if key != "version":
                setattr(self.workline, key, value)
        self.workline.version += 1  # type: ignore[attr-defined]
        return self.workline

    async def set_inactive_for_deactivate(self, _db: object, workline: object) -> object:
        workline.is_active = False  # type: ignore[attr-defined]
        self.inactive_writes += 1
        return workline


class _Devices:
    def __init__(self, devices: list[object]) -> None:
        self.devices = devices

    async def list_for_workline_configuration_update(
        self,
        _db: object,
        *,
        workline_id: int,
        device_codes: tuple[str, ...],
    ) -> list[object]:
        del workline_id, device_codes
        return self.devices

    async def get_by_work_line_id(self, _db: object, workline_id: int) -> list[object]:
        return [device for device in self.devices if device.work_line_id == workline_id]


def _workline(**changes: object) -> object:
    values = {
        "id": 7,
        "version": 3,
        "is_active": False,
        "line_type": LineType.AUTO,
        "plugin_key": None,
        "config": {},
    }
    values.update(changes)
    return SimpleNamespace(**values)


class _Device:
    def __init__(self, code: str, owner: int | None) -> None:
        self.id = hash(code)
        self.device_code = code
        self.work_line_id = owner
        self.is_deleted = False
        self.version = 0

    def increment_version(self) -> None:
        self.version += 1


def _device(code: str, owner: int | None) -> _Device:
    return _Device(code, owner)


class _Epochs:
    def __init__(self, active: object | None) -> None:
        self.active = active
        self.locked: list[int] = []

    async def get_active_for_workline(self, _db: object, _workline_id: int) -> object | None:
        return self.active

    async def lock_epoch_lifecycle(self, _db: object, epoch_id: int) -> None:
        self.locked.append(epoch_id)

    async def get_active_for_workline_for_update(self, _db: object, _workline_id: int) -> object | None:
        return self.active


class _EpochService:
    def __init__(self) -> None:
        self.closed: list[int] = []

    async def close_active_epoch(self, _db: object, **kwargs: object) -> object:
        self.closed.append(int(kwargs["workline_id"]))
        return SimpleNamespace(id=31)


class _Blocker:
    def __init__(self, count: int) -> None:
        self.count = count

    async def get_unfinished_workload_summary(self, _db: object, _workline_id: int) -> dict[str, object]:
        return {"count": self.count, "sample": {"identity": "P-1"} if self.count else None}


class _Safety:
    def __init__(self, active: object | None = None) -> None:
        self.active = active

    async def get_active_for_workline(self, _db: object, _workline_id: int) -> object | None:
        return self.active


def test_workline_generic_update_does_not_own_plugin_configuration() -> None:
    assert "plugin_key" not in WorkLineCreate.model_fields
    assert "config" not in WorkLineCreate.model_fields
    assert "plugin_key" not in WorkLineUpdate.model_fields
    assert "config" not in WorkLineUpdate.model_fields
    assert set(WorkLineConfigurationUpdate.model_fields) == {"version", "plugin_key", "config", "device_codes"}


@pytest.mark.asyncio
async def test_workline_generic_service_rejects_plugin_configuration_without_api_validation() -> None:
    with pytest.raises(BusinessException, match="工作线配置操作"):
        await WorkLineService().update(object(), 7, {"version": 3, "plugin_key": "example_plugin"})  # type: ignore[arg-type]

    with pytest.raises(BusinessException, match="工作线配置操作"):
        await WorkLineService().create(object(), {"plugin_key": "example_plugin"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_save_configuration_replaces_the_complete_device_set_and_commits_once() -> None:
    db = _Db()
    worklines = _WorkLines(_workline())
    bound = _device("D-1", 7)
    selected = _device("D-2", None)
    service = WorkLineConfigurationService(
        plugins=(_plugin(),),
        workline_repository=worklines,
        device_repository=_Devices([bound, selected]),
        safety_repository=_Safety(),
    )

    result = await service.save(
        db,
        workline_id=7,
        version=3,
        plugin_key="example_plugin",
        config={"mode": "AUTO"},
        device_codes=("D-2",),
    )

    assert bound.work_line_id is None
    assert selected.work_line_id == 7
    assert bound.version == 1
    assert selected.version == 1
    assert worklines.updates == [{"plugin_key": "example_plugin", "config": {"mode": "AUTO"}, "version": 3}]
    assert result.device_codes == ("D-2",)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_save_configuration_invalidates_changed_device_detail_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.services.workline_service import workline_service

    device_invalidator = AsyncMock()
    workline_invalidator = AsyncMock()
    monkeypatch.setattr(workline_service, "invalidate_cache", workline_invalidator)
    bound = _device("D-1", 7)
    selected = _device("D-2", None)
    service = WorkLineConfigurationService(
        plugins=(_plugin(),),
        workline_repository=_WorkLines(_workline()),
        device_repository=_Devices([bound, selected]),
        safety_repository=_Safety(),
        device_cache_invalidator=SimpleNamespace(invalidate_cache=device_invalidator),
    )
    cache = object()

    await service.save(
        _Db(),
        workline_id=7,
        version=3,
        plugin_key="example_plugin",
        config={},
        device_codes=("D-2",),
        cache=cache,
    )

    assert device_invalidator.await_args_list == [
        call(cache, bound.id),
        call(cache, selected.id),
        call(cache, invalidate_list=True),
    ]
    workline_invalidator.assert_awaited_once_with(cache, 7, invalidate_list=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workline", "devices", "codes", "message"),
    [
        (_workline(is_active=True), [], (), "已启用"),
        (_workline(), [_device("D-1", 8)], ("D-1",), "其他工作线"),
        (_workline(), [], ("UNKNOWN",), "不存在"),
        (_workline(), [_device("D-1", None)], ("D-1", "D-1"), "重复"),
    ],
)
async def test_save_configuration_fails_closed_without_partial_commit(
    workline: object,
    devices: list[object],
    codes: tuple[str, ...],
    message: str,
) -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        plugins=(_plugin(),),
        workline_repository=_WorkLines(workline),
        device_repository=_Devices(devices),
        safety_repository=_Safety(),
    )

    with pytest.raises(BusinessException, match=message):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key="example_plugin",
            config={},
            device_codes=codes,
        )

    assert db.commits == 0


@pytest.mark.asyncio
async def test_save_configuration_rejects_a_plugin_incompatible_with_selected_devices() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        plugins=(
            _plugin(
                compatibility_checker=lambda _workline, devices: (
                    ()
                    if any(device.device_role == "TRANSFER_DEVICE" for device in devices)
                    else ("DEVICE_ROLE_MISSING:TRANSFER_DEVICE",)
                )
            ),
        ),
        workline_repository=_WorkLines(_workline()),
        device_repository=_Devices([]),
        safety_repository=_Safety(),
    )

    with pytest.raises(BusinessException) as exc_info:
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key="example_plugin",
            config={},
            device_codes=(),
        )

    assert exc_info.value.detail == {
        "plugin_key": "example_plugin",
        "reasons": ["DEVICE_ROLE_MISSING:TRANSFER_DEVICE"],
    }
    assert db.commits == 0


@pytest.mark.asyncio
async def test_available_plugins_and_configuration_status_report_stable_incompatibility_reasons() -> None:
    workline = _workline(plugin_key="example_plugin", run_mode="AUTO", runtime_config_json={})
    service = WorkLineConfigurationService(
        plugins=(_plugin(checker=lambda _workline, _devices: ("DEVICE_ROLE_MISSING:TRANSFER_DEVICE",)),),
        workline_repository=_WorkLines(workline),
        device_repository=_Devices([]),
    )

    plugins = await service.available_plugins(object(), workline_id=7)
    status = await service.configuration_status(object(), workline_id=7)

    assert plugins[0].compatible is True
    assert plugins[0].incompatibility_reasons == ()
    assert status.can_activate is False
    plugin_check = next(check for check in status.checks if check.code == "PLUGIN_CONFIGURATION_COMPATIBLE")
    assert plugin_check.context["reasons"] == ["DEVICE_ROLE_MISSING:TRANSFER_DEVICE"]


@pytest.mark.asyncio
async def test_available_plugins_checks_candidate_compatibility_without_current_plugin_configuration() -> None:
    current = InstalledWorkLinePlugin(
        display_name="Current",
        runtime_binding=PluginRuntimeBinding(
            plugin_key="current_plugin",
            plugin_version="1.0",
            handlers=(),
            fact_factory=_Factory(),
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO,),
    )
    candidate = _plugin(
        checker=lambda _workline, _devices: ("CONFIGURATION_INVALID",),
        compatibility_checker=lambda _workline, _devices: (),
    )
    service = WorkLineConfigurationService(
        plugins=(current, candidate),
        workline_repository=_WorkLines(_workline(plugin_key="current_plugin", config={"current": {}})),
        device_repository=_Devices([]),
    )

    plugins = await service.available_plugins(object(), workline_id=7)

    candidate_summary = next(item for item in plugins if item.plugin_key == "example_plugin")
    assert candidate_summary.compatible is True


@pytest.mark.asyncio
async def test_save_configuration_fails_closed_when_current_plugin_was_removed_from_the_deployment() -> None:
    db = _Db()
    worklines = _WorkLines(_workline(plugin_key="removed_plugin"))
    service = WorkLineConfigurationService(
        plugins=(_plugin(),),
        workline_repository=worklines,
        device_repository=_Devices([]),
        safety_repository=_Safety(),
    )

    with pytest.raises(BusinessException, match="removed_plugin"):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key="example_plugin",
            config={},
            device_codes=(),
        )

    assert worklines.workline.plugin_key == "removed_plugin"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_save_configuration_is_blocked_by_the_current_plugins_business_tasks() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        plugins=(_plugin(blocker=_Blocker(1)),),
        workline_repository=_WorkLines(_workline(plugin_key="example_plugin")),
        device_repository=_Devices([]),
        safety_repository=_Safety(),
    )

    with pytest.raises(BusinessException, match="P-1"):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key=None,
            config={},
            device_codes=(),
        )

    assert db.commits == 0


@pytest.mark.asyncio
async def test_save_configuration_is_blocked_by_active_safety_incident() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        plugins=(_plugin(),),
        workline_repository=_WorkLines(_workline()),
        device_repository=_Devices([]),
        safety_repository=_Safety(SimpleNamespace(id=9)),
    )

    with pytest.raises(BusinessException, match="safety incident"):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key="example_plugin",
            config={},
            device_codes=(),
        )

    assert db.commits == 0


@pytest.mark.asyncio
async def test_deactivate_closes_the_active_epoch_and_workline_in_one_commit() -> None:
    db = _Db()
    worklines = _WorkLines(_workline(is_active=True, plugin_key="example_plugin"))
    epoch_service = _EpochService()
    service = WorkLineConfigurationService(
        plugins=(_plugin(blocker=_Blocker(0)),),
        workline_repository=worklines,
        device_repository=_Devices([]),
        epoch_repository=_Epochs(SimpleNamespace(id=31, epoch_code="E-31")),
        epoch_service=epoch_service,
        command_repository=object(),
        safety_repository=_Safety(),
        clock=lambda: object(),
    )

    result = await service.deactivate(db, workline_id=7, version=3)

    assert result.is_active is False
    assert epoch_service.closed == [7]
    assert worklines.inactive_writes == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_deactivate_is_blocked_by_the_current_plugins_business_tasks() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        plugins=(_plugin(blocker=_Blocker(1)),),
        workline_repository=_WorkLines(_workline(is_active=True, plugin_key="example_plugin")),
        device_repository=_Devices([]),
        epoch_repository=_Epochs(SimpleNamespace(id=31, epoch_code="E-31")),
        epoch_service=_EpochService(),
        command_repository=object(),
        safety_repository=_Safety(),
    )

    with pytest.raises(BusinessException, match="P-1"):
        await service.deactivate(db, workline_id=7, version=3)

    assert db.commits == 0


@pytest.mark.asyncio
async def test_deactivate_is_blocked_by_active_safety_incident() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        plugins=(_plugin(),),
        workline_repository=_WorkLines(_workline(is_active=True, plugin_key="example_plugin")),
        device_repository=_Devices([]),
        epoch_repository=_Epochs(SimpleNamespace(id=31, epoch_code="E-31")),
        epoch_service=_EpochService(),
        command_repository=object(),
        safety_repository=_Safety(SimpleNamespace(id=9)),
    )

    with pytest.raises(BusinessException, match="safety incident"):
        await service.deactivate(db, workline_id=7, version=3)

    assert db.commits == 0
