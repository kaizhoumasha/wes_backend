"""WORKLINE 运行时拓扑视图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from src.workline_runtime.plugin_manifest import WorklinePluginManifest


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _safe_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_dict(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _string_set(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    values = cast("list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any]", value)
    return frozenset(item for item in values if isinstance(item, str) and item)


def _device_sort_key(device: Any) -> tuple[int, int, int]:
    sort_order = getattr(device, "sort_order", 0)
    role_index = getattr(device, "role_index", 0)
    device_id = _safe_int(getattr(device, "id", None)) or 0
    return (
        sort_order if isinstance(sort_order, int) else 0,
        role_index if isinstance(role_index, int) else 0,
        device_id,
    )


def _extract_capabilities(capabilities_json: dict[str, Any]) -> frozenset[str]:
    for field_name in ("capabilities", "capability_codes", "supports_capabilities"):
        values = _string_set(capabilities_json.get(field_name))
        if values:
            return values
    return frozenset()


@dataclass(frozen=True)
class TopologyDeviceSnapshot:
    """插件架构使用的设备拓扑快照，不暴露数据库对象。"""

    device_id: int
    device_code: str | None
    device_role: str
    role_index: int | None
    upstream_device_id: int | None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    supported_event_types: frozenset[str] = field(default_factory=frozenset)
    supported_command_types: frozenset[str] = field(default_factory=frozenset)

    def supports_event(self, event_type: str) -> bool:
        """事件能力校验：设备未声明列表时保持允许策略。"""

        if not self.supported_event_types:
            return True
        return event_type in self.supported_event_types

    def supports_command(self, command_type: str) -> bool:
        """命令能力校验：设备未声明列表时保持允许策略。"""

        if not self.supported_command_types:
            return True
        return command_type in self.supported_command_types


@dataclass(frozen=True)
class WorklineTopologyView:
    """单次 workflow/session 内复用的工作线拓扑视图。"""

    devices_by_role: dict[str, tuple[TopologyDeviceSnapshot, ...]]
    device_by_id: dict[int, TopologyDeviceSnapshot]
    upstream_by_device_id: dict[int, int | None]
    downstream_by_device_id: dict[int, tuple[int, ...]]

    @classmethod
    def from_devices(cls, devices: list[Any] | tuple[Any, ...]) -> WorklineTopologyView:
        """从工作线设备列表推导拓扑视图。"""

        snapshots: list[TopologyDeviceSnapshot] = []
        for device in sorted(devices, key=_device_sort_key):
            device_id = _safe_int(getattr(device, "id", None))
            role = _safe_str(getattr(device, "device_role", None))
            if device_id is None or role is None:
                continue

            capabilities_json = _safe_dict(getattr(device, "capabilities_json", None))
            snapshots.append(
                TopologyDeviceSnapshot(
                    device_id=device_id,
                    device_code=_safe_str(getattr(device, "device_code", None)),
                    device_role=role,
                    role_index=_safe_int(getattr(device, "role_index", None)),
                    upstream_device_id=_safe_int(getattr(device, "upstream_device_id", None)),
                    capabilities=_extract_capabilities(capabilities_json),
                    supported_event_types=_string_set(capabilities_json.get("supports_event_types")),
                    supported_command_types=_string_set(capabilities_json.get("supports_command_types")),
                )
            )

        devices_by_role: dict[str, list[TopologyDeviceSnapshot]] = {}
        device_by_id = {snapshot.device_id: snapshot for snapshot in snapshots}
        upstream_by_device_id: dict[int, int | None] = {}
        downstream_mutable: dict[int, list[int]] = {}
        for snapshot in snapshots:
            devices_by_role.setdefault(snapshot.device_role, []).append(snapshot)
            upstream_by_device_id[snapshot.device_id] = snapshot.upstream_device_id
            if snapshot.upstream_device_id is not None:
                downstream_mutable.setdefault(snapshot.upstream_device_id, []).append(snapshot.device_id)

        return cls(
            devices_by_role={role: tuple(role_devices) for role, role_devices in devices_by_role.items()},
            device_by_id=device_by_id,
            upstream_by_device_id=upstream_by_device_id,
            downstream_by_device_id={
                device_id: tuple(sorted(downstream_ids)) for device_id, downstream_ids in downstream_mutable.items()
            },
        )

    def devices_for_role(self, role: str) -> tuple[TopologyDeviceSnapshot, ...]:
        """按角色读取设备快照。"""

        return self.devices_by_role.get(role, ())


def validate_topology_manifest(manifest: WorklinePluginManifest, topology: WorklineTopologyView) -> None:
    """校验工作线拓扑是否满足插件 manifest。"""

    for requirement in manifest.required_device_roles:
        devices = topology.devices_for_role(requirement.role)
        count = len(devices)
        if count < requirement.min_count:
            raise ValueError(
                f"插件 {manifest.plugin_key} 要求角色 {requirement.role} 至少 {requirement.min_count} 个设备，当前 {count} 个"
            )
        if requirement.max_count is not None and count > requirement.max_count:
            raise ValueError(
                f"插件 {manifest.plugin_key} 要求角色 {requirement.role} 最多 {requirement.max_count} 个设备，当前 {count} 个"
            )

        if requirement.capabilities:
            for device in devices:
                missing_capabilities = requirement.capabilities - device.capabilities
                if missing_capabilities:
                    raise ValueError(
                        f"设备 {device.device_code or device.device_id} 缺少能力: {', '.join(sorted(missing_capabilities))}"
                    )

    if manifest.event_source_roles is None:
        manifest.event_source_roles = {}
    for event_type, roles in manifest.event_source_roles.items():
        if not any(device.supports_event(event_type) for role in roles for device in topology.devices_for_role(role)):
            raise ValueError(f"插件 {manifest.plugin_key} 事件 {event_type} 没有可用来源设备角色: {', '.join(roles)}")

    if manifest.command_target_roles is None:
        manifest.command_target_roles = {}
    for command_type, roles in manifest.command_target_roles.items():
        if not any(
            device.supports_command(command_type) for role in roles for device in topology.devices_for_role(role)
        ):
            raise ValueError(f"插件 {manifest.plugin_key} 命令 {command_type} 没有可用目标设备角色: {', '.join(roles)}")


__all__ = [
    "TopologyDeviceSnapshot",
    "WorklineTopologyView",
    "validate_topology_manifest",
]
