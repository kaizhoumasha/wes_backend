"""部署内已安装业务插件的单一对象合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.app.execution.plugin_binding import PluginRuntimeBinding
    from src.app.workline.models.workline import LineType, WorkLineDeviceRole


@dataclass(frozen=True, slots=True)
class InstalledWorkLinePlugin:
    """同时承载执行路由与 WorkLine 装配所需的静态插件信息。"""

    display_name: str
    runtime_binding: PluginRuntimeBinding
    start_plan_builder: Any
    supported_line_types: tuple[LineType, ...]
    device_roles: tuple[WorkLineDeviceRole, ...] = ()
    business_blocker: Any | None = None
    wms_confirmation_follow_up_planner: Any | None = None
    transport_outcome_publisher: Any | None = None

    def __post_init__(self) -> None:
        if len({role.role_key for role in self.device_roles}) != len(self.device_roles):
            raise ValueError("duplicate device role")
        if not self.display_name.strip():
            raise ValueError("display_name is required")
        if type(self.supported_line_types) is not tuple or not self.supported_line_types:
            raise ValueError("supported_line_types must be a non-empty tuple")

    @property
    def plugin_key(self) -> str:
        return self.runtime_binding.plugin_key

    @property
    def plugin_version(self) -> str:
        return self.runtime_binding.plugin_version

    def supports(self, line_type: LineType) -> bool:
        return line_type in self.supported_line_types


def parse_device_bindings(
    config: object, roles: tuple[WorkLineDeviceRole, ...], *, require_complete: bool = True
) -> dict[str, str]:
    """通用绑定校验：草稿允许缺项，不接受插件私有配置或改写设备身份。"""
    if not isinstance(config, Mapping) or set(config) - {"device_bindings"}:
        raise ValueError("工作线配置只允许 device_bindings")
    raw = config.get("device_bindings", {})
    if not isinstance(raw, Mapping):
        raise ValueError("device_bindings 必须是对象")  # noqa: TRY004 - 外部配置校验统一使用 ValueError
    expected = {role.role_key for role in roles}
    if set(raw) - expected:
        raise ValueError("未知设备角色")
    bindings: dict[str, str] = {}
    for key, value in raw.items():
        if value is None and not require_complete:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise ValueError("设备绑定必须是有效设备编码")
        bindings[key] = value
    if len(set(bindings.values())) != len(bindings):
        raise ValueError("设备绑定不能重复")
    if require_complete and set(bindings) != expected:
        raise ValueError("设备角色尚未全部绑定")
    return bindings


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
    """按 WorkLine 冻结的完整插件身份精确选择，不回退到当前版本。"""

    plugin = resolve_installed_plugin(plugins, plugin_key)
    if plugin.plugin_version != plugin_version:
        raise LookupError(f"plugin version is not installed: {plugin_key}@{plugin_version}")
    return plugin


__all__ = [
    "InstalledWorkLinePlugin",
    "parse_device_bindings",
    "resolve_installed_plugin",
    "resolve_installed_plugin_version",
]
