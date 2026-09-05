"""部署内已安装业务插件的单一对象合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.app.execution.plugin_binding import PluginRuntimeBinding
    from src.app.workline.models.workline import LineType


@dataclass(frozen=True, slots=True)
class InstalledWorkLinePlugin:
    """同时承载执行路由与 WorkLine 装配所需的静态插件信息。"""

    display_name: str
    runtime_binding: PluginRuntimeBinding
    start_plan_builder: Any
    supported_line_types: tuple[LineType, ...]
    business_blocker: Any | None = None
    compatibility_checker: Any | None = None
    configuration_checker: Any | None = None
    wms_confirmation_follow_up_planner: Any | None = None
    transport_outcome_publisher: Any | None = None

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name is required")
        if type(self.supported_line_types) is not tuple or not self.supported_line_types:
            raise ValueError("supported_line_types must be a non-empty tuple")
        if self.compatibility_checker is not None and not callable(self.compatibility_checker):
            raise TypeError("compatibility_checker must be callable")
        if self.configuration_checker is not None and not callable(self.configuration_checker):
            raise TypeError("configuration_checker must be callable")

    @property
    def plugin_key(self) -> str:
        return self.runtime_binding.plugin_key

    @property
    def plugin_version(self) -> str:
        return self.runtime_binding.plugin_version

    def supports(self, line_type: LineType) -> bool:
        return line_type in self.supported_line_types


def resolve_installed_plugin(
    plugins: tuple[InstalledWorkLinePlugin, ...],
    plugin_key: str,
) -> InstalledWorkLinePlugin:
    """从部署期固定 tuple 精确选择当前插件，不提供默认或版本回退。"""

    matches = tuple(plugin for plugin in plugins if plugin.plugin_key == plugin_key)
    if not matches:
        raise LookupError(f"plugin is not installed: {plugin_key}")
    if len(matches) > 1:
        raise ValueError(f"duplicate installed plugin: {plugin_key}")
    return next(iter(matches))


def resolve_installed_plugin_version(
    plugins: tuple[InstalledWorkLinePlugin, ...],
    plugin_key: str,
    plugin_version: str,
) -> InstalledWorkLinePlugin:
    """按 Epoch 冻结的完整插件身份精确选择，不回退到当前版本。"""

    plugin = resolve_installed_plugin(plugins, plugin_key)
    if plugin.plugin_version != plugin_version:
        raise LookupError(f"plugin version is not installed: {plugin_key}@{plugin_version}")
    return plugin


__all__ = ["InstalledWorkLinePlugin", "resolve_installed_plugin", "resolve_installed_plugin_version"]
