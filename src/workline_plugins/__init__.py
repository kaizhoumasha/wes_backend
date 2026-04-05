"""工作线插件模块入口。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_plugin_registry import (
        WORKLINE_PLUGIN_REGISTRY,
        WorklinePluginDefinition,
        get_workline_plugin_definition,
    )
    from src.workline_plugins.simplified_smt_plugin import (
        SimplifiedSmtPlugin,
        simplified_smt_plugin,
    )

__all__ = [
    "WORKLINE_PLUGIN_REGISTRY",
    "SimplifiedSmtPlugin",
    "WorklinePluginDefinition",
    "get_workline_plugin_definition",
    "simplified_smt_plugin",
]

_EXPORT_SOURCES: dict[str, tuple[str, str]] = {
    "SimplifiedSmtPlugin": (
        "src.workline_plugins.simplified_smt_plugin",
        "SimplifiedSmtPlugin",
    ),
    "WORKLINE_PLUGIN_REGISTRY": (
        "src.workline_plugin_registry",
        "WORKLINE_PLUGIN_REGISTRY",
    ),
    "WorklinePluginDefinition": (
        "src.workline_plugin_registry",
        "WorklinePluginDefinition",
    ),
    "get_workline_plugin_definition": (
        "src.workline_plugin_registry",
        "get_workline_plugin_definition",
    ),
    "simplified_smt_plugin": (
        "src.workline_plugins.simplified_smt_plugin",
        "simplified_smt_plugin",
    ),
}


def __getattr__(name: str) -> Any:
    """按需解析导出，避免包导入时触发重型依赖链。"""

    if name not in _EXPORT_SOURCES:
        raise AttributeError(name)

    module_name, attr_name = _EXPORT_SOURCES[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
