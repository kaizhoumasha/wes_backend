"""Topology-aware material destination resolution for workline runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping

from src.workline_runtime.runtime_intent import Destination, DestinationKind


class DeviceLike(Protocol):
    id: int
    device_role: str
    upstream_device_id: int | None
    sort_order: int
    role_index: int


DeviceT = TypeVar("DeviceT", bound=DeviceLike)


def _int_attr(device: Any, name: str) -> int:
    value = getattr(device, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_int_attr(device: Any, name: str) -> int | None:
    value = getattr(device, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_attr(device: Any, name: str) -> str | None:
    value = getattr(device, name, None)
    return value if isinstance(value, str) and value else None


def _device_sort_key(device: DeviceLike) -> tuple[int, int, int]:
    return (_int_attr(device, "sort_order"), _int_attr(device, "role_index"), _int_attr(device, "id"))


def _describe_candidates(devices: list[DeviceLike]) -> str:
    return ", ".join(f"{_optional_int_attr(device, 'id')}:{_str_attr(device, 'device_role')}" for device in devices)


def _resolve_single_candidate(
    *,
    destination: Destination,
    source_device: DeviceLike,
    candidates: list[DeviceT],
) -> DeviceT:
    ordered_candidates = sorted(candidates, key=_device_sort_key)
    if len(ordered_candidates) == 1:
        return ordered_candidates[0]

    destination_value = destination.value if destination.value is not None else "-"
    detail = f"kind={destination.kind.value} value={destination_value} source_device_id={_optional_int_attr(source_device, 'id')}"
    if not ordered_candidates:
        raise ValueError(f"No destination matched {detail}")

    raise ValueError(f"Ambiguous destination matched {detail} candidates=[{_describe_candidates(ordered_candidates)}]")


def resolve_destination_device(
    *,
    destination: Destination,
    source_device: DeviceT,
    devices: list[DeviceT],
    route_roles: Mapping[str, str] | None = None,
) -> DeviceT:
    """Resolve a concrete material destination device from runtime topology."""
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
        return source_device

    if destination.kind == DestinationKind.NEXT:
        source_id = _optional_int_attr(source_device, "id")
        downstream_candidates = [
            device for device in devices if _optional_int_attr(device, "upstream_device_id") == source_id
        ]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=downstream_candidates,
        )

    if destination.kind == DestinationKind.ROLE:
        if _str_attr(source_device, "device_role") == destination.value:
            return source_device

        downstream_candidates = [
            device
            for device in devices
            if _optional_int_attr(device, "upstream_device_id") == _optional_int_attr(source_device, "id")
            and _str_attr(device, "device_role") == destination.value
        ]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=downstream_candidates,
        )

    if destination.kind == DestinationKind.DEVICE:
        device_candidates = [device for device in devices if _optional_int_attr(device, "id") == destination.value]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=device_candidates,
        )

    raise ValueError(f"Destination {destination.kind.value} does not resolve to a concrete device")


__all__ = ["resolve_destination_device"]
