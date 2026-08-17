"""部署期显式注入的插件 handler 与初始执行关联端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from wes_plugin_sdk import FactReference, HandlerMetadata


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class InitialExecutionDescriptor:
    material_trace_id: str
    execution_code: str

    def __post_init__(self) -> None:
        _required(self.material_trace_id, "material_trace_id")
        _required(self.execution_code, "execution_code")


class InitialExecutionCorrelator(Protocol):
    """Task 8 adapter 可异步读取已提交的 immutable evidence typed snapshot。"""

    async def correlate(self, evidence_id: str) -> InitialExecutionDescriptor | None: ...


class PluginFactFactory(Protocol):
    """借助自身的具名 typed reader 把基础 Fact 引用增强为插件 Fact。"""

    async def build(self, fact: FactReference) -> FactReference: ...


@dataclass(frozen=True, slots=True)
class PluginRuntimeBinding:
    plugin_key: str
    plugin_version: str
    handlers: tuple[Any, ...]
    fact_factory: PluginFactFactory
    initial_execution_correlator: InitialExecutionCorrelator | None = None

    def __post_init__(self) -> None:
        _required(self.plugin_key, "plugin_key")
        _required(self.plugin_version, "plugin_version")
        if type(self.handlers) is not tuple:
            raise TypeError("handlers must be a tuple")


class StaticPluginBinding:
    """不扫描环境的精确 `(plugin, version, Fact type, Fact version)` 路由。"""

    def __init__(self, bindings: tuple[PluginRuntimeBinding, ...]) -> None:
        if type(bindings) is not tuple:
            raise TypeError("bindings must be a tuple")
        self._bindings: dict[tuple[str, str], PluginRuntimeBinding] = {}
        self._handlers: dict[tuple[str, str, type[FactReference], str], Any] = {}
        for binding in bindings:
            binding_key = (binding.plugin_key, binding.plugin_version)
            if binding_key in self._bindings:
                raise ValueError(f"duplicate plugin binding: {binding_key}")
            self._bindings[binding_key] = binding
            for target in binding.handlers:
                metadata = getattr(target, "__wes_handler__", None)
                if type(metadata) is not HandlerMetadata:
                    raise TypeError("handler must declare static metadata with wes_plugin_sdk.handler")
                for fact_version in metadata.supported_versions:
                    route = (*binding_key, metadata.fact_type, fact_version)
                    if route in self._handlers:
                        raise ValueError(f"duplicate handler route: {route}")
                    self._handlers[route] = target

    def resolve_handler(self, plugin_key: str, plugin_version: str, fact: FactReference) -> Any:
        route = (plugin_key, plugin_version, type(fact), fact.fact_version)
        try:
            return self._handlers[route]
        except KeyError as exc:
            raise LookupError(f"no handler for route: {route}") from exc

    def resolve_initial_execution_correlator(
        self,
        plugin_key: str,
        plugin_version: str,
    ) -> InitialExecutionCorrelator:
        binding_key = (plugin_key, plugin_version)
        try:
            correlator = self._bindings[binding_key].initial_execution_correlator
        except KeyError as exc:
            raise LookupError(f"no plugin binding: {binding_key}") from exc
        if correlator is None:
            raise LookupError(f"no initial execution correlator for plugin binding: {binding_key}")
        return correlator

    def resolve_fact_factory(self, plugin_key: str, plugin_version: str) -> PluginFactFactory:
        binding_key = (plugin_key, plugin_version)
        try:
            return self._bindings[binding_key].fact_factory
        except KeyError as exc:
            raise LookupError(f"no plugin binding: {binding_key}") from exc


__all__ = [
    "InitialExecutionCorrelator",
    "InitialExecutionDescriptor",
    "PluginFactFactory",
    "PluginRuntimeBinding",
    "StaticPluginBinding",
]
