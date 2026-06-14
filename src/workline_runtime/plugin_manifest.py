"""WORKLINE 插件 manifest 纯数据合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar, cast

from src.workline_runtime.runtime_events import assert_not_reserved_runtime_event


class NodeRefKind(str, Enum):
    """拓扑节点引用类型。"""

    DEVICE_ROLE = "DEVICE_ROLE"
    RACK_POSITION = "RACK_POSITION"


class FlowEdgeType(str, Enum):
    """拓扑边类型。"""

    MATERIAL_FLOW = "MATERIAL_FLOW"
    OPERATION = "OPERATION"


class EventCategory(str, Enum):
    """插件事件分类。"""

    ENTRY_DEVICE = "ENTRY_DEVICE"
    INTERNAL = "INTERNAL"
    COMMAND_RESULT = "COMMAND_RESULT"
    OPERATOR = "OPERATOR"
    SAFETY = "SAFETY"


class RackPositionArgRole(str, Enum):
    """命令货架位参数的业务角色。"""

    SOURCE = "SOURCE"
    TARGET = "TARGET"


class RackPositionArgSourceKind(str, Enum):
    """命令货架位参数的动态来源。"""

    EVENT_PAYLOAD = "EVENT_PAYLOAD"
    SESSION_CONTEXT = "SESSION_CONTEXT"
    COMMAND_PAYLOAD = "COMMAND_PAYLOAD"
    RESOURCE_OVERLAY = "RESOURCE_OVERLAY"


_ALLOWED_RACK_KINDS = frozenset({"SINGLE_LAYER", "FIVE_LAYER"})
_MISSING_TOPOLOGY = object()
_EnumT = TypeVar("_EnumT", bound=Enum)


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_tuple(
    value: tuple[str, ...] | list[str] | set[str] | frozenset[str], *, field_name: str
) -> tuple[str, ...]:
    if isinstance(value, str | Mapping):
        raise TypeError(f"{field_name} must be a string collection")

    normalized: list[str] = []
    for item in value:
        if not _non_empty_str(item):
            raise ValueError(f"{field_name} must contain only non-empty strings")
        normalized.append(item)
    return tuple(normalized)


def _optional_string(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not _non_empty_str(value):
        raise ValueError(f"{field_name} must be a non-empty string when declared")
    return value


def _coerce_enum(enum_type: type[_EnumT], value: Any, *, field_name: str) -> _EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{field_name} value {value!r} must be one of: {allowed}") from exc


def _ensure_unique(values: tuple[str, ...], *, field_name: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"{field_name} must be unique: {', '.join(duplicates)}")


@dataclass(frozen=True, slots=True)
class DeviceRequirement:
    """插件所需设备角色和数量/能力约束。"""

    role: str
    min_count: int = 1
    max_count: int | None = None
    hardware_capabilities: tuple[str, ...] | list[str] | set[str] | frozenset[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _non_empty_str(self.role):
            raise ValueError("DeviceRequirement.role must be a non-empty string")
        if self.min_count < 0:
            raise ValueError(f"{self.role}.min_count must be >= 0")
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError(f"{self.role}.max_count must be >= min_count")
        object.__setattr__(
            self,
            "hardware_capabilities",
            _string_tuple(self.hardware_capabilities, field_name="DeviceRequirement.hardware_capabilities"),
        )


@dataclass(frozen=True, slots=True)
class RackPositionCarrierCapability:
    """WES 管理货架停靠位可承载的货架/槽位能力。"""

    allowed_rack_kinds: tuple[str, ...] | list[str] | set[str] | frozenset[str]
    min_capacity: int = 1
    max_capacity: int = 1
    allowed_slot_kinds: tuple[str, ...] | list[str] | set[str] | frozenset[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.min_capacity < 0:
            raise ValueError("RackPositionCarrierCapability.min_capacity must be >= 0")
        if self.max_capacity < self.min_capacity:
            raise ValueError("RackPositionCarrierCapability.max_capacity must be >= min_capacity")

        rack_kinds = _string_tuple(
            self.allowed_rack_kinds,
            field_name="RackPositionCarrierCapability.allowed_rack_kinds",
        )
        if not rack_kinds:
            raise ValueError("RackPositionCarrierCapability.allowed_rack_kinds must not be empty")
        invalid_rack_kinds = sorted(set(rack_kinds) - _ALLOWED_RACK_KINDS)
        if invalid_rack_kinds:
            raise ValueError(
                "RackPositionCarrierCapability.allowed_rack_kinds contains unsupported rack kind: "
                + ", ".join(invalid_rack_kinds)
            )

        object.__setattr__(self, "allowed_rack_kinds", rack_kinds)
        object.__setattr__(
            self,
            "allowed_slot_kinds",
            _string_tuple(self.allowed_slot_kinds, field_name="RackPositionCarrierCapability.allowed_slot_kinds"),
        )


@dataclass(frozen=True, slots=True)
class RackPosition:
    """WES 管理的货架停靠位/库存事实锚点，不代表泛化物理位置。"""

    code: str
    role: str
    station_code: str
    carrier_capability: RackPositionCarrierCapability

    def __post_init__(self) -> None:
        for field_name in ("code", "role", "station_code"):
            if not _non_empty_str(getattr(self, field_name)):
                raise ValueError(f"RackPosition.{field_name} must be a non-empty string")
        if not isinstance(self.carrier_capability, RackPositionCarrierCapability):
            raise TypeError("RackPosition.carrier_capability must be RackPositionCarrierCapability")


@dataclass(frozen=True, slots=True)
class NodeRef:
    """拓扑节点引用。"""

    kind: NodeRefKind
    ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _coerce_enum(NodeRefKind, self.kind, field_name="NodeRef.kind"))
        if not _non_empty_str(self.ref):
            raise ValueError("NodeRef.ref must be a non-empty string")


@dataclass(frozen=True, slots=True)
class FlowEdge:
    """拓扑中的物料流或操作关系。"""

    from_node: NodeRef
    to_node: NodeRef
    type: FlowEdgeType

    def __post_init__(self) -> None:
        if not isinstance(self.from_node, NodeRef):
            raise TypeError("FlowEdge.from_node must be NodeRef")
        if not isinstance(self.to_node, NodeRef):
            raise TypeError("FlowEdge.to_node must be NodeRef")
        object.__setattr__(self, "type", _coerce_enum(FlowEdgeType, self.type, field_name="FlowEdge.type"))


@dataclass(frozen=True, slots=True)
class TopologySpec:
    """插件声明的静态拓扑。

    MATERIAL_FLOW 只描述货架位之间的库存/物料流；设备与货架位的动作关系使用 OPERATION。
    """

    flow_edges: tuple[FlowEdge, ...] | list[FlowEdge] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        flow_edges = tuple(self.flow_edges)
        if not all(isinstance(edge, FlowEdge) for edge in flow_edges):
            raise TypeError("TopologySpec.flow_edges must contain only FlowEdge")
        object.__setattr__(self, "flow_edges", flow_edges)


@dataclass(frozen=True, slots=True)
class EventBinding:
    """插件声明的业务事件及来源设备角色。"""

    event: str
    source_device_roles: tuple[str, ...] | list[str] | set[str] | frozenset[str]
    category: EventCategory
    payload_schema_ref: str | None = None

    def __post_init__(self) -> None:
        if not _non_empty_str(self.event):
            raise ValueError("EventBinding.event must be a non-empty string")
        assert_not_reserved_runtime_event(
            self.event,
            owner="manifest EventBinding",
            declaration_surface="event",
        )
        source_device_roles = _string_tuple(
            self.source_device_roles,
            field_name="EventBinding.source_device_roles",
        )
        if not source_device_roles:
            raise ValueError("EventBinding.source_device_roles must not be empty")
        object.__setattr__(self, "source_device_roles", source_device_roles)
        object.__setattr__(
            self,
            "category",
            _coerce_enum(EventCategory, self.category, field_name="EventBinding.category"),
        )
        object.__setattr__(
            self,
            "payload_schema_ref",
            _optional_string(self.payload_schema_ref, field_name="EventBinding.payload_schema_ref"),
        )


@dataclass(frozen=True, slots=True)
class RackPositionArgSource:
    """命令货架位参数的动态解析来源。"""

    kind: RackPositionArgSourceKind
    path: str
    fallback_rack_position_ref: str | None = None

    def __post_init__(self) -> None:
        kind = _coerce_enum(RackPositionArgSourceKind, self.kind, field_name="RackPositionArgSource.kind")
        if not _non_empty_str(self.path):
            raise ValueError("RackPositionArgSource.path must be a non-empty string")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "fallback_rack_position_ref",
            _optional_string(
                self.fallback_rack_position_ref,
                field_name="RackPositionArgSource.fallback_rack_position_ref",
            ),
        )


@dataclass(frozen=True, slots=True)
class RackPositionArg:
    """命令中的货架位参数声明。"""

    name: str
    role: RackPositionArgRole
    required: bool = True
    rack_position_ref: str | None = None
    source: RackPositionArgSource | None = None

    def __post_init__(self) -> None:
        if not _non_empty_str(self.name):
            raise ValueError("RackPositionArg.name must be a non-empty string")
        object.__setattr__(
            self,
            "role",
            _coerce_enum(RackPositionArgRole, self.role, field_name="RackPositionArg.role"),
        )
        object.__setattr__(
            self,
            "rack_position_ref",
            _optional_string(self.rack_position_ref, field_name="RackPositionArg.rack_position_ref"),
        )
        if self.source is not None and not isinstance(self.source, RackPositionArgSource):
            raise TypeError("RackPositionArg.source must be RackPositionArgSource")
        if self.rack_position_ref is not None and self.source is not None:
            raise ValueError("RackPositionArg.rack_position_ref and source are mutually exclusive")
        if self.required and self.rack_position_ref is None and self.source is None:
            raise ValueError("required RackPositionArg must declare rack_position_ref or source")


@dataclass(frozen=True, slots=True)
class CommandResultBinding:
    """命令结果到事件的静态绑定。"""

    result: str
    event: str
    category: EventCategory
    classification: str | None = None
    terminal: bool = False
    next_event: str | None = None

    def __post_init__(self) -> None:
        if not _non_empty_str(self.result):
            raise ValueError("CommandResultBinding.result must be a non-empty string")
        if not _non_empty_str(self.event):
            raise ValueError("CommandResultBinding.event must be a non-empty string")
        assert_not_reserved_runtime_event(
            self.event,
            owner="manifest CommandResultBinding",
            declaration_surface="event",
        )
        category = _coerce_enum(EventCategory, self.category, field_name="CommandResultBinding.category")
        if category != EventCategory.COMMAND_RESULT:
            raise ValueError("CommandResultBinding.category must be COMMAND_RESULT")
        object.__setattr__(self, "category", category)
        object.__setattr__(
            self,
            "classification",
            _optional_string(self.classification, field_name="CommandResultBinding.classification"),
        )
        object.__setattr__(
            self,
            "next_event",
            _optional_string(self.next_event, field_name="CommandResultBinding.next_event"),
        )
        if self.next_event is not None:
            assert_not_reserved_runtime_event(
                self.next_event,
                owner="manifest CommandResultBinding",
                declaration_surface="next_event",
            )


@dataclass(frozen=True, slots=True)
class CommandBinding:
    """插件命令及目标设备/结果绑定。"""

    command: str
    target_device_role: str
    rack_position_args: tuple[RackPositionArg, ...] | list[RackPositionArg] = field(default_factory=tuple)
    payload_schema_ref: str | None = None
    result_bindings: tuple[CommandResultBinding, ...] | list[CommandResultBinding] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _non_empty_str(self.command):
            raise ValueError("CommandBinding.command must be a non-empty string")
        if not _non_empty_str(self.target_device_role):
            raise ValueError("CommandBinding.target_device_role must be a non-empty string")

        rack_position_args = tuple(self.rack_position_args)
        if not all(isinstance(arg, RackPositionArg) for arg in rack_position_args):
            raise TypeError("CommandBinding.rack_position_args must contain only RackPositionArg")
        result_bindings = tuple(self.result_bindings)
        if not all(isinstance(binding, CommandResultBinding) for binding in result_bindings):
            raise TypeError("CommandBinding.result_bindings must contain only CommandResultBinding")

        object.__setattr__(self, "rack_position_args", rack_position_args)
        object.__setattr__(
            self,
            "payload_schema_ref",
            _optional_string(self.payload_schema_ref, field_name="CommandBinding.payload_schema_ref"),
        )
        object.__setattr__(self, "result_bindings", result_bindings)


@dataclass(frozen=True, slots=True)
class ResourceBoundary:
    """插件声明的资源边界。"""

    rack_position_code: str
    rack_kind: str
    business_demand_type: str
    wms_operation_type: str
    snapshot_kind: str
    lease_scope: str

    def __post_init__(self) -> None:
        for field_name in (
            "rack_position_code",
            "rack_kind",
            "business_demand_type",
            "wms_operation_type",
            "snapshot_kind",
            "lease_scope",
        ):
            if not _non_empty_str(getattr(self, field_name)):
                raise ValueError(f"ResourceBoundary.{field_name} must be a non-empty string")
        if self.rack_kind not in _ALLOWED_RACK_KINDS:
            raise ValueError(f"ResourceBoundary.rack_kind must be one of: {', '.join(sorted(_ALLOWED_RACK_KINDS))}")


@dataclass(frozen=True, slots=True)
class WorklinePluginManifest:
    """插件可序列化静态合同。"""

    plugin_key: str = ""
    contract_version: str = ""
    devices: tuple[DeviceRequirement, ...] | list[DeviceRequirement] | None = None
    rack_positions: tuple[RackPosition, ...] | list[RackPosition] | None = None
    topology: TopologySpec = field(default=cast("TopologySpec", _MISSING_TOPOLOGY))
    commands: tuple[CommandBinding, ...] | list[CommandBinding] = field(default_factory=tuple)
    events: tuple[EventBinding, ...] | list[EventBinding] = field(default_factory=tuple)
    resource_boundaries: tuple[ResourceBoundary, ...] | list[ResourceBoundary] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _non_empty_str(self.plugin_key):
            raise ValueError("manifest.plugin_key must be a non-empty string")
        if not _non_empty_str(self.contract_version):
            raise ValueError("manifest.contract_version must be a non-empty string")

        devices = tuple(self.devices or ())
        if not devices:
            raise ValueError("manifest.devices must not be empty")
        if not all(isinstance(device, DeviceRequirement) for device in devices):
            raise TypeError("manifest.devices must contain only DeviceRequirement")
        device_roles = tuple(device.role for device in devices)
        _ensure_unique(device_roles, field_name="manifest.devices.role")

        rack_positions = tuple(self.rack_positions or ())
        if not rack_positions:
            raise ValueError("manifest.rack_positions must not be empty")
        if not all(isinstance(rack_position, RackPosition) for rack_position in rack_positions):
            raise TypeError("manifest.rack_positions must contain only RackPosition")
        rack_position_codes = tuple(rack_position.code for rack_position in rack_positions)
        _ensure_unique(rack_position_codes, field_name="manifest.rack_positions.code")

        raw_topology: Any = self.topology
        if raw_topology is _MISSING_TOPOLOGY or raw_topology is None:
            raise ValueError("manifest.topology must be declared")
        if not isinstance(raw_topology, TopologySpec):
            raise TypeError("manifest.topology must be TopologySpec")
        topology = raw_topology

        commands = tuple(self.commands)
        if not all(isinstance(command, CommandBinding) for command in commands):
            raise TypeError("manifest.commands must contain only CommandBinding")
        events = tuple(self.events)
        if not all(isinstance(event, EventBinding) for event in events):
            raise TypeError("manifest.events must contain only EventBinding")
        resource_boundaries = tuple(self.resource_boundaries)
        if not all(isinstance(boundary, ResourceBoundary) for boundary in resource_boundaries):
            raise TypeError("manifest.resource_boundaries must contain only ResourceBoundary")

        device_role_set = set(device_roles)
        rack_positions_by_code = {rack_position.code: rack_position for rack_position in rack_positions}
        rack_position_code_set = set(rack_position_codes)
        self._validate_events(events, device_role_set)
        self._validate_commands(commands, device_role_set, rack_position_code_set)
        self._validate_resource_boundaries(resource_boundaries, rack_positions_by_code)
        self._validate_topology_refs(topology, device_role_set, rack_position_code_set)

        object.__setattr__(self, "devices", devices)
        object.__setattr__(self, "rack_positions", rack_positions)
        object.__setattr__(self, "topology", topology)
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "resource_boundaries", resource_boundaries)

    @staticmethod
    def _validate_events(events: tuple[EventBinding, ...], device_roles: set[str]) -> None:
        for event in events:
            unknown_roles = sorted(set(event.source_device_roles) - device_roles)
            if unknown_roles:
                raise ValueError(
                    f"EventBinding {event.event} source roles are not declared in manifest.devices: "
                    + ", ".join(unknown_roles)
                )

    @staticmethod
    def _validate_commands(
        commands: tuple[CommandBinding, ...],
        device_roles: set[str],
        rack_position_codes: set[str],
    ) -> None:
        for command in commands:
            if command.target_device_role not in device_roles:
                raise ValueError(
                    f"CommandBinding {command.command} target role is not declared in manifest.devices: "
                    f"{command.target_device_role}"
                )
            for result_binding in command.result_bindings:
                if result_binding.category != EventCategory.COMMAND_RESULT:
                    raise ValueError("CommandResultBinding.category must be COMMAND_RESULT")
            for rack_position_arg in command.rack_position_args:
                _validate_rack_position_arg_refs(rack_position_arg, rack_position_codes)

    @staticmethod
    def _validate_resource_boundaries(
        resource_boundaries: tuple[ResourceBoundary, ...],
        rack_positions_by_code: dict[str, RackPosition],
    ) -> None:
        for boundary in resource_boundaries:
            rack_position = rack_positions_by_code.get(boundary.rack_position_code)
            if rack_position is None:
                raise ValueError(
                    "ResourceBoundary.rack_position_code is not declared in manifest.rack_positions: "
                    f"{boundary.rack_position_code}"
                )
            if boundary.rack_kind not in rack_position.carrier_capability.allowed_rack_kinds:
                allowed_rack_kinds = ", ".join(rack_position.carrier_capability.allowed_rack_kinds)
                raise ValueError(
                    "ResourceBoundary.rack_kind is not allowed by "
                    "RackPosition.carrier_capability.allowed_rack_kinds: "
                    f"rack_position_code={boundary.rack_position_code}, rack_kind={boundary.rack_kind}, "
                    f"allowed_rack_kinds={allowed_rack_kinds}"
                )

    @staticmethod
    def _validate_topology_refs(
        topology: TopologySpec,
        device_roles: set[str],
        rack_position_codes: set[str],
    ) -> None:
        for edge in topology.flow_edges:
            if edge.type not in (FlowEdgeType.MATERIAL_FLOW, FlowEdgeType.OPERATION):
                raise ValueError("FlowEdge.type must be MATERIAL_FLOW or OPERATION")
            if edge.type == FlowEdgeType.MATERIAL_FLOW and (
                edge.from_node.kind != NodeRefKind.RACK_POSITION or edge.to_node.kind != NodeRefKind.RACK_POSITION
            ):
                raise ValueError("FlowEdge MATERIAL_FLOW edges must connect RACK_POSITION nodes")
            for node_ref in (edge.from_node, edge.to_node):
                if node_ref.kind == NodeRefKind.DEVICE_ROLE and node_ref.ref not in device_roles:
                    raise ValueError(
                        f"Topology NodeRef DEVICE_ROLE ref is not declared in manifest.devices: {node_ref.ref}"
                    )
                if node_ref.kind == NodeRefKind.RACK_POSITION and node_ref.ref not in rack_position_codes:
                    raise ValueError(
                        f"Topology NodeRef RACK_POSITION ref is not declared in manifest.rack_positions: {node_ref.ref}"
                    )


def _validate_rack_position_arg_refs(rack_position_arg: RackPositionArg, rack_position_codes: set[str]) -> None:
    if (
        rack_position_arg.rack_position_ref is not None
        and rack_position_arg.rack_position_ref not in rack_position_codes
    ):
        raise ValueError(
            "RackPositionArg.rack_position_ref is not declared in manifest.rack_positions: "
            f"{rack_position_arg.rack_position_ref}"
        )
    if (
        rack_position_arg.source is not None
        and rack_position_arg.source.fallback_rack_position_ref is not None
        and rack_position_arg.source.fallback_rack_position_ref not in rack_position_codes
    ):
        raise ValueError(
            "RackPositionArgSource.fallback_rack_position_ref is not declared in manifest.rack_positions: "
            f"{rack_position_arg.source.fallback_rack_position_ref}"
        )


__all__ = [
    "CommandBinding",
    "CommandResultBinding",
    "DeviceRequirement",
    "EventBinding",
    "EventCategory",
    "FlowEdge",
    "FlowEdgeType",
    "NodeRef",
    "NodeRefKind",
    "RackPosition",
    "RackPositionArg",
    "RackPositionArgRole",
    "RackPositionArgSource",
    "RackPositionArgSourceKind",
    "RackPositionCarrierCapability",
    "ResourceBoundary",
    "TopologySpec",
    "WorklinePluginManifest",
]
