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


def _normalize_string_set(
    value: Sequence[str] | set[str] | frozenset[str] | None, *, field_name: str
) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str | Mapping):
        raise TypeError(f"manifest.{field_name} must be a string collection")

    normalized: set[str] = set()
    for item in value:
        if not _non_empty_str(item):
            raise ValueError(f"manifest.{field_name} must contain only non-empty strings")
        normalized.add(item)
    return frozenset(normalized)


_ALLOWED_SINGLE_LAYER_STATION_ROLES = frozenset({"SOURCE", "TARGET", "CLASSIFIER_WORK"})
_ALLOWED_SINGLE_LAYER_BUSINESS_DEMAND_TYPES = frozenset(
    {"SORTING_INBOUND_SOURCE", "SORTING_INBOUND_TARGET", "ROUGH_SORTER_BIN_ALLOCATION"}
)
_ALLOWED_SINGLE_LAYER_WMS_OPERATION_TYPES = frozenset(
    {"SUPPLY_SINGLE_LAYER_RACK", "ALLOCATE_SORTING_TARGET_BIN", "REPLACE_CLASSIFIER_WORK_RACK"}
)
_ALLOWED_SINGLE_LAYER_SNAPSHOT_KINDS = frozenset(
    {"ACTIVE_SOURCE_BIN_RACK", "ACTIVE_TARGET_BIN_RACK", "ACTIVE_CLASSIFIER_BIN_RACK"}
)
_ALLOWED_SINGLE_LAYER_LEASE_SCOPES = frozenset({"STATION"})


def _ensure_allowed_single_layer_boundary_value(field_name: str, value: str, allowed_values: frozenset[str]) -> None:
    if value not in allowed_values:
        allowed_text = ", ".join(sorted(allowed_values))
        raise ValueError(f"SingleLayerRackBoundary.{field_name} must be one of: {allowed_text}")


@dataclass(frozen=True)
class SingleLayerRackBoundary:
    """插件涉及货架承接时必须显式声明的 station/rack 边界。"""

    station_code: str
    position_code: str
    rack_kind: str
    station_role: str
    business_demand_type: str
    wms_operation_type: str
    snapshot_kind: str
    lease_scope: str

    def __post_init__(self) -> None:
        for field_name in (
            "station_code",
            "position_code",
            "rack_kind",
            "station_role",
            "business_demand_type",
            "wms_operation_type",
            "snapshot_kind",
            "lease_scope",
        ):
            if not _non_empty_str(getattr(self, field_name)):
                raise ValueError(f"SingleLayerRackBoundary.{field_name} must be a non-empty string")
        if self.rack_kind != "SINGLE_LAYER":
            raise ValueError("SingleLayerRackBoundary.rack_kind must be SINGLE_LAYER")
        _ensure_allowed_single_layer_boundary_value(
            "station_role",
            self.station_role,
            _ALLOWED_SINGLE_LAYER_STATION_ROLES,
        )
        _ensure_allowed_single_layer_boundary_value(
            "business_demand_type",
            self.business_demand_type,
            _ALLOWED_SINGLE_LAYER_BUSINESS_DEMAND_TYPES,
        )
        _ensure_allowed_single_layer_boundary_value(
            "wms_operation_type",
            self.wms_operation_type,
            _ALLOWED_SINGLE_LAYER_WMS_OPERATION_TYPES,
        )
        _ensure_allowed_single_layer_boundary_value(
            "snapshot_kind",
            self.snapshot_kind,
            _ALLOWED_SINGLE_LAYER_SNAPSHOT_KINDS,
        )
        _ensure_allowed_single_layer_boundary_value(
            "lease_scope",
            self.lease_scope,
            _ALLOWED_SINGLE_LAYER_LEASE_SCOPES,
        )

    @classmethod
    def from_value(cls, value: SingleLayerRackBoundary | Mapping[str, Any]) -> SingleLayerRackBoundary:
        """从 dataclass 或 dict 归一化插件声明。"""

        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("manifest.single_layer_boundaries item must be a mapping or SingleLayerRackBoundary")
        return cls(
            station_code=str(value.get("station_code") or ""),
            position_code=str(value.get("position_code") or ""),
            rack_kind=str(value.get("rack_kind") or ""),
            station_role=str(value.get("station_role") or ""),
            business_demand_type=str(value.get("business_demand_type") or ""),
            wms_operation_type=str(value.get("wms_operation_type") or ""),
            snapshot_kind=str(value.get("snapshot_kind") or ""),
            lease_scope=str(value.get("lease_scope") or ""),
        )

    def to_summary(self) -> dict[str, str]:
        """导出给 API summary 的稳定结构。"""

        return {
            "station_code": self.station_code,
            "position_code": self.position_code,
            "rack_kind": self.rack_kind,
            "station_role": self.station_role,
            "business_demand_type": self.business_demand_type,
            "wms_operation_type": self.wms_operation_type,
            "snapshot_kind": self.snapshot_kind,
            "lease_scope": self.lease_scope,
        }


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

    插件身份、契约版本、设备角色和业务键解析器是最小必填字段。
    """

    plugin_key: str
    contract_version: str
    required_device_roles: tuple[DeviceRoleRequirement, ...]
    business_key_resolver: BusinessKeyResolver
    result_classifier: ResultClassifier | None = None
    context_model: type[Any] | None = None
    event_source_roles: Mapping[str, str | tuple[str, ...] | list[str] | set[str]] | None = None
    command_target_roles: Mapping[str, str | tuple[str, ...] | list[str] | set[str]] | None = None
    supported_events: frozenset[str] = field(default_factory=frozenset)
    supported_commands: frozenset[str] = field(default_factory=frozenset)
    capabilities: frozenset[str] | Sequence[str] = field(default_factory=frozenset)
    resource_kinds: frozenset[str] | Sequence[str] = field(default_factory=frozenset)
    requires_single_layer_boundary: bool = False
    single_layer_boundaries: Sequence[SingleLayerRackBoundary | Mapping[str, Any]] = field(default_factory=tuple)
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
        object.__setattr__(self, "capabilities", _normalize_string_set(self.capabilities, field_name="capabilities"))
        object.__setattr__(
            self, "resource_kinds", _normalize_string_set(self.resource_kinds, field_name="resource_kinds")
        )
        object.__setattr__(
            self,
            "single_layer_boundaries",
            tuple(SingleLayerRackBoundary.from_value(boundary) for boundary in self.single_layer_boundaries),
        )
        if _requires_single_layer_boundaries(self) and not self.single_layer_boundaries:
            raise ValueError("manifest.single_layer_boundaries must be declared for single-layer rack plugins")
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


def _requires_single_layer_boundaries(manifest: WorklinePluginManifest) -> bool:
    return (
        bool(manifest.requires_single_layer_boundary)
        or "SINGLE_LAYER" in manifest.resource_kinds
        or "station_lease" in manifest.capabilities
        or "active_snapshot" in manifest.capabilities
        or "rack_operation" in manifest.capabilities
    )


__all__ = [
    "BusinessKeyResolver",
    "DeviceRoleRequirement",
    "MaterialIdentityResolver",
    "ResultClassifier",
    "SingleLayerRackBoundary",
    "WorklinePluginManifest",
]
