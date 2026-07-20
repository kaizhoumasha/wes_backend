"""Workline Plugin Definition 的类型化展示与拓扑声明。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DeviceRequirement:
    role: str
    min_count: int = 1
    max_count: int | None = None
    hardware_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RackPositionCarrierCapability:
    allowed_rack_kinds: tuple[str, ...]
    min_capacity: int = 1
    max_capacity: int = 1
    allowed_slot_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RackPosition:
    code: str
    role: str
    station_code: str
    carrier_capability: RackPositionCarrierCapability


@dataclass(frozen=True, slots=True)
class NodeRef:
    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class FlowEdge:
    from_node: NodeRef
    to_node: NodeRef
    type: str


@dataclass(frozen=True, slots=True)
class TopologySpec:
    flow_edges: tuple[FlowEdge, ...] = ()


@dataclass(frozen=True, slots=True)
class EventBinding:
    event: str
    source_device_roles: tuple[str, ...]
    category: str


@dataclass(frozen=True, slots=True)
class CommandBinding:
    command: str
    target_device_role: str


@dataclass(frozen=True, slots=True)
class ResourceBoundary:
    rack_position_code: str
    rack_kind: str
    business_demand_type: str
    wms_operation_type: str
    snapshot_kind: str
    lease_scope: str


@dataclass(frozen=True, slots=True)
class SessionSubject:
    type: str
    physical_form: str
    identity_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StateMachineSubject:
    category: str
    type: str
    physical_form: str


@dataclass(frozen=True, slots=True)
class StateMachineOwner:
    model: str
    field: str


@dataclass(frozen=True, slots=True)
class StateMachineTransition:
    from_state: str
    to_states: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StateMachine:
    id: str
    subject: StateMachineSubject
    state_owner: StateMachineOwner
    granularity: str
    transitions: tuple[StateMachineTransition, ...]


@dataclass(frozen=True, slots=True)
class StateMachineContractProfile:
    """构建期允许的状态机 owner、粒度与状态值合同。"""

    subject_type: str
    owner_model: str
    owner_field: str
    granularity: str
    status_contract: str
    allowed_states: frozenset[str]


STATE_MACHINE_CONTRACT_PROFILES = (
    StateMachineContractProfile(
        subject_type="MATERIAL_UNIT",
        owner_model="MaterialUnit",
        owner_field="status",
        granularity="MATERIAL_LIFECYCLE",
        status_contract="MaterialUnitStatus",
        allowed_states=frozenset({"IN_TRANSIT", "STORED", "COMPLETED", "NG", "RECONCILING"}),
    ),
)


@dataclass(frozen=True, slots=True)
class PipelineQueue:
    code: str
    role: str
    capacity: int | str
    order_policy: str


@dataclass(frozen=True, slots=True)
class WorklinePluginSchema:
    """由 Python Definition 持有的唯一展示/拓扑 schema。"""

    devices: tuple[DeviceRequirement, ...] = ()
    rack_positions: tuple[RackPosition, ...] = ()
    topology: TopologySpec = field(default_factory=TopologySpec)
    events: tuple[EventBinding, ...] = ()
    commands: tuple[CommandBinding, ...] = ()
    resource_boundaries: tuple[ResourceBoundary, ...] = ()
    session_subject: SessionSubject | None = None
    state_machines: tuple[StateMachine, ...] = ()
    pipeline_queues: tuple[PipelineQueue, ...] = ()

    def validate_resource_wait_subject(self, *, subject_type: str, projection_type: str) -> None:
        allowed_pairs = {
            (boundary.business_demand_type, boundary.snapshot_kind) for boundary in self.resource_boundaries
        }
        if (subject_type, projection_type) not in allowed_pairs:
            raise ValueError("RESOURCE_WAIT subject/projection must belong to the same resource boundary")


__all__ = [
    "STATE_MACHINE_CONTRACT_PROFILES",
    "CommandBinding",
    "DeviceRequirement",
    "EventBinding",
    "FlowEdge",
    "NodeRef",
    "PipelineQueue",
    "RackPosition",
    "RackPositionCarrierCapability",
    "ResourceBoundary",
    "SessionSubject",
    "StateMachine",
    "StateMachineContractProfile",
    "StateMachineOwner",
    "StateMachineSubject",
    "StateMachineTransition",
    "TopologySpec",
    "WorklinePluginSchema",
]
