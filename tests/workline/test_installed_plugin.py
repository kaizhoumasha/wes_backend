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
