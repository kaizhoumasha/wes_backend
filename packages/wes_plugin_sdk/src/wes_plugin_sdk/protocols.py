"""插件 handler 可依赖的最窄只读快照协议。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


class ExecutionLifecycle(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    HOLD = "HOLD"
    CLOSED = "CLOSED"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    material_execution_id: str
    material_trace_id: str
    line_run_epoch_id: str
    lifecycle: ExecutionLifecycle
    version: int

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.material_trace_id, "material_trace_id")
        _required(self.line_run_epoch_id, "line_run_epoch_id")
        if not isinstance(self.lifecycle, ExecutionLifecycle):
            raise ValueError("lifecycle must be an ExecutionLifecycle")  # noqa: TRY004 - stable SDK contract.
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 0:
            raise ValueError("version must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class PositionResourceSnapshot:
    resource_id: str
    resource_type: str
    state_version: int
    material_trace_id: str | None
    accepts_material: bool

    def __post_init__(self) -> None:
        _required(self.resource_id, "resource_id")
        _required(self.resource_type, "resource_type")
        if not isinstance(self.state_version, int) or isinstance(self.state_version, bool) or self.state_version < 0:
            raise ValueError("state_version must be a non-negative integer")
        if self.material_trace_id is not None:
            _required(self.material_trace_id, "material_trace_id")
        if not isinstance(self.accepts_material, bool):
            raise ValueError("accepts_material must be a boolean")  # noqa: TRY004 - stable SDK contract.


@dataclass(frozen=True, slots=True)
class DeviceBindingSnapshot:
    device_role: str
    device_code: str
    endpoint_code: str
    contract_key: str
    contract_version: str

    def __post_init__(self) -> None:
        for field_name in ("device_role", "device_code", "endpoint_code", "contract_key", "contract_version"):
            _required(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class EpochConfigurationSnapshot:
    line_run_epoch_id: str
    workline_code: str
    plugin_key: str
    plugin_version: str
    config_digest: str
    topology_digest: str
    device_bindings: tuple[DeviceBindingSnapshot, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "line_run_epoch_id",
            "workline_code",
            "plugin_key",
            "plugin_version",
            "config_digest",
            "topology_digest",
        ):
            _required(getattr(self, field_name), field_name)
        if type(self.device_bindings) is not tuple:
            raise TypeError("device_bindings must be a tuple")
        if not self.device_bindings:
            raise ValueError("device_bindings must not be empty")
        if any(type(binding) is not DeviceBindingSnapshot for binding in self.device_bindings):
            raise TypeError("device_bindings must contain DeviceBindingSnapshot values")
        roles = [binding.device_role for binding in self.device_bindings]
        if len(roles) != len(set(roles)):
            raise ValueError("device_bindings must not contain duplicate roles")


@runtime_checkable
class ExecutionSnapshotReader(Protocol):
    def get_execution(self, material_execution_id: str) -> ExecutionSnapshot: ...


@runtime_checkable
class PositionResourceSnapshotReader(Protocol):
    def get_position_resource(self, resource_id: str) -> PositionResourceSnapshot: ...


@runtime_checkable
class EpochConfigurationSnapshotReader(Protocol):
    def get_epoch_configuration(self, line_run_epoch_id: str) -> EpochConfigurationSnapshot: ...
