"""Workline 插件最终 Definition 合同。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from src.app.runtime.extension_identity import sha256_digest, stable_sort, validate_key_version


def _callable_identity(value: Any) -> str:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if (
        not isinstance(module, str)
        or not module
        or not isinstance(qualname, str)
        or not qualname
        or qualname == "<lambda>"
        or "<locals>" in qualname
    ):
        raise TypeError("callable must have a stable import identity")
    return f"{module}.{qualname}"


@dataclass(frozen=True, slots=True)
class WorklinePluginDefinition:
    """插件不可变声明，不包含插件运行时实例或执行逻辑。"""

    plugin_key: str
    contract_version: str
    config_model: type[BaseModel]
    state_model: type[BaseModel]
    routes: tuple[str, ...]
    allowed_capabilities: tuple[tuple[str, str], ...]
    parsers: dict[str, Any]

    def __post_init__(self) -> None:
        validate_key_version(self.plugin_key, field_name="plugin_key")
        validate_key_version(self.contract_version, field_name="contract_version")
        for field_name in ("config_model", "state_model"):
            model = getattr(self, field_name)
            if not inspect.isclass(model) or not issubclass(model, BaseModel):
                raise TypeError(f"{field_name} must be a Pydantic model class")

        if len(set(self.routes)) != len(self.routes):
            raise ValueError("routes must be unique")
        for route in self.routes:
            validate_key_version(route, field_name="route")
        routes = stable_sort(self.routes)

        capabilities: list[tuple[str, str]] = []
        for capability_key, contract_version in self.allowed_capabilities:
            capabilities.append(
                (
                    validate_key_version(capability_key, field_name="capability_key"),
                    validate_key_version(contract_version, field_name="contract_version"),
                )
            )
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("allowed_capabilities must be unique")

        for route, parser in self.parsers.items():
            if route not in routes:
                raise ValueError(f"parser route is not declared: {route}")
            if not callable(parser):
                raise TypeError(f"parser must be callable: {route}")
            _callable_identity(parser)
        object.__setattr__(self, "routes", routes)
        object.__setattr__(self, "allowed_capabilities", stable_sort(capabilities))
        object.__setattr__(self, "parsers", MappingProxyType(dict(sorted(self.parsers.items()))))

    @property
    def identity(self) -> str:
        """返回只由插件声明元数据决定的稳定 identity。"""

        payload = {
            "allowed_capabilities": self.allowed_capabilities,
            "config_model": _callable_identity(self.config_model),
            "parsers": {route: _callable_identity(parser) for route, parser in self.parsers.items()},
            "routes": self.routes,
            "state_model": _callable_identity(self.state_model),
        }
        return f"{self.plugin_key}@{self.contract_version}:{sha256_digest(payload)}"


__all__ = ["WorklinePluginDefinition"]
