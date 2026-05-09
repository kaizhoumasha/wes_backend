"""WORKLINE 插件 manifest 合同。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.workline_runtime.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    MaterialIdentityResolver,
    material_identity_input_to_hash,
)
from src.workline_runtime.runtime_events import assert_no_reserved_runtime_events

if TYPE_CHECKING:
    from src.workline_runtime.ng_reason import NgReasonDefinition

BusinessKeyResolver = Callable[[dict[str, Any]], str | None]
ResultClassifier = Callable[[dict[str, Any]], str | None]


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_role_values(value: str | tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(item for item in value if isinstance(item, str) and item)


def _normalize_role_map(
    value: Mapping[str, str | tuple[str, ...] | list[str] | set[str]] | None,
) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}

    normalized: dict[str, tuple[str, ...]] = {}
    for contract_name, roles in value.items():
        if not isinstance(contract_name, str) or not contract_name:
            raise ValueError("manifest role map key must be a non-empty string")
        role_values = _normalize_role_values(roles)
        if not role_values:
            raise ValueError(f"manifest role map {contract_name!r} must declare at least one role")
        normalized[contract_name] = role_values
    return normalized


@dataclass(frozen=True)
class DeviceRoleRequirement:
    """插件所需设备角色和数量/能力约束。"""

    role: str
    min_count: int = 1
    max_count: int | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not _non_empty_str(self.role):
            raise ValueError("DeviceRoleRequirement.role must be a non-empty string")
        if self.min_count < 0:
            raise ValueError(f"{self.role}.min_count must be >= 0")
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError(f"{self.role}.max_count must be >= min_count")


@dataclass(frozen=True)
class WorklinePluginManifest:
    """插件运行契约。

    PR2 只要求最小必填字段：插件身份、契约版本、设备角色和业务键解析器。
    状态机、context model 等字段先作为可选能力保留。
    """

    plugin_key: str
    contract_version: str
    required_device_roles: tuple[DeviceRoleRequirement, ...]
    business_key_resolver: BusinessKeyResolver
    result_classifier: ResultClassifier | None = None
    state_machine_class: type[Any] | None = None
    context_model: type[Any] | None = None
    event_source_roles: Mapping[str, str | tuple[str, ...] | list[str] | set[str]] | None = None
    command_target_roles: Mapping[str, str | tuple[str, ...] | list[str] | set[str]] | None = None
    supported_events: frozenset[str] = field(default_factory=frozenset)
    supported_commands: frozenset[str] = field(default_factory=frozenset)
    material_identity_resolver: MaterialIdentityResolver | None = None
    ng_reason_catalog: Sequence[NgReasonDefinition] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _non_empty_str(self.plugin_key):
            raise ValueError("manifest.plugin_key must be a non-empty string")
        if not _non_empty_str(self.contract_version):
            raise ValueError("manifest.contract_version must be a non-empty string")
        if not self.required_device_roles:
            raise ValueError("manifest.required_device_roles must not be empty")
        if not callable(self.business_key_resolver):
            raise TypeError("manifest.business_key_resolver must be callable")
        if self.result_classifier is not None and not callable(self.result_classifier):
            raise TypeError("manifest.result_classifier must be callable")
        if self.material_identity_resolver is not None and not callable(self.material_identity_resolver):
            raise TypeError("manifest.material_identity_resolver must be callable")

        normalized_event_source_roles = _normalize_role_map(self.event_source_roles)
        object.__setattr__(self, "event_source_roles", normalized_event_source_roles)
        object.__setattr__(self, "command_target_roles", _normalize_role_map(self.command_target_roles))
        object.__setattr__(self, "ng_reason_catalog", tuple(self.ng_reason_catalog))

        owner = f"manifest {self.plugin_key}"
        assert_no_reserved_runtime_events(
            self.supported_events,
            owner=owner,
            declaration_surface="supported_events",
        )
        assert_no_reserved_runtime_events(
            normalized_event_source_roles.keys(),
            owner=owner,
            declaration_surface="event_source_roles",
        )

    def resolve_business_key(self, payload_json: dict[str, Any]) -> str | None:
        """调用插件声明的业务键解析器。"""

        return _non_empty_str(self.business_key_resolver(payload_json))

    def classify_result(self, payload_json: dict[str, Any]) -> str | None:
        """调用插件声明的命令结果分类器。"""

        if self.result_classifier is None:
            return None
        return _non_empty_str(self.result_classifier(payload_json))

    def resolve_material_identity(self, input_value: MaterialIdentityInput) -> MaterialIdentity:
        """调用插件声明的物料身份解析器；缺省时显式返回 MISSING。"""

        if self.material_identity_resolver is None:
            return MaterialIdentity(
                resolution_status=MaterialIdentityResolutionStatus.MISSING,
                raw_evidence_hash=material_identity_input_to_hash(input_value),
            )
        return self.material_identity_resolver(input_value)

    def list_ng_reasons(self) -> Sequence[NgReasonDefinition]:
        """返回插件声明的 NG 原因目录。"""

        return self.ng_reason_catalog


__all__ = [
    "BusinessKeyResolver",
    "DeviceRoleRequirement",
    "MaterialIdentityResolver",
    "ResultClassifier",
    "WorklinePluginManifest",
]
