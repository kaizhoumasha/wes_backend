"""工作线插件注册表。"""

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class WorklinePluginDefinition:
    """工作线插件定义。"""

    plugin_key: str
    plugin_module: str
    plugin_class_name: str
    contract_module: str | None = None
    state_machine_module: str | None = None
    state_machine_class_name: str | None = None

    @property
    def plugin_class(self) -> type[Any]:
        """惰性解析插件类。"""

        return getattr(import_module(self.plugin_module), self.plugin_class_name)

    @property
    def state_machine_class(self) -> type[Any] | None:
        """惰性解析状态机类。"""

        if self.state_machine_module is None or self.state_machine_class_name is None:
            return None
        return getattr(import_module(self.state_machine_module), self.state_machine_class_name)


WORKLINE_PLUGIN_REGISTRY: dict[str, WorklinePluginDefinition] = {
    "smt_classifier": WorklinePluginDefinition(
        plugin_key="smt_classifier",
        plugin_module="src.workline_plugins.smt_classifier",
        plugin_class_name="SmtClassifierPlugin",
        contract_module=None,
        state_machine_module=None,
        state_machine_class_name=None,
    ),
}


def get_workline_plugin_definition(plugin_key: str | None) -> WorklinePluginDefinition | None:
    """按插件标识获取插件定义。"""

    if not plugin_key:
        return None
    return WORKLINE_PLUGIN_REGISTRY.get(plugin_key)


def parse_workline_six_in_one(plugin_key: str | None, payload: dict[str, Any] | None) -> Any | None:
    """调用插件提供的 SixInOne 解析入口。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None

    parser = getattr(definition.plugin_class, "parse_six_in_one_payload", None)
    if callable(parser):
        return parser(payload)
    return None


def get_plugin_contract_version(plugin_key: str | None) -> str | None:
    """
    从插件类获取 contract_version。

    Args:
        plugin_key: 插件标识

    Returns:
        str | None: contract_version 字符串，如果不存在则返回 None
    """
    if not plugin_key:
        return None
    plugin_def = get_workline_plugin_definition(plugin_key)
    if not plugin_def:
        return None
    contract_version = getattr(plugin_def.plugin_class, "contract_version", None)
    return contract_version if isinstance(contract_version, str) and contract_version else None


def validate_workline_plugin_assignment(
    plugin_key: str,
    workline: Any,
    devices: Sequence[Any],
) -> None:
    """调用插件自身能力校验工作线拓扑/设备要求。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        from src.core.exceptions import BadRequestException

        raise BadRequestException(message=f"不支持的工作线插件: {plugin_key}")

    validator = getattr(definition.plugin_class, "validate_workline_topology", None)
    if callable(validator):
        _ = validator(workline, devices)


__all__ = [
    "WORKLINE_PLUGIN_REGISTRY",
    "WorklinePluginDefinition",
    "get_plugin_contract_version",
    "get_workline_plugin_definition",
    "parse_workline_six_in_one",
    "validate_workline_plugin_assignment",
]
