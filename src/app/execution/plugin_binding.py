"""部署期显式注入的插件 handler 与初始执行关联端口。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from wes_plugin_sdk import FactReference, HandlerMetadata, PluginDefinition
from wes_plugin_sdk.validation import validate_required_text as _required


@dataclass(frozen=True, slots=True)
class InitialExecutionDescriptor:
    material_trace_id: str
    execution_code: str

    def __post_init__(self) -> None:
        _ = _required(self.material_trace_id, "material_trace_id")
        _ = _required(self.execution_code, "execution_code")


class InitialExecutionCorrelator(Protocol):
    """Task 8 adapter 可异步读取已提交的 immutable evidence typed snapshot。"""

    async def correlate(self, db: object, evidence_id: str) -> InitialExecutionDescriptor | None: ...


class PluginFactFactory(Protocol):
    """借助自身的具名 typed reader 把基础 Fact 引用增强为插件 Fact。"""

    async def build(self, db: object, fact: FactReference) -> FactReference: ...


class BusinessEvidenceDisposition(str, Enum):
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"
    RECONCILING = "RECONCILING"
    DEFERRED = "DEFERRED"


@dataclass(frozen=True, slots=True)
class BusinessEvidenceApplication:
    disposition: BusinessEvidenceDisposition
    decision_digest: str | None = None
    retry_after_ms: int | None = None

    def __post_init__(self) -> None:
        if self.disposition is BusinessEvidenceDisposition.APPLIED and (
            self.decision_digest is None or len(self.decision_digest) != 64
        ):
            raise ValueError("applied business evidence requires a decision digest")
        if self.disposition is BusinessEvidenceDisposition.DEFERRED:
            if self.decision_digest is not None or type(self.retry_after_ms) is not int or self.retry_after_ms <= 0:
                raise ValueError("deferred business evidence requires a positive retry_after_ms and no digest")
        elif self.retry_after_ms is not None:
            raise ValueError("retry_after_ms only belongs to deferred business evidence")


class BusinessEvidenceConsumer(Protocol):
    async def apply_in_session(
        self, db: object, evidence_id: int, *, workline_id: int
    ) -> BusinessEvidenceApplication: ...


@dataclass(frozen=True, slots=True)
class PluginRuntimeBinding:
    plugin_key: str
    plugin_version: str
    handlers: tuple[Any, ...]
    fact_factory: PluginFactFactory | None = None
    initial_execution_correlator: InitialExecutionCorrelator | None = None
    business_evidence_consumer: BusinessEvidenceConsumer | None = None
    business_wms_operations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ = _required(self.plugin_key, "plugin_key")
        _ = _required(self.plugin_version, "plugin_version")
        if type(self.handlers) is not tuple:
            raise TypeError("handlers must be a tuple")
        if self.handlers and self.fact_factory is None:
            raise ValueError("material handlers require a fact factory")
        if type(self.business_wms_operations) is not tuple or len(set(self.business_wms_operations)) != len(
            self.business_wms_operations
        ):
            raise ValueError("business_wms_operations must be a unique tuple")


class StaticPluginBinding:
    """不扫描环境的精确 `(plugin, version, Fact type, Fact version)` 路由。"""

    def __init__(
        self, bindings: tuple[PluginRuntimeBinding, ...], *, definitions: tuple[PluginDefinition, ...] = ()
    ) -> None:
        if type(bindings) is not tuple:
            raise TypeError("bindings must be a tuple")
        self._declared = {(item.plugin_key, item.plugin_version) for item in definitions}
        if len(self._declared) != len(definitions):
            raise ValueError("duplicate plugin definition")
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
                if not issubclass(metadata.fact_type, FactReference):
                    raise TypeError("execution handler fact_type must inherit FactReference")
                for fact_version in metadata.supported_versions:
                    route = (*binding_key, metadata.fact_type, fact_version)
                    if route in self._handlers:
                        raise ValueError(f"duplicate handler route: {route}")
                    self._handlers[route] = target

    @property
    def business_wms_routes(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (binding.plugin_key, binding.plugin_version, operation)
            for binding in self._bindings.values()
            if binding.business_evidence_consumer is not None
            for operation in binding.business_wms_operations
        )

    def has_handlers(self, plugin_key: str, plugin_version: str) -> bool:
        identity = (plugin_key, plugin_version)
        binding = self._bindings.get(identity)
        if binding is not None:
            return bool(binding.handlers)
        if identity in self._declared:
            return False
        raise LookupError(f"no plugin binding or declaration: {identity}")

    def has_business_evidence_consumer(self, plugin_key: str, plugin_version: str, *, operation: str | None) -> bool:
        identity = (plugin_key, plugin_version)
        binding = self._bindings.get(identity)
        if binding is not None:
            return binding.business_evidence_consumer is not None and (
                operation is None or operation in binding.business_wms_operations
            )
        if identity in self._declared:
            return False
        raise LookupError(f"no plugin binding or declaration: {identity}")

    def resolve_business_evidence_consumer(self, plugin_key: str, plugin_version: str) -> BusinessEvidenceConsumer:
        identity = (plugin_key, plugin_version)
        binding = self._bindings.get(identity)
        if binding is None or binding.business_evidence_consumer is None:
            raise LookupError(f"no business evidence consumer for plugin binding: {identity}")
        return binding.business_evidence_consumer

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
            factory = self._bindings[binding_key].fact_factory
        except KeyError as exc:
            raise LookupError(f"no plugin binding: {binding_key}") from exc
        if factory is None:
            raise LookupError(f"no material fact factory for plugin binding: {binding_key}")
        return factory


__all__ = [
    "BusinessEvidenceApplication",
    "BusinessEvidenceConsumer",
    "BusinessEvidenceDisposition",
    "InitialExecutionCorrelator",
    "InitialExecutionDescriptor",
    "PluginFactFactory",
    "PluginRuntimeBinding",
    "StaticPluginBinding",
]
