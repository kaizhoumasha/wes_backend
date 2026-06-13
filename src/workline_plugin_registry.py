"""工作线插件注册表。"""

from collections.abc import Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from importlib import import_module
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_runtime.material_identity import MaterialIdentity, MaterialIdentityInput
    from src.workline_runtime.ng_reason import NgReasonDefinition
    from src.workline_runtime.plugin_manifest import WorklinePluginManifest

_WORKLINE_PLUGIN_MANIFEST_FIELDS = (
    "plugin_key",
    "contract_version",
    "devices",
    "positions",
    "topology",
    "commands",
    "events",
    "resource_boundaries",
)


@dataclass(frozen=True)
class WorklinePluginDefinition:
    """工作线插件定义。"""

    plugin_key: str
    plugin_module: str
    plugin_class_name: str
    contract_module: str | None = None
    _plugin_instance: Any | None = field(default=None, init=False, repr=False, compare=False)
    _plugin_instance_lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    @property
    def plugin_class(self) -> type[Any]:
        """惰性解析插件类。"""

        return getattr(import_module(self.plugin_module), self.plugin_class_name)

    @property
    def manifest(self) -> "WorklinePluginManifest":
        """惰性解析插件 manifest。"""

        manifest = getattr(self.plugin_class, "manifest", None)
        if not is_dataclass(manifest) or isinstance(manifest, type):
            raise TypeError(f"工作线插件 {self.plugin_key} 缺少有效 manifest")
        manifest_fields = tuple(item.name for item in fields(manifest))
        if manifest_fields != _WORKLINE_PLUGIN_MANIFEST_FIELDS:
            raise TypeError(
                f"工作线插件 {self.plugin_key} manifest 必须只声明 8 个静态字段: "
                f"{', '.join(_WORKLINE_PLUGIN_MANIFEST_FIELDS)}"
            )
        if getattr(manifest, "plugin_key", None) != self.plugin_key:
            raise ValueError(
                f"工作线插件 {self.plugin_key} manifest.plugin_key 不匹配: {getattr(manifest, 'plugin_key', None)}"
            )
        return manifest  # type: ignore[return-value]

    @property
    def plugin_instance(self) -> Any:
        """惰性创建并缓存该 definition 的唯一插件实例。"""

        if self._plugin_instance is None:
            with self._plugin_instance_lock:
                if self._plugin_instance is None:
                    object.__setattr__(self, "_plugin_instance", self.plugin_class())
        return self._plugin_instance


WORKLINE_PLUGIN_REGISTRY: dict[str, WorklinePluginDefinition] = {
    "SMT_SORTING_INBOUND": WorklinePluginDefinition(
        plugin_key="SMT_SORTING_INBOUND",
        plugin_module="src.workline_plugins.smt_sorting_inbound.plugin",
        plugin_class_name="SmtSortingInboundPlugin",
    ),
    "rough_sorter": WorklinePluginDefinition(
        plugin_key="rough_sorter",
        plugin_module="src.workline_plugins.rough_sorter.plugin",
        plugin_class_name="RoughSorterPlugin",
        contract_module="src.workline_plugins.rough_sorter.contract",
    ),
}


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

    parser = getattr(definition.plugin_instance, "parse_six_in_one_payload", None)
    if callable(parser):
        return parser(payload)
    return None


def resolve_workline_business_key(plugin_key: str | None, payload_json: dict[str, Any]) -> str | None:
    """通过插件运行时实例解析业务主键。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None
    resolver = getattr(definition.plugin_instance, "resolve_business_key", None)
    if not callable(resolver):
        return None
    return resolver(payload_json)


def classify_workline_result(plugin_key: str | None, payload_json: dict[str, Any]) -> str | None:
    """通过插件运行时实例解析命令结果分类。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None
    classifier = getattr(definition.plugin_instance, "classify_result", None)
    if not callable(classifier):
        return None
    return classifier(payload_json)


def get_workline_context_model(plugin_key: str | None) -> type[Any] | None:
    """通过插件运行时实例获取上下文模型。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None

    resolver = getattr(definition.plugin_instance, "get_context_model", None)
    context_model = resolver() if callable(resolver) else None
    return context_model if isinstance(context_model, type) else None


def _missing_material_identity(input_value: "MaterialIdentityInput") -> "MaterialIdentity":
    from src.workline_runtime.material_identity import (
        MaterialIdentity,
        MaterialIdentityResolutionStatus,
        material_identity_input_to_hash,
    )

    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.MISSING,
        raw_evidence_hash=material_identity_input_to_hash(input_value),
    )


def resolve_workline_material_identity(
    plugin_key: str | None,
    input_value: "MaterialIdentityInput",
) -> "MaterialIdentity":
    """通过插件运行时实例解析物料身份，缺省时返回 MISSING。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return _missing_material_identity(input_value)

    resolver = getattr(definition.plugin_instance, "resolve_material_identity", None)
    if not callable(resolver):
        return _missing_material_identity(input_value)
    return resolver(input_value)


def list_workline_ng_reasons(plugin_key: str | None) -> tuple["NgReasonDefinition", ...]:
    """通过插件运行时实例列出 NG 原因，缺省时返回空目录。"""

    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return ()

    resolver = getattr(definition.plugin_instance, "list_ng_reasons", None)
    reasons = resolver() if callable(resolver) else None
    return tuple(reasons or ())


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

    validator = getattr(definition.plugin_instance, "validate_workline_topology", None)
    if callable(validator):
        _ = validator(workline, devices)


__all__ = [
    "WORKLINE_PLUGIN_REGISTRY",
    "WorklinePluginDefinition",
    "classify_workline_result",
    "get_plugin_contract_version",
    "get_workline_context_model",
    "get_workline_plugin_definition",
    "list_workline_ng_reasons",
    "list_workline_plugin_definitions",
    "parse_workline_six_in_one",
    "resolve_workline_business_key",
    "resolve_workline_material_identity",
    "validate_workline_plugin_assignment",
]
