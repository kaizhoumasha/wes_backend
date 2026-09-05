"""纯 handler 共用的只读快照不变量。"""

from __future__ import annotations

from wes_plugin_sdk import (
    DeviceBindingSnapshot,
    EpochConfigurationSnapshot,
    ExecutionLifecycle,
    ExecutionSnapshot,
)

PLUGIN_KEY = "rough_sorter"
PLUGIN_VERSION = "1.0.0"

ROLE_CONTRACTS = {
    "MEASUREMENT_DEVICE": "rough_sorter.measurement_device",
    "TRANSFER_DEVICE": "rough_sorter.transfer_device",
    "PLACEMENT_DEVICE": "rough_sorter.placement_device",
}


def require_execution(
    snapshot: ExecutionSnapshot,
    *,
    material_execution_id: str,
    material_trace_id: str,
    allow_reconciling: bool = False,
) -> ExecutionSnapshot:
    if snapshot.material_execution_id != material_execution_id or snapshot.material_trace_id != material_trace_id:
        raise ValueError("execution identity does not match Fact")
    if snapshot.lifecycle is ExecutionLifecycle.CLOSED or (
        snapshot.lifecycle is ExecutionLifecycle.RECONCILING and not allow_reconciling
    ):
        raise ValueError(f"execution lifecycle does not allow automatic decisions: {snapshot.lifecycle.value}")
    return snapshot


def require_epoch(
    snapshot: EpochConfigurationSnapshot,
    *,
    line_run_epoch_id: str,
    workline_code: str | None = None,
) -> EpochConfigurationSnapshot:
    if snapshot.line_run_epoch_id != line_run_epoch_id:
        raise ValueError("epoch identity does not match Fact")
    if snapshot.plugin_key != PLUGIN_KEY or snapshot.plugin_version != PLUGIN_VERSION:
        raise ValueError("epoch plugin identity does not match rough sorter")
    if workline_code is not None and snapshot.workline_code != workline_code:
        raise ValueError("epoch WorkLine does not match Fact")
    bindings = {binding.device_role: binding for binding in snapshot.device_bindings}
    if len(snapshot.device_bindings) != len(ROLE_CONTRACTS) or set(bindings) != set(ROLE_CONTRACTS):
        raise ValueError("epoch must bind exactly the three rough sorter roles")
    for role, contract_key in ROLE_CONTRACTS.items():
        binding = bindings[role]
        if binding.contract_key != contract_key or binding.contract_version != "1.0":
            raise ValueError(f"epoch binding does not match approved contract: {role}")
    return snapshot


def require_device_binding(
    snapshot: EpochConfigurationSnapshot,
    device_role: str,
) -> DeviceBindingSnapshot:
    matches = tuple(binding for binding in snapshot.device_bindings if binding.device_role == device_role)
    if len(matches) != 1:
        raise ValueError(f"epoch must bind exactly one rough sorter device for role: {device_role}")
    return matches[0]


__all__ = [
    "PLUGIN_KEY",
    "PLUGIN_VERSION",
    "require_device_binding",
    "require_epoch",
    "require_execution",
]
