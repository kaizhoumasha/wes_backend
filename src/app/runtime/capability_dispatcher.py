"""Runtime capability dispatcher for target-state runtime capability wiring.

该模块只做静态 catalog 路由和 provider profile admission，不做动态 import，
也不 fallback 到旧 plugin/null plugin。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from src.app.contracts.external_contract_profile import ExternalContractProfile


class RuntimeCapabilityRouteError(LookupError):
    """Runtime capability 无法路由。"""


class RuntimeCapabilityUndeclaredError(PermissionError):
    """Provider profile 未声明目标 capability。"""


@dataclass(frozen=True, slots=True)
class RuntimeCapabilityDefinition:
    """Runtime capability 静态路由定义。"""

    capability_key: str
    contract_capability: str
    handler: Callable[[Any], Any]
    direction: str = "effect"
    contract_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capability_key:
            raise ValueError("capability_key must be non-empty")
        if not self.contract_capability:
            raise ValueError("contract_capability must be non-empty")
        if any(not capability for capability in self.declared_contract_capabilities):
            raise ValueError("contract_capabilities must be non-empty")
        if self.direction not in {"query", "effect"}:
            raise ValueError("direction must be query or effect")

    @property
    def declared_contract_capabilities(self) -> tuple[str, ...]:
        """返回 provider profile 必须声明的 port method 集合。"""

        return self.contract_capabilities or (self.contract_capability,)


class RuntimeCapabilityCatalog:
    """Runtime capability 静态 catalog。"""

    def __init__(self, definitions: Iterable[RuntimeCapabilityDefinition]) -> None:
        self._definitions = {definition.capability_key: definition for definition in definitions}

    def get(self, capability_key: str | None) -> RuntimeCapabilityDefinition:
        if not capability_key or capability_key not in self._definitions:
            raise RuntimeCapabilityRouteError(f"unknown runtime capability: {capability_key}")
        return self._definitions[capability_key]

    def list_keys(self) -> list[str]:
        return sorted(self._definitions)


class RuntimeCapabilityDispatcher:
    """RuntimeInbox normalized input -> runtime capability handler。"""

    def __init__(self, catalog: RuntimeCapabilityCatalog) -> None:
        self._catalog = catalog

    def dispatch(self, normalized_input: Any, *, profile: ExternalContractProfile | None = None) -> Any:
        """按 normalized input 的 runtime_capability 进行静态路由。"""

        capability_key = getattr(normalized_input, "runtime_capability", None)
        definition = self._catalog.get(capability_key)
        self._ensure_declared(definition, profile=profile)
        return definition.handler(normalized_input)

    @staticmethod
    def _ensure_declared(
        definition: RuntimeCapabilityDefinition,
        *,
        profile: ExternalContractProfile | None,
    ) -> None:
        if profile is None:
            raise RuntimeCapabilityUndeclaredError(
                f"provider profile required for runtime capability: {definition.capability_key}"
            )
        for contract_capability in definition.declared_contract_capabilities:
            try:
                profile.ensure_runtime_capability_declared(
                    contract_capability,
                    direction=definition.direction,  # type: ignore[arg-type]
                )
            except PermissionError as exc:
                raise RuntimeCapabilityUndeclaredError(str(exc)) from exc


__all__ = [
    "RuntimeCapabilityCatalog",
    "RuntimeCapabilityDefinition",
    "RuntimeCapabilityDispatcher",
    "RuntimeCapabilityRouteError",
    "RuntimeCapabilityUndeclaredError",
]
