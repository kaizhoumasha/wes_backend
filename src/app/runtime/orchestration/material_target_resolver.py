# 旧 runtime 镜像实现:src.workline_runtime.material_target_resolver 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。
# 自引用 src.workline_runtime.{device_ordering, runtime_intent} 已重定向到本目录 / 本目录 mirror。

"""Topology-aware material destination resolution for workline runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from src.app.runtime.orchestration.device_ordering import device_sort_key
from src.app.runtime.orchestration.runtime_intent import Destination, DestinationKind
from src.utils.value_normalization import optional_int_attr, optional_str_attr


class DeviceLike(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def device_role(self) -> str: ...

    @property
    def upstream_device_id(self) -> int | None: ...

    @property
    def sort_order(self) -> int: ...

    @property
    def role_index(self) -> int: ...


DeviceT = TypeVar("DeviceT", bound=DeviceLike)


def _describe_candidates(devices: Sequence[DeviceLike]) -> str:
    return ", ".join(
        f"{optional_int_attr(device, 'id')}:{optional_str_attr(device, 'device_role')}" for device in devices
    )


def _resolve_single_candidate(
    *,
    destination: Destination,
    source_device: DeviceLike | None,
    candidates: Sequence[DeviceT],
) -> DeviceT:
    ordered_candidates = sorted(candidates, key=device_sort_key)
    if len(ordered_candidates) == 1:
        return ordered_candidates[0]

    destination_value = destination.value if destination.value is not None else "-"
    detail = (
        f"kind={destination.kind.value} "
        f"value={destination_value} source_device_id={optional_int_attr(source_device, 'id')}"
    )
    if not ordered_candidates:
        raise ValueError(f"No destination matched {detail}")

    raise ValueError(f"Ambiguous destination matched {detail} candidates=[{_describe_candidates(ordered_candidates)}]")


def resolve_destination_device(
    *,
    destination: Destination,
    source_device: DeviceT | None,
    devices: Sequence[DeviceT],
    route_roles: Mapping[str, str] | None = None,
) -> DeviceT:
    """Resolve a concrete material destination device from runtime topology."""
    if source_device is None and destination.kind in {DestinationKind.CURRENT, DestinationKind.NEXT}:
        raise ValueError("Cannot resolve command target without source device")

    if destination.kind in {DestinationKind.NG_ROUTE, DestinationKind.PASS_ROUTE}:
        route_key = destination.kind.value
        configured_role = None
        if route_roles is not None:
            configured_role = route_roles.get(route_key) or route_roles.get(route_key.lower())
        if not configured_role:
            raise ValueError(f"No route role configured for {route_key}")
        return resolve_destination_device(
            destination=Destination.role(configured_role),
            source_device=source_device,
            devices=devices,
            route_roles=route_roles,
        )

    if destination.kind == DestinationKind.CURRENT:
        if source_device is None:  # 防御性收窄；入口已对 CURRENT/NEXT fail closed。
            raise ValueError("Cannot resolve command target without source device")
        return source_device

    if destination.kind == DestinationKind.NEXT:
        if source_device is None:  # 防御性收窄；避免优化模式移除运行时保护。
            raise ValueError("Cannot resolve command target without source device")
        source_id = optional_int_attr(source_device, "id")
        downstream_candidates = [
            device for device in devices if optional_int_attr(device, "upstream_device_id") == source_id
        ]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=downstream_candidates,
        )

    if destination.kind == DestinationKind.ROLE:
        if source_device is not None and optional_str_attr(source_device, "device_role") == destination.value:
            return source_device

        if source_device is not None:
            source_id = optional_int_attr(source_device, "id")
            downstream_role_candidates = [
                device
                for device in devices
                if optional_int_attr(device, "upstream_device_id") == source_id
                and optional_str_attr(device, "device_role") == destination.value
            ]
            if downstream_role_candidates:
                return _resolve_single_candidate(
                    destination=destination,
                    source_device=source_device,
                    candidates=downstream_role_candidates,
                )

        role_candidates = [
            device for device in devices if optional_str_attr(device, "device_role") == destination.value
        ]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=role_candidates,
        )

    if destination.kind == DestinationKind.DEVICE:
        device_candidates = [device for device in devices if optional_int_attr(device, "id") == destination.value]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=device_candidates,
        )

    raise ValueError(f"Destination {destination.kind.value} does not resolve to a concrete device")


__all__ = ["resolve_destination_device"]
