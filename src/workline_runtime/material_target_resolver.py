"""Topology-aware material destination resolution for workline runtime."""

from __future__ import annotations

from typing import Protocol, TypeVar

from src.workline_runtime.runtime_intent import Destination, DestinationKind


class DeviceLike(Protocol):
    id: int
    device_role: str
    upstream_device_id: int | None
    sort_order: int
    role_index: int


DeviceT = TypeVar("DeviceT", bound=DeviceLike)


def _device_sort_key(device: DeviceLike) -> tuple[int, int, int]:
    return (device.sort_order, device.role_index, device.id)


def _describe_candidates(devices: list[DeviceLike]) -> str:
    return ", ".join(f"{device.id}:{device.device_role}" for device in devices)


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
    detail = f"kind={destination.kind.value} value={destination_value} source_device_id={source_device.id}"
    if not ordered_candidates:
        raise ValueError(f"No destination matched {detail}")

    raise ValueError(f"Ambiguous destination matched {detail} candidates=[{_describe_candidates(ordered_candidates)}]")


def resolve_destination_device(
    *,
    destination: Destination,
    source_device: DeviceT,
    devices: list[DeviceT],
) -> DeviceT:
    """Resolve a concrete material destination device from runtime topology."""

    if destination.kind == DestinationKind.CURRENT:
        return source_device

    if destination.kind == DestinationKind.NEXT:
        downstream_candidates = [device for device in devices if device.upstream_device_id == source_device.id]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=downstream_candidates,
        )

    if destination.kind == DestinationKind.ROLE:
        downstream_candidates = [
            device
            for device in devices
            if device.upstream_device_id == source_device.id and device.device_role == destination.value
        ]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=downstream_candidates,
        )

    if destination.kind == DestinationKind.DEVICE:
        device_candidates = [device for device in devices if device.id == destination.value]
        return _resolve_single_candidate(
            destination=destination,
            source_device=source_device,
            candidates=device_candidates,
        )

    raise ValueError(f"Destination {destination.kind.value} does not resolve to a concrete device")


__all__ = ["resolve_destination_device"]
