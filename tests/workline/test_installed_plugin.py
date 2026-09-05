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
