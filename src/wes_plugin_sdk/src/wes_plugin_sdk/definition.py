"""插件静态声明；只描述逻辑资源需求，不依赖运行实现。"""

from dataclasses import dataclass
from typing import Literal

from .validation import validate_persistable_text, validate_required_refs


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkLineDeviceRole:
    """插件内设备业务角色，现场设备由工作线绑定。"""

    role_key: str
    display_name: str

    def __post_init__(self) -> None:
        validate_persistable_text(self.role_key, "role_key", max_length=100)
        validate_persistable_text(self.display_name, "display_name", max_length=100)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkLinePositionSlot:
    """插件工作位需求；位置编码和容量由工作线维护。"""

    slot_key: str
    display_name: str
    position_type: Literal["RACK_POSITION", "STATION"]
    location_type: str
    allowed_rack_kind: Literal["SINGLE_LAYER", "FIVE_LAYER", "RETURN", "TRANSFER", "PRODUCTION"] | None = None

    def __post_init__(self) -> None:
        for name in ("slot_key", "display_name", "location_type"):
            validate_persistable_text(getattr(self, name), name, max_length=100)
        if self.position_type not in ("RACK_POSITION", "STATION"):
            raise ValueError("invalid position_type")
        if self.allowed_rack_kind not in (None, "SINGLE_LAYER", "FIVE_LAYER", "RETURN", "TRANSFER", "PRODUCTION"):
            raise ValueError("invalid allowed_rack_kind")
        if self.position_type == "STATION" and self.allowed_rack_kind is not None:
            raise ValueError("普通工作位插槽不能约束货架类型")


@dataclass(frozen=True, slots=True, kw_only=True)
class PluginDefinition:
    """身份与资源的唯一声明；业务实现引用该对象。"""

    plugin_key: str
    plugin_version: str
    display_name: str
    supported_line_types: tuple[Literal["AUTO", "MANUAL", "HYBRID"], ...]
    device_roles: tuple[WorkLineDeviceRole, ...] = ()
    position_slots: tuple[WorkLinePositionSlot, ...] = ()

    def __post_init__(self) -> None:
        for name in ("plugin_key", "plugin_version", "display_name"):
            validate_persistable_text(getattr(self, name), name, max_length=100)
        validate_required_refs(self.supported_line_types, "supported_line_types")
        if any(value not in ("AUTO", "MANUAL", "HYBRID") for value in self.supported_line_types):
            raise ValueError("invalid supported_line_types")
        if type(self.device_roles) is not tuple or any(
            not isinstance(role, WorkLineDeviceRole) for role in self.device_roles
        ):
            raise TypeError("device_roles must be a tuple of WorkLineDeviceRole")
        if type(self.position_slots) is not tuple or any(
            not isinstance(slot, WorkLinePositionSlot) for slot in self.position_slots
        ):
            raise TypeError("position_slots must be a tuple of WorkLinePositionSlot")
        if len({role.role_key for role in self.device_roles}) != len(self.device_roles):
            raise ValueError("duplicate device role")
        if len({slot.slot_key for slot in self.position_slots}) != len(self.position_slots):
            raise ValueError("duplicate position slot")
