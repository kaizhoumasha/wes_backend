"""工作线插件模块入口。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_plugin_registry import (
        WORKLINE_PLUGIN_REGISTRY,
        WorklinePluginDefinition,
        get_workline_plugin_definition,
    )
    from src.workline_plugins.smt_classifier import (
        SmtClassifierPlugin,
        smt_classifier_plugin,
    )
    from src.workline_plugins.smt_full_box_exchange import (
        SmtFullBoxExchangePlugin,
        smt_full_box_exchange_plugin,
    )

__all__ = [
    "WORKLINE_PLUGIN_REGISTRY",
    "SmtClassifierPlugin",
    "SmtFullBoxExchangePlugin",
    "WorklinePluginDefinition",
    "get_workline_plugin_definition",
    "smt_classifier_plugin",
    "smt_full_box_exchange_plugin",
]

_EXPORT_SOURCES: dict[str, tuple[str, str]] = {
    "SmtClassifierPlugin": (
        "src.workline_plugins.smt_classifier",
        "SmtClassifierPlugin",
    ),
    "SmtFullBoxExchangePlugin": (
        "src.workline_plugins.smt_full_box_exchange",
        "SmtFullBoxExchangePlugin",
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
    "smt_classifier_plugin": (
        "src.workline_plugins.smt_classifier",
        "smt_classifier_plugin",
    ),
    "smt_full_box_exchange_plugin": (
        "src.workline_plugins.smt_full_box_exchange",
        "smt_full_box_exchange_plugin",
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
