"""Topology-aware command target resolution for workline runtime."""

from __future__ import annotations

from typing import Any

from src.workline_runtime.types import CommandIntent, CommandTargetScope


def _device_id(device: Any) -> int | None:
    value = getattr(device, "id", None)
    return value if isinstance(value, int) else None


def _device_role(device: Any) -> str | None:
    value = getattr(device, "device_role", None)
    return value if isinstance(value, str) and value else None


def _upstream_device_id(device: Any) -> int | None:
    value = getattr(device, "upstream_device_id", None)
    return value if isinstance(value, int) else None


def _device_sort_key(device: Any) -> tuple[int, int, int]:
    sort_order = getattr(device, "sort_order", 0)
    role_index = getattr(device, "role_index", 0)
    device_id = _device_id(device) or 0
    return (
        sort_order if isinstance(sort_order, int) else 0,
        role_index if isinstance(role_index, int) else 0,
        device_id,
    )


def _describe_devices(devices: list[Any]) -> str:
    return ", ".join(
        f"{getattr(device, 'device_code', None) or _device_id(device)}:{_device_role(device) or '-'}"
        for device in devices
    )


def _find_device_by_id(devices: list[Any], *, device_id: int) -> Any | None:
    for device in devices:
        if _device_id(device) == device_id:
            return device
    return None


def _scoped_candidates(*, target_scope: CommandTargetScope, source_device: Any, devices: list[Any]) -> list[Any]:
    source_device_id = _device_id(source_device)
    if source_device_id is None:
        raise ValueError("Source device missing primary key")

    if target_scope == CommandTargetScope.CURRENT:
        return [source_device]
    if target_scope == CommandTargetScope.DOWNSTREAM:
        return [device for device in devices if _upstream_device_id(device) == source_device_id]
    raise ValueError(f"Unsupported target scope: {target_scope}")


def resolve_command_target(*, command_intent: CommandIntent, source_device: Any | None, devices: list[Any]) -> Any:
    """Resolve a command target from runtime topology.

    Resolution rules:
    1. Explicit ``target_device_id`` wins when present.
    2. Otherwise resolve candidates from ``target_scope`` relative to the current source device.
    3. Apply optional ``device_role`` filtering within the scoped candidates.
    4. Require exactly one resolved candidate.
    """

    explicit_target_device_id = command_intent.target_device_id
    if explicit_target_device_id is not None:
        target_device = _find_device_by_id(devices, device_id=explicit_target_device_id)
        if target_device is not None:
            return target_device
        raise ValueError(f"Target device not found: {explicit_target_device_id}")

    if source_device is None:
        raise ValueError("Cannot resolve command target without source device")

    target_scope = command_intent.target_scope
    candidates = _scoped_candidates(target_scope=target_scope, source_device=source_device, devices=devices)
    requested_role = command_intent.device_role
    if requested_role:
        candidates = [device for device in candidates if _device_role(device) == requested_role]

    source_device_id = _device_id(source_device)
    ordered_candidates = sorted(candidates, key=_device_sort_key)
    if len(ordered_candidates) == 1:
        return ordered_candidates[0]

    if not ordered_candidates:
        raise ValueError(
            "No command target matched "
            f"scope={target_scope} role={requested_role or '-'} source_device_id={source_device_id}"
        )

    raise ValueError(
        "Ambiguous command target "
        f"scope={target_scope} role={requested_role or '-'} source_device_id={source_device_id} "
        f"candidates=[{_describe_devices(ordered_candidates)}]"
    )


__all__ = ["resolve_command_target"]
