"""WorkLine 插件配置与设备全集保存合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from wes_plugin_sdk import PluginDefinition, WorkLineDeviceRole

from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.workline import (
    LineType,
    WorkLineBaseConfigurationUpdate,
    WorkLineConfigurationUpdate,
    WorkLineCreate,
    WorkLinePositionInput,
    WorkLineUpdate,
)
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BusinessException


class _Factory:
    async def build(self, _db: object, fact: object) -> object:
        return fact


def _plugin(
    *,
    blocker: object | None = None,
) -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        definition=PluginDefinition(
            plugin_key="example_plugin",
            plugin_version="1.0",
            display_name="Example",
            supported_line_types=(LineType.AUTO,),
            device_roles=(WorkLineDeviceRole(role_key="SCAN", display_name="识别设备"),),
        ),
        runtime_binding=PluginRuntimeBinding(
            plugin_key="example_plugin",
            plugin_version="1.0",
            handlers=(),
            fact_factory=_Factory(),
        ),
        start_plan_builder=object(),
        business_blocker=blocker,
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


@pytest.mark.asyncio
async def test_save_checks_submitted_configuration_before_changing_device_ownership() -> None:
    db = _Db()
    workline = _workline(plugin_key="example_plugin", config={"device_bindings": {"SCAN": "D-1"}})
    worklines = _WorkLines(workline)
    previous = _device("D-1", 7)
    incoming = _device("D-2", None)

    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=worklines,
        device_repository=_Devices([previous, incoming]),
    )
    with pytest.raises(BusinessException):
        await service.save_base(
            db,
            positions=(),
            workline_id=7,
            version=3,
            device_codes=("D-2",),
        )
    assert workline.config == {"device_bindings": {"SCAN": "D-1"}}
    assert previous.work_line_id == 7
    assert incoming.work_line_id is None
    assert worklines.updates == []
    assert db.commits == db.flushes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration_only", [False, True])
async def test_save_accepts_new_configuration_even_when_saved_configuration_is_incomplete(declaration_only) -> None:
    db = _Db()
    worklines = _WorkLines(_workline(config={}))
    incoming = _device("D-2", 7)

    from dataclasses import replace

    plugin = _plugin()
    if declaration_only:
        plugin = replace(plugin, runtime_binding=None, start_plan_builder=None)
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((plugin).definition,),
        workline_repository=worklines,
        device_repository=_Devices([incoming]),
    )
    await service.save(
        db,
        workline_id=7,
        version=3,
        plugin_key="example_plugin",
        config={"device_bindings": {"SCAN": "D-2"}},
    )
    assert worklines.updates[0]["config"] == {"device_bindings": {"SCAN": "D-2"}}
    assert incoming.work_line_id == 7
    assert db.commits == 1


@pytest.mark.asyncio
async def test_configuration_status_checks_saved_config_in_complete_mode() -> None:
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=_WorkLines(
            _workline(plugin_key="example_plugin", config={"device_bindings": {"SCAN": "D-2"}})
        ),
        device_repository=_Devices([_device("D-2", 7)]),
    )
    result = await service.configuration_status(_Db(), workline_id=7)
    check_result = next(check for check in result.checks if check.code == "PLUGIN_CONFIGURATION_COMPATIBLE")
    assert check_result.status == "PASS"


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
        "plugin_version": None,
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
        self.is_active = True
        self.endpoint_base_url = "http://ecs:8080"
        self.version = 0

    def increment_version(self) -> None:
        self.version += 1


def _device(code: str, owner: int | None) -> _Device:
    return _Device(code, owner)


class _Blocker:
    def __init__(self, count: int) -> None:
        self.count = count

    async def get_unfinished_workload_summary(self, _db: object, _workline_id: int) -> dict[str, object]:
        return {"count": self.count, "sample": {"identity": "P-1"} if self.count else None}


def test_workline_generic_update_does_not_own_plugin_configuration() -> None:
    assert "plugin_key" not in WorkLineCreate.model_fields
    assert "config" not in WorkLineCreate.model_fields
    assert "plugin_key" not in WorkLineUpdate.model_fields
    assert "config" not in WorkLineUpdate.model_fields
    assert set(WorkLineConfigurationUpdate.model_fields) == {"version", "plugin_key", "config"}
    from pydantic import ValidationError

    for physical_field in ("device_codes", "positions"):
        with pytest.raises(ValidationError):
            WorkLineConfigurationUpdate.model_validate({"version": 3, physical_field: []})
    for plugin_field in ("plugin_key", "config"):
        with pytest.raises(ValidationError):
            WorkLineBaseConfigurationUpdate.model_validate(
                {
                    "version": 3,
                    "device_codes": [],
                    "positions": [],
                    plugin_field: None,
                }
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["plugin_key", "plugin_version", "flow_mode", "device_contracts", "position_bindings"]
)
async def test_workline_generic_service_rejects_plugin_configuration_without_api_validation(field: str) -> None:
    with pytest.raises(BusinessException, match="工作线配置操作"):
        await WorkLineService().update(object(), 7, {"version": 3, field: "changed"})  # type: ignore[arg-type]

    with pytest.raises(BusinessException, match="工作线配置操作"):
        await WorkLineService().create(object(), {field: "changed"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_save_configuration_replaces_the_complete_device_set_and_commits_once() -> None:
    db = _Db()
    worklines = _WorkLines(
        _workline(plugin_version="old", flow_mode="OLD", device_contracts={"D-1": {}}, position_bindings={"OLD": {}})
    )
    bound = _device("D-1", 7)
    selected = _device("D-2", None)
    positions = _RackPositions()
    draft = WorkLinePositionInput(
        position_code="SORT-WORK-1",
        position_name="五层货架工作位 1",
        position_role="SMT_SORTER_STATION",
        allowed_rack_kind="FIVE_LAYER",
        capacity=1,
        logic_location_code="SORT-3-WORK-1",
    )
    service = WorkLineConfigurationService(
        definitions=((_plugin()).definition,),
        position_repository=positions,
        workline_repository=worklines,
        device_repository=_Devices([bound, selected]),
    )

    result = await service.save_base(
        db,
        workline_id=7,
        version=3,
        device_codes=("D-2",),
        positions=(draft,),
    )

    assert bound.work_line_id is None
    assert selected.work_line_id == 7
    assert bound.version == 1
    assert selected.version == 1
    assert worklines.updates == [
        {
            "plugin_version": None,
            "flow_mode": None,
            "device_contracts": {},
            "position_bindings": {},
            "version": 3,
        }
    ]
    assert result.device_codes == ("D-2",)
    assert positions.saved == (draft,)
    assert result.positions == (draft,)
    assert worklines.workline.plugin_key is None
    assert worklines.workline.config == {}
    assert db.commits == 1
    # Plugin assignment and removal never rewrite stable physical configuration.
    await service.save(
        db, workline_id=7, version=4, plugin_key="example_plugin", config={"device_bindings": {"SCAN": "D-2"}}
    )
    await service.save(db, workline_id=7, version=5, plugin_key=None, config={})
    assert positions.saved == (draft,)
    assert selected.work_line_id == 7 and selected.version == 1
    assert bound.work_line_id is None and bound.version == 1


@pytest.mark.asyncio
async def test_save_configuration_invalidates_changed_device_detail_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.services.workline_service import workline_service

    device_invalidator = AsyncMock()
    workline_invalidator = AsyncMock()
    monkeypatch.setattr(workline_service, "invalidate_cache", workline_invalidator)
    bound = _device("D-1", 7)
    selected = _device("D-2", None)
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=_WorkLines(_workline()),
        device_repository=_Devices([bound, selected]),
        device_cache_invalidator=SimpleNamespace(invalidate_cache=device_invalidator),
    )
    cache = object()

    await service.save_base(
        _Db(),
        positions=(),
        workline_id=7,
        version=3,
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
    ("workline", "devices", "codes", "unfinished", "message"),
    [
        (_workline(is_active=True), [], (), False, "已启用"),
        (_workline(), [_device("D-1", 8)], ("D-1",), False, "其他工作线"),
        (_workline(), [], ("UNKNOWN",), False, "不存在"),
        (_workline(), [_device("D-1", None)], ("D-1", "D-1"), False, "重复"),
        (_workline(), [], (), True, "未完成运行负载"),
    ],
)
async def test_save_configuration_fails_closed_without_partial_commit(
    workline: object,
    devices: list[object],
    codes: tuple[str, ...],
    unfinished: bool,
    message: str,
) -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=_WorkLines(workline, unfinished=unfinished),
        device_repository=_Devices(devices),
    )

    with pytest.raises(BusinessException, match=message):
        await service.save_base(
            db,
            positions=(),
            workline_id=7,
            version=3,
            device_codes=codes,
        )

    assert db.commits == 0


@pytest.mark.asyncio
async def test_save_configuration_rejects_a_plugin_incompatible_with_selected_devices() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=_WorkLines(_workline()),
        device_repository=_Devices([]),
    )

    with pytest.raises(BusinessException) as exc_info:
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key="example_plugin",
            config={"device_bindings": {"SCAN": "D-MISSING"}},
        )

    assert exc_info.value.detail == {
        "plugin_key": "example_plugin",
        "reasons": ["DEVICE_BINDING_UNKNOWN:D-MISSING"],
    }
    assert db.commits == 0


@pytest.mark.asyncio
async def test_available_plugins_and_configuration_status_report_stable_incompatibility_reasons() -> None:
    workline = _workline(plugin_key="example_plugin", run_mode="AUTO", runtime_config_json={})
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=_WorkLines(workline),
        device_repository=_Devices([]),
    )

    plugins = await service.available_plugins(object(), workline_id=7)
    status = await service.configuration_status(object(), workline_id=7)

    assert plugins[0].device_roles[0].role_key == "SCAN"
    assert plugins[0].compatible is True
    assert plugins[0].incompatibility_reasons == ()
    assert status.can_activate is False
    plugin_check = next(check for check in status.checks if check.code == "PLUGIN_CONFIGURATION_COMPATIBLE")
    assert plugin_check.context["reasons"] == ["CONFIGURATION_INVALID"]


@pytest.mark.asyncio
async def test_available_plugins_checks_candidate_compatibility_without_current_plugin_configuration() -> None:
    current = InstalledWorkLinePlugin(
        definition=PluginDefinition(
            plugin_key="current_plugin",
            plugin_version="1.0",
            display_name="Current",
            supported_line_types=(LineType.AUTO,),
        ),
        runtime_binding=PluginRuntimeBinding(
            plugin_key="current_plugin",
            plugin_version="1.0",
            handlers=(),
            fact_factory=_Factory(),
        ),
        start_plan_builder=object(),
    )
    candidate = _plugin()
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=(
            (current).definition,
            (candidate).definition,
        ),
        workline_repository=_WorkLines(_workline(plugin_key="current_plugin", config={"current": {}})),
        device_repository=_Devices([]),
    )

    plugins = await service.available_plugins(object(), workline_id=7)

    candidate_summary = next(item for item in plugins if item.plugin_key == "example_plugin")
    assert candidate_summary.compatible is True


@pytest.mark.asyncio
async def test_save_without_plugin_rejects_leftover_configuration_before_writes() -> None:
    db = _Db()
    worklines = _WorkLines(_workline())
    device = _device("D1", None)
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=worklines,
        device_repository=_Devices([device]),
    )

    with pytest.raises(BusinessException, match="配置"):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key=None,
            config={"device_bindings": {"SCAN": "D1"}},
        )

    assert device.work_line_id is None
    assert worklines.workline.config == {}
    assert db.commits == 0


@pytest.mark.asyncio
async def test_save_configuration_fails_closed_when_current_plugin_was_removed_from_the_deployment() -> None:
    db = _Db()
    worklines = _WorkLines(_workline(plugin_key="removed_plugin"))
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin()).definition,),
        workline_repository=worklines,
        device_repository=_Devices([]),
    )

    with pytest.raises(BusinessException, match="removed_plugin"):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key="example_plugin",
            config={},
        )

    assert worklines.workline.plugin_key == "removed_plugin"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_save_configuration_is_blocked_by_the_current_plugins_business_tasks() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin(blocker=_Blocker(1))).definition,),
        business_blockers={"example_plugin": _Blocker(1)},
        workline_repository=_WorkLines(_workline(plugin_key="example_plugin")),
        device_repository=_Devices([]),
    )

    with pytest.raises(BusinessException, match="P-1"):
        await service.save(
            db,
            workline_id=7,
            version=3,
            plugin_key=None,
            config={},
        )

    assert db.commits == 0


@pytest.mark.asyncio
async def test_deactivate_updates_workline_in_one_commit() -> None:
    db = _Db()
    worklines = _WorkLines(_workline(is_active=True, plugin_key="example_plugin"))
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin(blocker=_Blocker(0))).definition,),
        business_blockers={"example_plugin": _Blocker(0)},
        workline_repository=worklines,
        device_repository=_Devices([]),
    )

    result = await service.deactivate(db, workline_id=7, version=3)

    assert result.is_active is False
    assert worklines.inactive_writes == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_deactivate_is_blocked_by_the_current_plugins_business_tasks() -> None:
    db = _Db()
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin(blocker=_Blocker(1))).definition,),
        business_blockers={"example_plugin": _Blocker(1)},
        workline_repository=_WorkLines(_workline(is_active=True, plugin_key="example_plugin")),
        device_repository=_Devices([]),
    )

    with pytest.raises(BusinessException, match="P-1"):
        await service.deactivate(db, workline_id=7, version=3)

    assert db.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["save", "deactivate"])
async def test_missing_selected_plugin_version_blocks_lifecycle_before_business_check(action: str) -> None:
    db = _Db()
    worklines = _WorkLines(
        _workline(plugin_key="example_plugin", plugin_version="unavailable", is_active=action == "deactivate")
    )
    blocker = SimpleNamespace(get_unfinished_workload_summary=AsyncMock())
    service = WorkLineConfigurationService(
        position_repository=_RackPositions(),
        definitions=((_plugin(blocker=blocker)).definition,),
        business_blockers={"example_plugin": blocker},
        workline_repository=worklines,
        device_repository=_Devices([]),
    )
    with pytest.raises(BusinessException):
        if action == "deactivate":
            await service.deactivate(db, workline_id=7, version=3)
        else:
            await service.save(
                db,
                workline_id=7,
                version=3,
                plugin_key=None,
                config={},
            )
    blocker.get_unfinished_workload_summary.assert_not_awaited()
    assert worklines.updates == []
    assert worklines.inactive_writes == db.commits == 0


class _RackPositions:
    def __init__(self) -> None:
        self.saved: tuple[WorkLinePositionInput, ...] = ()

    async def has_active_placements(self, db: object, workline_id: int) -> bool:
        return False

    async def list_for_workline(
        self, db: object, workline_id: int, *, for_update: bool = False
    ) -> list[WorkLinePositionInput]:
        return list(self.saved)

    async def replace_for_workline(
        self, db: object, *, workline: object, positions: tuple[WorkLinePositionInput, ...], existing: list[object]
    ) -> None:
        self.saved = positions


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_field", ["position_code", "logic_location_code", "device_id"])
async def test_position_validation_prevents_partial_assembly_writes(invalid_field: str) -> None:
    db = _Db()
    positions = _RackPositions()
    worklines = _WorkLines(_workline())
    incoming = _device("D-2", None)
    drafts = [
        WorkLinePositionInput(
            position_code=f"WORK-{index}",
            position_name=f"五层货架工作位 {index}",
            position_role="SMT_SORTER_STATION",
            allowed_rack_kind="FIVE_LAYER",
            logic_location_code=f"LOCATION-{index}",
        )
        for index in (1, 2)
    ]
    setattr(drafts[1], invalid_field, 999 if invalid_field == "device_id" else getattr(drafts[0], invalid_field))
    service = WorkLineConfigurationService(
        definitions=((_plugin()).definition,),
        position_repository=positions,
        workline_repository=worklines,
        device_repository=_Devices([incoming]),
    )
    with pytest.raises(BusinessException):
        await service.save_base(
            db,
            workline_id=7,
            version=3,
            device_codes=("D-2",),
            positions=tuple(drafts),
        )
    assert incoming.work_line_id is None
    assert positions.saved == ()
    assert worklines.updates == []
    assert db.commits == db.flushes == 0


@pytest.mark.asyncio
async def test_position_assembly_is_validated_before_save_and_protects_bound_resources() -> None:
    from dataclasses import replace

    from wes_plugin_sdk import WorkLinePositionSlot

    slot = WorkLinePositionSlot(slot_key="INPUT", display_name="入口", position_type="STATION", location_type="INLET")
    plugin = replace(_plugin(), definition=replace(_plugin().definition, position_slots=(slot,)))
    line = _workline()
    positions = _RackPositions()
    positions.saved = (
        WorkLinePositionInput(
            position_code="LOCAL", position_name="入口", position_type="STATION", logic_location_code="CNV0301"
        ),
    )
    db = _Db()
    repository = _WorkLines(line)
    service = WorkLineConfigurationService(
        definitions=((plugin).definition,),
        position_repository=positions,
        workline_repository=repository,
        device_repository=_Devices([_device("D-1", 7)]),
    )
    config = {"device_bindings": {"SCAN": "D-1"}, "position_bindings": {"INPUT": "LOCAL"}}
    await service.save(db, workline_id=7, version=3, plugin_key=plugin.plugin_key, config=config)
    assert line.config == config
    assert positions.saved[0].position_code == "LOCAL"
    status = await service.configuration_status(db, workline_id=7)
    check = next(c for c in status.checks if c.code == "PLUGIN_CONFIGURATION_COMPATIBLE")
    assert check.status == "PASS"
    summary = (await service.available_plugins(db, workline_id=7))[0]
    assert summary.position_slots == (slot,)
    for invalid in ((), (positions.saved[0].model_copy(update={"enabled": False}),)):
        with pytest.raises(BusinessException, match="业务装配失效"):
            await service.save_base(db, workline_id=7, version=line.version, device_codes=("D-1",), positions=invalid)
    assert positions.saved[0].enabled
    assert db.commits == 1
