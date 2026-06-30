# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_manifest 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。
# C3 已 defer 本镜像到 C5b,因 runtime_events 镜像在 C5a 才就位。
# 自引用 src.workline_runtime.runtime_events 已重定向到 C5a events_bridge。

"""WORKLINE 插件 manifest 纯数据合同。"""

from __future__ import annotations

import logging
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from src.app.runtime.orchestration.events_bridge import assert_not_reserved_runtime_event
from src.app.runtime.orchestration.models.material_unit import MaterialUnitStatus

logger = logging.getLogger(__name__)


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


_ALLOWED_RACK_KINDS = frozenset({"SINGLE_LAYER", "FIVE_LAYER"})
_ALLOWED_PIPELINE_ORDER_POLICIES = frozenset({"FIFO", "LIFO", "PRIORITY"})
# pipeline_queues.role 白名单：覆盖设计文档队列角色（Buffer/Gate/Wait/Workstation/Exception）
# 与现有 manifest/template 使用的 ENTRY/SCAN/WORK 等同义角色。
_ALLOWED_PIPELINE_QUEUE_ROLES = frozenset(
    {"BUFFER", "GATE", "WAIT", "WORKSTATION", "EXCEPTION", "ENTRY", "SCAN", "WORK"}
)
# state_machines.granularity 白名单：当前唯一合法值，新增需同步设计文档。
_ALLOWED_STATE_MACHINE_GRANULARITIES = frozenset({"MATERIAL_LIFECYCLE"})
_MATERIAL_UNIT_SESSION_TYPE = "MATERIAL_UNIT"
_MATERIAL_UNIT_PHYSICAL_FORM = "REEL"
_MATERIAL_UNIT_OWNER_MODEL = "MaterialUnit"
_MATERIAL_UNIT_OWNER_FIELD = "status"
# 从 MaterialUnitStatus 枚举派生，避免硬编码副本与枚举漂移。
_MATERIAL_UNIT_STATUS_VALUES = frozenset({status.value for status in MaterialUnitStatus})
if frozenset({"IN_TRANSIT", "STORED", "COMPLETED", "NG", "RECONCILING"}) != _MATERIAL_UNIT_STATUS_VALUES:
    raise RuntimeError(
        f"MaterialUnitStatus 枚举与 manifest 合同状态集漂移，需同步设计文档: {sorted(_MATERIAL_UNIT_STATUS_VALUES)}"
    )
_TERMINAL_EXCEPTION_STATES = frozenset({"NG", "RECONCILING"})
_MISSING_TOPOLOGY = object()
_EnumT = TypeVar("_EnumT", bound=Enum)
_YAML_LEGACY_FIELDS = frozenset(
    {
        "rack_position_args",
        "payload_schema_ref",
        "result_bindings",
        "result",
        "classification",
        "terminal",
        "next_event",
    }
)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """拒绝重复 mapping key 的 SafeLoader。"""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(
            None,
            None,
            f"expected a mapping node, but found {node.id}",
            node.start_mark,
        )

    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            )
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_unique_key_yaml(text: str) -> Any:
    """使用 SafeLoader 子类加载 YAML，并拒绝重复 mapping key。"""

    loader = _UniqueKeySafeLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


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


def _ensure_material_unit_status(value: str, *, field_name: str) -> None:
    if value not in _MATERIAL_UNIT_STATUS_VALUES:
        allowed = ", ".join(sorted(_MATERIAL_UNIT_STATUS_VALUES))
        raise ValueError(f"{field_name} value {value!r} must be one of: {allowed}")


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


@dataclass(frozen=True, slots=True)
class CommandBinding:
    """插件命令及目标设备角色。"""

    command: str
    target_device_role: str

    def __post_init__(self) -> None:
        if not _non_empty_str(self.command):
            raise ValueError("CommandBinding.command must be a non-empty string")
        if not _non_empty_str(self.target_device_role):
            raise ValueError("CommandBinding.target_device_role must be a non-empty string")


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
class SessionSubject:
    """插件运行会话的业务主体。"""

    type: str
    physical_form: str
    identity_sources: tuple[str, ...] | list[str] | set[str] | frozenset[str]

    def __post_init__(self) -> None:
        if not _non_empty_str(self.type):
            raise ValueError("SessionSubject.type must be a non-empty string")
        if not _non_empty_str(self.physical_form):
            raise ValueError("SessionSubject.physical_form must be a non-empty string")
        identity_sources = _string_tuple(self.identity_sources, field_name="SessionSubject.identity_sources")
        if not identity_sources:
            raise ValueError("SessionSubject.identity_sources must not be empty")
        object.__setattr__(self, "identity_sources", identity_sources)


@dataclass(frozen=True, slots=True)
class StateMachineSubject:
    """状态机绑定的业务主体。"""

    category: str
    type: str
    physical_form: str

    def __post_init__(self) -> None:
        for field_name in ("category", "type", "physical_form"):
            if not _non_empty_str(getattr(self, field_name)):
                raise ValueError(f"StateMachineSubject.{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class StateMachineOwner:
    """状态机状态字段归属。"""

    model: str
    field: str

    def __post_init__(self) -> None:
        if self.model != _MATERIAL_UNIT_OWNER_MODEL:
            raise ValueError(f"StateMachineOwner.model must be {_MATERIAL_UNIT_OWNER_MODEL}")
        if self.field != _MATERIAL_UNIT_OWNER_FIELD:
            raise ValueError(f"StateMachineOwner.field must be {_MATERIAL_UNIT_OWNER_FIELD}")


@dataclass(frozen=True, slots=True)
class StateMachineTransition:
    """状态机允许的状态流转。"""

    from_state: str
    to_states: tuple[str, ...] | list[str] | set[str] | frozenset[str]

    def __post_init__(self) -> None:
        if not _non_empty_str(self.from_state):
            raise ValueError("StateMachineTransition.from_state must be a non-empty string")
        _ensure_material_unit_status(self.from_state, field_name="StateMachineTransition.from_state")
        to_states = _string_tuple(self.to_states, field_name="StateMachineTransition.to_states")
        for index, to_state in enumerate(to_states):
            _ensure_material_unit_status(to_state, field_name=f"StateMachineTransition.to_states[{index}]")
        object.__setattr__(
            self,
            "to_states",
            to_states,
        )


@dataclass(frozen=True, slots=True)
class StateMachine:
    """插件声明的业务状态机。"""

    id: str
    subject: StateMachineSubject
    state_owner: StateMachineOwner
    granularity: str
    transitions: tuple[StateMachineTransition, ...] | list[StateMachineTransition]

    def __post_init__(self) -> None:
        if not _non_empty_str(self.id):
            raise ValueError("StateMachine.id must be a non-empty string")
        if not isinstance(self.subject, StateMachineSubject):
            raise TypeError("StateMachine.subject must be StateMachineSubject")
        if not isinstance(self.state_owner, StateMachineOwner):
            raise TypeError("StateMachine.state_owner must be StateMachineOwner")
        if not _non_empty_str(self.granularity):
            raise ValueError("StateMachine.granularity must be a non-empty string")
        if self.granularity not in _ALLOWED_STATE_MACHINE_GRANULARITIES:
            allowed = ", ".join(sorted(_ALLOWED_STATE_MACHINE_GRANULARITIES))
            raise ValueError(f"StateMachine.granularity must be one of: {allowed}, got: {self.granularity!r}")

        transitions = tuple(self.transitions)
        if not transitions:
            raise ValueError("StateMachine.transitions must not be empty")
        if not all(isinstance(transition, StateMachineTransition) for transition in transitions):
            raise TypeError("StateMachine.transitions must contain only StateMachineTransition")
        _ensure_unique(
            tuple(transition.from_state for transition in transitions),
            field_name="StateMachine.transitions.from_state",
        )
        self._warn_missing_terminal_exception_exits(transitions)
        object.__setattr__(self, "transitions", transitions)

    def _warn_missing_terminal_exception_exits(self, transitions: tuple[StateMachineTransition, ...]) -> None:
        declared_from_states = {transition.from_state for transition in transitions}
        missing_exit_states = sorted(_TERMINAL_EXCEPTION_STATES - declared_from_states)
        if missing_exit_states:
            logger.warning(
                "StateMachine %s missing material unit status exits: %s",
                self.id,
                ", ".join(missing_exit_states),
            )


@dataclass(frozen=True, slots=True)
class PipelineQueue:
    """插件声明的管线队列。"""

    code: str
    role: str
    capacity: int | str
    order_policy: str = "FIFO"

    def __post_init__(self) -> None:
        if not _non_empty_str(self.code):
            raise ValueError("PipelineQueue.code must be a non-empty string")
        if not _non_empty_str(self.role):
            raise ValueError("PipelineQueue.role must be a non-empty string")
        if self.role not in _ALLOWED_PIPELINE_QUEUE_ROLES:
            allowed = ", ".join(sorted(_ALLOWED_PIPELINE_QUEUE_ROLES))
            raise ValueError(f"PipelineQueue.role must be one of: {allowed}, got: {self.role!r}")
        if isinstance(self.capacity, bool):
            raise TypeError("PipelineQueue.capacity must be a positive integer or MANY")
        if isinstance(self.capacity, int):
            if self.capacity <= 0:
                raise ValueError("PipelineQueue.capacity must be a positive integer or MANY")
        elif self.capacity != "MANY":
            raise ValueError("PipelineQueue.capacity must be a positive integer or MANY")
        if self.order_policy not in _ALLOWED_PIPELINE_ORDER_POLICIES:
            allowed = ", ".join(sorted(_ALLOWED_PIPELINE_ORDER_POLICIES))
            raise ValueError(f"PipelineQueue.order_policy must be one of: {allowed}")


def _yaml_path(parent: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if parent == "<root>":
        return key
    return f"{parent}.{key}"


def _yaml_mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be an object")
    return value


def _yaml_sequence(value: Any, *, path: str) -> Sequence[Any]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"{path} must be a list")
    return value


def _yaml_required_str(mapping: Mapping[str, Any], key: str, *, path: str) -> str:
    value = mapping.get(key)
    field_path = _yaml_path(path, key)
    required_value = _non_empty_str(value)
    if required_value is None:
        raise ValueError(f"{field_path} must be a non-empty string")
    return required_value


def _yaml_int_field(
    mapping: Mapping[str, Any],
    key: str,
    *,
    path: str,
    default: int | None,
    allow_null: bool,
) -> int | None:
    if key not in mapping:
        return default
    value = mapping[key]
    field_path = _yaml_path(path, key)
    if value is None:
        if allow_null:
            return None
        raise ValueError(f"{field_path} must be an integer and cannot be null")
    if isinstance(value, bool):
        raise TypeError(f"{field_path} must be an integer, not boolean")
    if not isinstance(value, int):
        expected = "an integer or null" if allow_null else "an integer"
        raise TypeError(f"{field_path} must be {expected}")
    return value


def _yaml_int(mapping: Mapping[str, Any], key: str, *, path: str, default: int) -> int:
    return cast("int", _yaml_int_field(mapping, key, path=path, default=default, allow_null=False))


def _yaml_nullable_int(mapping: Mapping[str, Any], key: str, *, path: str, default: int | None) -> int | None:
    return _yaml_int_field(mapping, key, path=path, default=default, allow_null=True)


def _yaml_str_list(value: Any, *, path: str) -> tuple[str, ...]:
    items = _yaml_sequence(value, path=path)
    normalized: list[str] = []
    for index, item in enumerate(items):
        if not _non_empty_str(item):
            raise ValueError(f"{_yaml_path(path, index)} must be a non-empty string")
        normalized.append(item)
    return tuple(normalized)


def _expect_yaml_keys(mapping: Mapping[str, Any], allowed: set[str], *, path: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{path} contains unsupported field: {', '.join(unknown)}")


def _reject_legacy_yaml_fields(value: Any, *, path: str = "<root>") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_path = _yaml_path(path, str(key))
            if key in _YAML_LEGACY_FIELDS:
                raise ValueError(f"{key_path} is a removed payload binding field")
            _reject_legacy_yaml_fields(item, path=key_path)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_legacy_yaml_fields(item, path=_yaml_path(path, index))


def _project_yaml_device_roles(
    device_roles: Mapping[str, Any],
) -> tuple[tuple[DeviceRequirement, ...], tuple[CommandBinding, ...], tuple[EventBinding, ...]]:
    devices: list[DeviceRequirement] = []
    commands: list[CommandBinding] = []
    event_sources: dict[str, tuple[EventCategory, list[str]]] = {}
    command_targets: dict[str, str] = {}

    for role, raw_role_spec in device_roles.items():
        if not _non_empty_str(role):
            raise ValueError("device_roles keys must be non-empty strings")
        role_path = _yaml_path("device_roles", role)
        role_spec = _yaml_mapping(raw_role_spec, path=role_path)
        _expect_yaml_keys(
            role_spec,
            {"min_count", "max_count", "hardware_capabilities", "commands", "events"},
            path=role_path,
        )
        devices.append(
            DeviceRequirement(
                role=role,
                min_count=_yaml_int(role_spec, "min_count", path=role_path, default=1),
                max_count=_yaml_nullable_int(role_spec, "max_count", path=role_path, default=None),
                hardware_capabilities=_yaml_str_list(
                    role_spec.get("hardware_capabilities", []),
                    path=_yaml_path(role_path, "hardware_capabilities"),
                ),
            )
        )

        role_commands = _yaml_str_list(role_spec.get("commands", []), path=_yaml_path(role_path, "commands"))
        _ensure_unique(role_commands, field_name=f"{role_path}.commands")
        for command in role_commands:
            existing_role = command_targets.get(command)
            if existing_role is not None:
                raise ValueError(f"duplicate command {command} in device_roles: {existing_role}, {role}")
            command_targets[command] = role
            commands.append(CommandBinding(command=command, target_device_role=role))

        raw_events = _yaml_sequence(role_spec.get("events", []), path=_yaml_path(role_path, "events"))
        seen_role_events: list[str] = []
        for index, raw_event in enumerate(raw_events):
            event_path = _yaml_path(_yaml_path(role_path, "events"), index)
            event_spec = _yaml_mapping(raw_event, path=event_path)
            _expect_yaml_keys(event_spec, {"event", "category"}, path=event_path)
            event_name = _yaml_required_str(event_spec, "event", path=event_path)
            category = _coerce_enum(
                EventCategory,
                _yaml_required_str(event_spec, "category", path=event_path),
                field_name=f"{event_path}.category",
            )
            seen_role_events.append(event_name)
            existing = event_sources.get(event_name)
            if existing is None:
                event_sources[event_name] = (category, [role])
            else:
                existing_category, roles = existing
                if existing_category != category:
                    raise ValueError(f"{event_path}.category conflicts with event {event_name} category")
                roles.append(role)
        _ensure_unique(tuple(seen_role_events), field_name=f"{role_path}.events.event")

    events = tuple(
        EventBinding(event=event_name, source_device_roles=tuple(roles), category=category)
        for event_name, (category, roles) in event_sources.items()
    )
    return tuple(devices), tuple(commands), events


def _project_yaml_rack_positions(value: Any) -> tuple[RackPosition, ...]:
    items = _yaml_sequence(value, path="rack_positions")
    rack_positions: list[RackPosition] = []
    for index, raw_item in enumerate(items):
        path = _yaml_path("rack_positions", index)
        item = _yaml_mapping(raw_item, path=path)
        _expect_yaml_keys(item, {"code", "role", "station_code", "carrier_capability"}, path=path)
        capability_path = _yaml_path(path, "carrier_capability")
        capability = _yaml_mapping(item.get("carrier_capability"), path=capability_path)
        _expect_yaml_keys(
            capability,
            {"allowed_rack_kinds", "allowed_slot_kinds", "min_capacity", "max_capacity"},
            path=capability_path,
        )
        rack_positions.append(
            RackPosition(
                code=_yaml_required_str(item, "code", path=path),
                role=_yaml_required_str(item, "role", path=path),
                station_code=_yaml_required_str(item, "station_code", path=path),
                carrier_capability=RackPositionCarrierCapability(
                    allowed_rack_kinds=_yaml_str_list(
                        capability.get("allowed_rack_kinds", []),
                        path=_yaml_path(capability_path, "allowed_rack_kinds"),
                    ),
                    min_capacity=_yaml_int(capability, "min_capacity", path=capability_path, default=1),
                    max_capacity=_yaml_int(capability, "max_capacity", path=capability_path, default=1),
                    allowed_slot_kinds=_yaml_str_list(
                        capability.get("allowed_slot_kinds", []),
                        path=_yaml_path(capability_path, "allowed_slot_kinds"),
                    ),
                ),
            )
        )
    return tuple(rack_positions)


def _project_yaml_node_ref(value: Any, *, path: str) -> NodeRef:
    node = _yaml_mapping(value, path=path)
    _expect_yaml_keys(node, {"kind", "ref"}, path=path)
    return NodeRef(
        kind=_coerce_enum(
            NodeRefKind,
            _yaml_required_str(node, "kind", path=path),
            field_name=f"{path}.kind",
        ),
        ref=_yaml_required_str(node, "ref", path=path),
    )


def _project_yaml_topology(value: Any) -> TopologySpec:
    topology = _yaml_mapping(value, path="topology")
    _expect_yaml_keys(topology, {"flow_edges"}, path="topology")
    edges = _yaml_sequence(topology.get("flow_edges"), path="topology.flow_edges")
    flow_edges: list[FlowEdge] = []
    for index, raw_edge in enumerate(edges):
        path = _yaml_path("topology.flow_edges", index)
        edge = _yaml_mapping(raw_edge, path=path)
        _expect_yaml_keys(edge, {"from", "to", "type"}, path=path)
        flow_edges.append(
            FlowEdge(
                from_node=_project_yaml_node_ref(edge.get("from"), path=_yaml_path(path, "from")),
                to_node=_project_yaml_node_ref(edge.get("to"), path=_yaml_path(path, "to")),
                type=_coerce_enum(
                    FlowEdgeType,
                    _yaml_required_str(edge, "type", path=path),
                    field_name=f"{path}.type",
                ),
            )
        )
    return TopologySpec(flow_edges=tuple(flow_edges))


def _project_yaml_resource_boundaries(value: Any) -> tuple[ResourceBoundary, ...]:
    items = _yaml_sequence(value, path="resource_boundaries")
    boundaries: list[ResourceBoundary] = []
    allowed = {
        "rack_position_code",
        "rack_kind",
        "business_demand_type",
        "wms_operation_type",
        "snapshot_kind",
        "lease_scope",
    }
    for index, raw_item in enumerate(items):
        path = _yaml_path("resource_boundaries", index)
        item = _yaml_mapping(raw_item, path=path)
        _expect_yaml_keys(item, allowed, path=path)
        boundaries.append(
            ResourceBoundary(
                rack_position_code=_yaml_required_str(item, "rack_position_code", path=path),
                rack_kind=_yaml_required_str(item, "rack_kind", path=path),
                business_demand_type=_yaml_required_str(item, "business_demand_type", path=path),
                wms_operation_type=_yaml_required_str(item, "wms_operation_type", path=path),
                snapshot_kind=_yaml_required_str(item, "snapshot_kind", path=path),
                lease_scope=_yaml_required_str(item, "lease_scope", path=path),
            )
        )
    return tuple(boundaries)


def _project_yaml_session_subject(value: Any) -> SessionSubject:
    subject = _yaml_mapping(value, path="session_subject")
    _expect_yaml_keys(subject, {"type", "physical_form", "identity_sources"}, path="session_subject")
    return SessionSubject(
        type=_yaml_required_str(subject, "type", path="session_subject"),
        physical_form=_yaml_required_str(subject, "physical_form", path="session_subject"),
        identity_sources=_yaml_str_list(
            subject.get("identity_sources", []),
            path="session_subject.identity_sources",
        ),
    )


def _project_yaml_state_machine_subject(value: Any, *, path: str) -> StateMachineSubject:
    subject = _yaml_mapping(value, path=path)
    _expect_yaml_keys(subject, {"category", "type", "physical_form"}, path=path)
    return StateMachineSubject(
        category=_yaml_required_str(subject, "category", path=path),
        type=_yaml_required_str(subject, "type", path=path),
        physical_form=_yaml_required_str(subject, "physical_form", path=path),
    )


def _project_yaml_state_machine_owner(value: Any, *, path: str) -> StateMachineOwner:
    owner = _yaml_mapping(value, path=path)
    _expect_yaml_keys(owner, {"model", "field"}, path=path)
    try:
        return StateMachineOwner(
            model=_yaml_required_str(owner, "model", path=path),
            field=_yaml_required_str(owner, "field", path=path),
        )
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _project_yaml_state_machine_transitions(value: Any, *, path: str) -> tuple[StateMachineTransition, ...]:
    items = _yaml_sequence(value, path=path)
    transitions: list[StateMachineTransition] = []
    for index, raw_item in enumerate(items):
        item_path = _yaml_path(path, index)
        item = _yaml_mapping(raw_item, path=item_path)
        _expect_yaml_keys(item, {"from", "to"}, path=item_path)
        try:
            transitions.append(
                StateMachineTransition(
                    from_state=_yaml_required_str(item, "from", path=item_path),
                    to_states=_yaml_str_list(item.get("to", []), path=_yaml_path(item_path, "to")),
                )
            )
        except ValueError as exc:
            raise ValueError(str(exc).replace("from_state", "from").replace("to_states", "to")) from exc

    from_states = tuple(transition.from_state for transition in transitions)
    duplicates = sorted({from_state for from_state in from_states if from_states.count(from_state) > 1})
    if duplicates:
        raise ValueError(f"{path}.from must be unique: {', '.join(duplicates)}")
    return tuple(transitions)


def _project_yaml_state_machines(value: Any) -> tuple[StateMachine, ...]:
    items = _yaml_sequence(value, path="state_machines")
    state_machines: list[StateMachine] = []
    for index, raw_item in enumerate(items):
        path = _yaml_path("state_machines", index)
        item = _yaml_mapping(raw_item, path=path)
        _expect_yaml_keys(
            item,
            {"id", "subject", "state_owner", "granularity", "transitions"},
            path=path,
        )
        try:
            state_machines.append(
                StateMachine(
                    id=_yaml_required_str(item, "id", path=path),
                    subject=_project_yaml_state_machine_subject(item.get("subject"), path=_yaml_path(path, "subject")),
                    state_owner=_project_yaml_state_machine_owner(
                        item.get("state_owner"),
                        path=_yaml_path(path, "state_owner"),
                    ),
                    granularity=_yaml_required_str(item, "granularity", path=path),
                    transitions=_project_yaml_state_machine_transitions(
                        item.get("transitions"),
                        path=_yaml_path(path, "transitions"),
                    ),
                )
            )
        except ValueError as exc:
            raise ValueError(
                str(exc).replace("StateMachine.transitions.from_state", f"{path}.transitions.from")
            ) from exc
    return tuple(state_machines)


def _yaml_pipeline_capacity(mapping: Mapping[str, Any], *, path: str) -> int | str:
    value = mapping.get("capacity")
    field_path = _yaml_path(path, "capacity")
    if isinstance(value, bool):
        raise TypeError(f"{field_path} must be a positive integer or MANY")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"{field_path} must be a positive integer or MANY")
        return value
    if value == "MANY":
        return "MANY"
    raise ValueError(f"{field_path} must be a positive integer or MANY")


def _project_yaml_pipeline_queues(value: Any) -> tuple[PipelineQueue, ...]:
    items = _yaml_sequence(value, path="pipeline_queues")
    queues: list[PipelineQueue] = []
    for index, raw_item in enumerate(items):
        path = _yaml_path("pipeline_queues", index)
        item = _yaml_mapping(raw_item, path=path)
        _expect_yaml_keys(item, {"code", "role", "capacity", "order_policy"}, path=path)
        order_policy = _yaml_required_str(item, "order_policy", path=path) if "order_policy" in item else "FIFO"
        if order_policy not in _ALLOWED_PIPELINE_ORDER_POLICIES:
            allowed = ", ".join(sorted(_ALLOWED_PIPELINE_ORDER_POLICIES))
            raise ValueError(f"{_yaml_path(path, 'order_policy')} must be one of: {allowed}")
        try:
            queues.append(
                PipelineQueue(
                    code=_yaml_required_str(item, "code", path=path),
                    role=_yaml_required_str(item, "role", path=path),
                    capacity=_yaml_pipeline_capacity(item, path=path),
                    order_policy=order_policy,
                )
            )
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    return tuple(queues)


def _validate_yaml_topology_refs(
    topology: TopologySpec,
    *,
    device_roles: set[str],
    rack_position_codes: set[str],
) -> None:
    for index, edge in enumerate(topology.flow_edges):
        edge_path = _yaml_path("topology.flow_edges", index)
        if edge.type == FlowEdgeType.MATERIAL_FLOW:
            for side, node_ref in (("from", edge.from_node), ("to", edge.to_node)):
                if node_ref.kind != NodeRefKind.RACK_POSITION:
                    raise ValueError(
                        f"{_yaml_path(_yaml_path(edge_path, side), 'kind')} must be RACK_POSITION for "
                        "MATERIAL_FLOW edges"
                    )
        elif (
            NodeRefKind.DEVICE_ROLE in {edge.from_node.kind, edge.to_node.kind} and edge.type != FlowEdgeType.OPERATION
        ):
            raise ValueError(f"{_yaml_path(edge_path, 'type')} must be OPERATION for edges involving DEVICE_ROLE")

        for side, node_ref in (("from", edge.from_node), ("to", edge.to_node)):
            ref_path = _yaml_path(_yaml_path(edge_path, side), "ref")
            if node_ref.kind == NodeRefKind.DEVICE_ROLE and node_ref.ref not in device_roles:
                raise ValueError(f"{ref_path} DEVICE_ROLE ref is not declared in device_roles: {node_ref.ref}")
            if node_ref.kind == NodeRefKind.RACK_POSITION and node_ref.ref not in rack_position_codes:
                raise ValueError(f"{ref_path} RACK_POSITION ref is not declared in rack_positions: {node_ref.ref}")


def _validate_yaml_resource_boundaries(
    resource_boundaries: tuple[ResourceBoundary, ...],
    *,
    rack_positions_by_code: dict[str, RackPosition],
) -> None:
    for index, boundary in enumerate(resource_boundaries):
        boundary_path = _yaml_path("resource_boundaries", index)
        rack_position = rack_positions_by_code.get(boundary.rack_position_code)
        if rack_position is None:
            raise ValueError(
                f"{_yaml_path(boundary_path, 'rack_position_code')} is not declared in rack_positions: "
                f"{boundary.rack_position_code}"
            )
        if boundary.rack_kind not in rack_position.carrier_capability.allowed_rack_kinds:
            allowed_rack_kinds = ", ".join(rack_position.carrier_capability.allowed_rack_kinds)
            raise ValueError(
                f"{_yaml_path(boundary_path, 'rack_kind')} is not allowed by "
                "rack_positions.carrier_capability.allowed_rack_kinds: "
                f"rack_position_code={boundary.rack_position_code}, rack_kind={boundary.rack_kind}, "
                f"allowed_rack_kinds={allowed_rack_kinds}"
            )


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
    session_subject: SessionSubject | None = None
    state_machines: tuple[StateMachine, ...] | list[StateMachine] = field(default_factory=tuple)
    pipeline_queues: tuple[PipelineQueue, ...] | list[PipelineQueue] = field(default_factory=tuple)

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> WorklinePluginManifest:
        """从插件目录内 manifest.yaml 加载静态合同。"""

        manifest_path = Path(path)
        try:
            raw = _load_unique_key_yaml(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"{manifest_path}: YAML parse failed: {exc}") from exc
        except OSError as exc:
            raise ValueError(f"{manifest_path}: failed to read manifest YAML: {exc}") from exc

        try:
            return cls.from_yaml_dict(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{manifest_path}: {exc}") from exc

    @classmethod
    def from_yaml_dict(cls, data: Mapping[str, Any]) -> WorklinePluginManifest:
        """从 role-centered YAML authoring 结构投影为运行时 manifest。"""

        root = _yaml_mapping(data, path="<root>")
        _reject_legacy_yaml_fields(root)
        _expect_yaml_keys(
            root,
            {
                "plugin_key",
                "contract_version",
                "device_roles",
                "rack_positions",
                "topology",
                "resource_boundaries",
                "session_subject",
                "state_machines",
                "pipeline_queues",
            },
            path="<root>",
        )

        plugin_key = _yaml_required_str(root, "plugin_key", path="<root>")
        contract_version = _yaml_required_str(root, "contract_version", path="<root>")
        device_roles = _yaml_mapping(root.get("device_roles"), path="device_roles")
        if not device_roles:
            raise ValueError("device_roles must not be empty")

        devices, commands, events = _project_yaml_device_roles(device_roles)
        rack_positions = _project_yaml_rack_positions(root.get("rack_positions"))
        topology = _project_yaml_topology(root.get("topology"))
        resource_boundaries = _project_yaml_resource_boundaries(root.get("resource_boundaries"))
        session_subject = _project_yaml_session_subject(root["session_subject"]) if "session_subject" in root else None
        state_machines = _project_yaml_state_machines(root.get("state_machines", []))
        pipeline_queues = _project_yaml_pipeline_queues(root.get("pipeline_queues", []))
        device_role_set = {device.role for device in devices}
        rack_positions_by_code = {rack_position.code: rack_position for rack_position in rack_positions}
        rack_position_code_set = set(rack_positions_by_code)
        _validate_yaml_topology_refs(
            topology,
            device_roles=device_role_set,
            rack_position_codes=rack_position_code_set,
        )
        _validate_yaml_resource_boundaries(
            resource_boundaries,
            rack_positions_by_code=rack_positions_by_code,
        )

        return cls(
            plugin_key=plugin_key,
            contract_version=contract_version,
            devices=devices,
            rack_positions=rack_positions,
            topology=topology,
            commands=commands,
            events=events,
            resource_boundaries=resource_boundaries,
            session_subject=session_subject,
            state_machines=state_machines,
            pipeline_queues=pipeline_queues,
        )

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
        if self.session_subject is not None and not isinstance(self.session_subject, SessionSubject):
            raise TypeError("manifest.session_subject must be SessionSubject")
        state_machines = tuple(self.state_machines)
        if not all(isinstance(state_machine, StateMachine) for state_machine in state_machines):
            raise TypeError("manifest.state_machines must contain only StateMachine")
        _ensure_unique(
            tuple(state_machine.id for state_machine in state_machines), field_name="manifest.state_machines.id"
        )
        pipeline_queues = tuple(self.pipeline_queues)
        if not all(isinstance(queue, PipelineQueue) for queue in pipeline_queues):
            raise TypeError("manifest.pipeline_queues must contain only PipelineQueue")
        _ensure_unique(tuple(queue.code for queue in pipeline_queues), field_name="manifest.pipeline_queues.code")

        device_role_set = set(device_roles)
        rack_positions_by_code = {rack_position.code: rack_position for rack_position in rack_positions}
        rack_position_code_set = set(rack_position_codes)
        self._validate_events(events, device_role_set)
        self._validate_commands(commands, device_role_set)
        self._validate_resource_boundaries(resource_boundaries, rack_positions_by_code)
        self._validate_topology_refs(topology, device_role_set, rack_position_code_set)
        self._validate_state_machines(state_machines, self.session_subject)

        object.__setattr__(self, "devices", devices)
        object.__setattr__(self, "rack_positions", rack_positions)
        object.__setattr__(self, "topology", topology)
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "resource_boundaries", resource_boundaries)
        object.__setattr__(self, "state_machines", state_machines)
        object.__setattr__(self, "pipeline_queues", pipeline_queues)

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
    ) -> None:
        _ensure_unique(tuple(command.command for command in commands), field_name="manifest.commands.command")
        for command in commands:
            if command.target_device_role not in device_roles:
                raise ValueError(
                    f"CommandBinding {command.command} target role is not declared in manifest.devices: "
                    f"{command.target_device_role}"
                )

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
    def _validate_state_machines(
        state_machines: tuple[StateMachine, ...],
        session_subject: SessionSubject | None,
    ) -> None:
        if state_machines and session_subject is None:
            raise ValueError("manifest.session_subject must be declared when manifest.state_machines is declared")
        if session_subject is not None:
            if session_subject.type != _MATERIAL_UNIT_SESSION_TYPE:
                raise ValueError(f"manifest.session_subject.type must be {_MATERIAL_UNIT_SESSION_TYPE}")
            if session_subject.physical_form != _MATERIAL_UNIT_PHYSICAL_FORM:
                raise ValueError(f"manifest.session_subject.physical_form must be {_MATERIAL_UNIT_PHYSICAL_FORM}")

        for state_machine in state_machines:
            if session_subject is None:
                continue
            if state_machine.subject.category != session_subject.type:
                raise ValueError(
                    "manifest.state_machines.subject.category must match manifest.session_subject.type: "
                    f"{state_machine.subject.category} != {session_subject.type}"
                )
            if state_machine.subject.type != session_subject.type:
                raise ValueError(
                    "manifest.state_machines.subject.type must match manifest.session_subject.type: "
                    f"{state_machine.subject.type} != {session_subject.type}"
                )
            if state_machine.subject.physical_form != session_subject.physical_form:
                raise ValueError(
                    "manifest.state_machines.subject.physical_form must match "
                    "manifest.session_subject.physical_form: "
                    f"{state_machine.subject.physical_form} != {session_subject.physical_form}"
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
            if edge.type == FlowEdgeType.MATERIAL_FLOW:
                pass
            elif (
                NodeRefKind.DEVICE_ROLE in {edge.from_node.kind, edge.to_node.kind}
                and edge.type != FlowEdgeType.OPERATION
            ):
                raise ValueError("FlowEdge edges involving DEVICE_ROLE must use OPERATION")
            for node_ref in (edge.from_node, edge.to_node):
                if node_ref.kind == NodeRefKind.DEVICE_ROLE and node_ref.ref not in device_roles:
                    raise ValueError(
                        f"Topology NodeRef DEVICE_ROLE ref is not declared in manifest.devices: {node_ref.ref}"
                    )
                if node_ref.kind == NodeRefKind.RACK_POSITION and node_ref.ref not in rack_position_codes:
                    raise ValueError(
                        f"Topology NodeRef RACK_POSITION ref is not declared in manifest.rack_positions: {node_ref.ref}"
                    )

    def validate_resource_wait_subject(self, *, subject_type: str, projection_type: str) -> None:
        """校验 RESOURCE_WAIT 指向 manifest 已声明的主体/投影类型。"""

        if not _non_empty_str(subject_type):
            raise ValueError("RESOURCE_WAIT subject_type must be a non-empty string")
        if not _non_empty_str(projection_type):
            raise ValueError("RESOURCE_WAIT projection_type must be a non-empty string")

        declared_subjects: set[tuple[str, str]] = set()
        if self.session_subject is not None:
            declared_subjects.add((self.session_subject.type, "SESSION_SUBJECT"))
        for state_machine in self.state_machines:
            declared_subjects.add((state_machine.subject.type, state_machine.granularity))
        for queue in self.pipeline_queues:
            declared_subjects.add((queue.code, "QUEUE_MEMBERSHIP"))
        for boundary in self.resource_boundaries:
            declared_subjects.add((boundary.rack_position_code, boundary.snapshot_kind))
            declared_subjects.add((boundary.rack_kind, boundary.snapshot_kind))
            declared_subjects.add((f"{boundary.rack_position_code}:{boundary.rack_kind}", boundary.snapshot_kind))
            declared_subjects.add((boundary.rack_position_code, f"{boundary.lease_scope}_LEASE"))

        if (subject_type, projection_type) in declared_subjects:
            return

        declared_text = ", ".join(
            f"{declared_subject}/{declared_projection}"
            for declared_subject, declared_projection in sorted(declared_subjects)
        )
        raise ValueError(
            "RESOURCE_WAIT subject is not declared in manifest: "
            f"subject_type={subject_type}, projection_type={projection_type}, declared={declared_text}"
        )


__all__ = [
    "CommandBinding",
    "DeviceRequirement",
    "EventBinding",
    "EventCategory",
    "FlowEdge",
    "FlowEdgeType",
    "NodeRef",
    "NodeRefKind",
    "PipelineQueue",
    "RackPosition",
    "RackPositionCarrierCapability",
    "ResourceBoundary",
    "SessionSubject",
    "StateMachine",
    "StateMachineOwner",
    "StateMachineSubject",
    "StateMachineTransition",
    "TopologySpec",
    "WorklinePluginManifest",
]
