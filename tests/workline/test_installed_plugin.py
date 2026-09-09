"""部署内业务插件对象合同。"""

import pytest

from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin
from src.app.workline.models.workline import LineType


class _Factory:
    async def build(self, _db: object, fact: object) -> object:
        return fact


def _plugin(key: str = "rough_sorter") -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        display_name="粗分业务",
        runtime_binding=PluginRuntimeBinding(
            plugin_key=key,
            plugin_version="1.0.0",
            handlers=(),
            fact_factory=_Factory(),
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO, LineType.MANUAL),
    )


def test_installed_plugin_is_the_single_source_of_runtime_and_workline_metadata() -> None:
    plugin = _plugin()

    assert plugin.plugin_key == "rough_sorter"
    assert plugin.plugin_version == "1.0.0"
    assert plugin.supports(LineType.AUTO)
    assert not plugin.supports(LineType.HYBRID)


def test_installed_plugins_resolve_one_exact_current_plugin_without_fallback() -> None:
    plugins = (_plugin("rough_sorter"), _plugin("manual_picking"))

    assert resolve_installed_plugin(plugins, "manual_picking").plugin_key == "manual_picking"
    with pytest.raises(LookupError, match="not installed"):
        resolve_installed_plugin(plugins, "unknown")


def test_installed_plugins_reject_duplicate_plugin_keys() -> None:
    with pytest.raises(ValueError, match="duplicate installed plugin"):
        resolve_installed_plugin((_plugin(), _plugin()), "rough_sorter")


def test_declared_roles_reject_duplicates_and_validate_only_device_bindings() -> None:
    from dataclasses import replace

    from src.app.workline.installed_plugin import parse_device_bindings
    from src.app.workline.models.workline import WorkLineDeviceRole

    with pytest.raises(ValueError):
        WorkLineDeviceRole.model_validate({"role_key": "SCAN", "display_name": "识别设备", "required": False})
    role = WorkLineDeviceRole(role_key="SCAN", display_name="识别设备")
    with pytest.raises(ValueError, match="duplicate device role"):
        replace(_plugin(), device_roles=(role, role))
    plugin = replace(_plugin(), device_roles=(role,))
    assert parse_device_bindings({}, plugin.device_roles, require_complete=False) == {}
    assert parse_device_bindings({"device_bindings": {"SCAN": "D1"}}, plugin.device_roles) == {"SCAN": "D1"}
    for config in (
        {},
        {"device_bindings": {"UNKNOWN": "D1"}},
        {"rough_sorter": {}},
        {"device_bindings": {"SCAN": " "}},
    ):
        with pytest.raises(ValueError):
            parse_device_bindings(config, plugin.device_roles)


@pytest.mark.parametrize(
    "bindings",
    [[], {"SCAN": True}, {"SCAN": "x" * 101}, {"SCAN": "D1", "ARRIVAL": "D1"}],
)
def test_device_binding_drafts_reject_malformed_or_duplicate_device_identities(bindings: object) -> None:
    from src.app.workline.installed_plugin import parse_device_bindings
    from src.app.workline.models.workline import WorkLineDeviceRole

    roles = (
        WorkLineDeviceRole(role_key="SCAN", display_name="识别设备"),
        WorkLineDeviceRole(role_key="ARRIVAL", display_name="到位设备"),
    )
    with pytest.raises(ValueError):
        parse_device_bindings({"device_bindings": bindings}, roles, require_complete=False)


def test_device_binding_draft_null_is_unbound_and_complete_binding_preserves_identity() -> None:
    from src.app.workline.installed_plugin import parse_device_bindings
    from src.app.workline.models.workline import WorkLineDeviceRole

    roles = (WorkLineDeviceRole(role_key="SCAN", display_name="识别设备"),)
    draft = {"device_bindings": {"SCAN": None}}
    assert parse_device_bindings(draft, roles, require_complete=False) == {}
    with pytest.raises(ValueError):
        parse_device_bindings(draft, roles)
    assert parse_device_bindings({"device_bindings": {"SCAN": " D1 "}}, roles) == {"SCAN": " D1 "}


def test_position_slots_resolve_each_worklines_resources_without_site_codes_in_plugin() -> None:
    from dataclasses import replace

    from src.app.workline.installed_plugin import parse_position_bindings, resolve_position_bindings
    from src.app.workline.models.workline import WorkLinePositionInput, WorkLinePositionSlot

    slot = WorkLinePositionSlot(slot_key="INPUT", display_name="入口", position_type="STATION", location_type="INLET")
    with pytest.raises(ValueError):
        WorkLinePositionSlot.model_validate({**slot.model_dump(), "required": False})
    plugin = replace(_plugin(), position_slots=(slot,))
    with pytest.raises(ValueError, match="duplicate position slot"):
        replace(plugin, position_slots=(slot, slot))
    for site in ("CNV0301", "OTHER-LINE-IN"):
        position = WorkLinePositionInput(
            position_code=site, position_name="现场入口", position_type="STATION", logic_location_code=site
        )
        config = {"position_bindings": {"INPUT": site}}
        assert parse_position_bindings(config, plugin.position_slots) == {"INPUT": site}
        resolved = resolve_position_bindings(config, plugin.position_slots, (position,))
        assert resolved[0].position_role == "INPUT"
        assert resolved[0].location_id == site
        assert resolved[0].location_type == "INLET"
        with pytest.raises(ValueError):
            resolve_position_bindings(config, plugin.position_slots, ())
        with pytest.raises(ValueError):
            resolve_position_bindings(config, plugin.position_slots, (position.model_copy(update={"enabled": False}),))
    assert parse_position_bindings({}, plugin.position_slots, require_complete=False) == {}
    with pytest.raises(ValueError):
        parse_position_bindings({}, plugin.position_slots)

    output = slot.model_copy(update={"slot_key": "OUTPUT"})
    slots = (slot, output)
    for config in (
        [],
        {"unknown": {}},
        {"position_bindings": []},
        {"position_bindings": {"UNKNOWN": "IN"}},
        *({"position_bindings": {"INPUT": value}} for value in (True, " ", "x" * 81)),
        {"position_bindings": {"INPUT": "IN", "OUTPUT": "IN"}},
    ):
        with pytest.raises(ValueError):
            parse_position_bindings(config, slots, require_complete=False)
    assert parse_position_bindings({"position_bindings": {"INPUT": "x" * 80}}, (slot,)) == {"INPUT": "x" * 80}
    null_binding = {"position_bindings": {"INPUT": None}}
    assert resolve_position_bindings(null_binding, (slot,), (), require_complete=False) == ()
    with pytest.raises(ValueError):
        parse_position_bindings(null_binding, (slot,))

    position = WorkLinePositionInput(
        position_code="IN", position_name="入口", position_type="STATION", logic_location_code="CNV0301"
    )
    config = {"position_bindings": {"INPUT": "IN"}}
    rack = WorkLinePositionInput(
        position_code="IN",
        position_name="货架位",
        position_type="RACK_POSITION",
        position_role="SMT_SORTER_STATION",
        allowed_rack_kind="FIVE_LAYER",
        logic_location_code="KT16",
    )
    for positions, message in (
        ((position, position), "工作位编码不能重复"),
        ((rack,), "类型不匹配"),
        (
            (position.model_copy(update={"logic_location_code": None, "external_location_code": "EXTERNAL"}),),
            "缺少执行位置编码",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            resolve_position_bindings(config, (slot,), positions)
    rack_slot = WorkLinePositionSlot(
        slot_key="INPUT",
        display_name="货架",
        position_type="RACK_POSITION",
        location_type="RACK_POSITION",
        allowed_rack_kind="SINGLE_LAYER",
    )
    with pytest.raises(ValueError, match="类型不匹配"):
        resolve_position_bindings(config, (rack_slot,), (rack,))
    with pytest.raises(ValueError, match="执行位置编码不能重复"):
        resolve_position_bindings(
            {"position_bindings": {"INPUT": "IN", "OUTPUT": "OUT"}},
            slots,
            (position, position.model_copy(update={"position_code": "OUT"})),
        )
    with pytest.raises(ValueError):
        WorkLinePositionSlot.model_validate({**slot.model_dump(), "allowed_rack_kind": "FIVE_LAYER"})


def test_station_positions_do_not_require_or_accept_rack_properties() -> None:
    from pydantic import ValidationError

    from src.app.workline.models.workline import WorkLinePositionInput

    station = {"position_code": "IN", "position_name": "入口", "position_type": "STATION"}
    assert WorkLinePositionInput(**station).allowed_rack_kind is None
    with pytest.raises(ValidationError):
        WorkLinePositionInput(**station, allowed_rack_kind="FIVE_LAYER")
    with pytest.raises(ValidationError):
        WorkLinePositionInput(position_code="RACK", position_name="货架位", position_type="RACK_POSITION")
