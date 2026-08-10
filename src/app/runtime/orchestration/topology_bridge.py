# 旧 runtime 桥接实现:src.workline_runtime.topology 的门面副本
# 旧 runtime 入口删除后,本桥接承载对应正式边界。
# 自引用 src.workline_runtime.device_ordering 已重定向到本目录 device_ordering
# (stable ordering bridge)。
# 拓扑校验只消费 generated Definition 的 typed schema。

"""WORKLINE 运行时拓扑视图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from src.app.runtime.orchestration.device_ordering import device_sort_key
from src.utils.value_normalization import as_dict, optional_int_attr, optional_str_attr


def _string_set(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    values = cast("list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any]", value)
    return frozenset(item for item in values if isinstance(item, str) and item)


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
        for device in sorted(devices, key=device_sort_key):
            device_id = optional_int_attr(device, "id")
            role = optional_str_attr(device, "device_role")
            if device_id is None or role is None:
                continue

            capabilities_json = as_dict(getattr(device, "capabilities_json", None))
            snapshots.append(
                TopologyDeviceSnapshot(
                    device_id=device_id,
                    device_code=optional_str_attr(device, "device_code"),
                    device_role=role,
                    role_index=optional_int_attr(device, "role_index"),
                    upstream_device_id=optional_int_attr(device, "upstream_device_id"),
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


__all__ = [
    "TopologyDeviceSnapshot",
    "WorklineTopologyView",
]
