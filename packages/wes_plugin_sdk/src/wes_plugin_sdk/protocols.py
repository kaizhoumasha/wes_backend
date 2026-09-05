"""插件 handler 可依赖的最窄只读快照协议。"""

from dataclasses import dataclass
from enum import StrEnum

from .validation import validate_required_text as _required


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
class DeviceBindingSnapshot:
    device_role: str
    device_code: str
    contract_key: str
    contract_version: str

    def __post_init__(self) -> None:
        for field_name in ("device_role", "device_code", "contract_key", "contract_version"):
            _required(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class PositionBindingSnapshot:
    position_role: str
    location_id: str
    location_type: str

    def __post_init__(self) -> None:
        for field_name in ("position_role", "location_id", "location_type"):
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
    position_bindings: tuple[PositionBindingSnapshot, ...]

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
        device_codes = [binding.device_code for binding in self.device_bindings]
        if len(device_codes) != len(set(device_codes)):
            raise ValueError("device_bindings must not contain duplicate device codes")
        if type(self.position_bindings) is not tuple:
            raise TypeError("position_bindings must be a tuple")
        if not self.position_bindings:
            raise ValueError("position_bindings must not be empty")
        if any(type(binding) is not PositionBindingSnapshot for binding in self.position_bindings):
            raise TypeError("position_bindings must contain PositionBindingSnapshot values")
        position_roles = [binding.position_role for binding in self.position_bindings]
        if len(position_roles) != len(set(position_roles)):
            raise ValueError("position_bindings must not contain duplicate roles")
        location_ids = [binding.location_id for binding in self.position_bindings]
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("position_bindings must not contain duplicate locations")
