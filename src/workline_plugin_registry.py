"""工作线插件注册表。"""

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_runtime.plugin_manifest import WorklinePluginManifest


@dataclass(frozen=True)
class WorklinePluginDefinition:
    """工作线插件定义。"""

    plugin_key: str
    plugin_module: str
    plugin_class_name: str
    contract_module: str | None = None

    @property
    def plugin_class(self) -> type[Any]:
        """惰性解析插件类。"""

        return getattr(import_module(self.plugin_module), self.plugin_class_name)

    @property
    def manifest(self) -> "WorklinePluginManifest":
        """惰性解析插件 manifest。"""

        manifest = getattr(self.plugin_class, "manifest", None)
        if not _looks_like_manifest(manifest):
            raise TypeError(f"工作线插件 {self.plugin_key} 缺少有效 manifest")
        if getattr(manifest, "plugin_key", None) != self.plugin_key:
            raise ValueError(
                f"工作线插件 {self.plugin_key} manifest.plugin_key 不匹配: {getattr(manifest, 'plugin_key', None)}"
            )
        return manifest  # type: ignore[return-value]


def _looks_like_manifest(value: Any) -> bool:
    """轻量校验 manifest 形状，避免 registry 顶层导入 runtime 包。"""

    return (
        value is not None
        and isinstance(getattr(value, "plugin_key", None), str)
        and isinstance(getattr(value, "contract_version", None), str)
        and isinstance(getattr(value, "required_device_roles", None), tuple)
        and callable(getattr(value, "resolve_business_key", None))
    )


WORKLINE_PLUGIN_REGISTRY: dict[str, WorklinePluginDefinition] = {}


def get_workline_plugin_definition(plugin_key: str | None) -> WorklinePluginDefinition | None:
    """按插件标识获取插件定义。"""

    if not plugin_key:
        return None
    return WORKLINE_PLUGIN_REGISTRY.get(plugin_key)


def list_workline_plugin_definitions() -> list[WorklinePluginDefinition]:
    """按插件标识稳定导出已注册插件定义。"""

    return [WORKLINE_PLUGIN_REGISTRY[key] for key in sorted(WORKLINE_PLUGIN_REGISTRY)]


def parse_workline_six_in_one(plugin_key: str | None, payload: dict[str, Any] | None) -> Any | None:
    """调用插件提供的 SixInOne 解析入口。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None

    parser = getattr(definition.plugin_class, "parse_six_in_one_payload", None)
    if callable(parser):
        return parser(payload)
    return None


def resolve_workline_business_key(plugin_key: str | None, payload_json: dict[str, Any]) -> str | None:
    """通过插件 manifest 解析业务主键。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None
    return definition.manifest.resolve_business_key(payload_json)


def classify_workline_result(plugin_key: str | None, payload_json: dict[str, Any]) -> str | None:
    """通过插件 manifest 解析命令结果分类。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None
    return definition.manifest.classify_result(payload_json)


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
    contract_version = plugin_def.manifest.contract_version
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

    from src.workline_runtime.topology import WorklineTopologyView, validate_topology_manifest

    topology = WorklineTopologyView.from_devices(list(devices))
    try:
        validate_topology_manifest(definition.manifest, topology)
    except ValueError as exc:
        from src.core.exceptions import BadRequestException

        raise BadRequestException(message=str(exc)) from exc

    validator = getattr(definition.plugin_class, "validate_workline_topology", None)
    if callable(validator):
        _ = validator(workline, devices)


__all__ = [
    "WORKLINE_PLUGIN_REGISTRY",
    "WorklinePluginDefinition",
    "classify_workline_result",
    "get_plugin_contract_version",
    "get_workline_plugin_definition",
    "list_workline_plugin_definitions",
    "parse_workline_six_in_one",
    "resolve_workline_business_key",
    "validate_workline_plugin_assignment",
]
