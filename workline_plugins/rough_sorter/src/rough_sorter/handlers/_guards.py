"""纯 handler 共用的只读快照不变量。"""

from __future__ import annotations

from wes_plugin_sdk import (
    DevicePosition,
    EpochConfigurationSnapshot,
    EpochConfigurationSnapshotReader,
    ExecutionLifecycle,
    ExecutionSnapshot,
    ExecutionSnapshotReader,
    PositionResourceSnapshotReader,
)

PLUGIN_KEY = "rough_sorter"
PLUGIN_VERSION = "1.0.0"

ROLE_CONTRACTS = {
    "MEASUREMENT_DEVICE": "rough_sorter.measurement_device",
    "TRANSFER_DEVICE": "rough_sorter.transfer_device",
    "PLACEMENT_DEVICE": "rough_sorter.placement_device",
}


def require_execution(
    reader: ExecutionSnapshotReader,
    *,
    material_execution_id: str,
    material_trace_id: str,
    allow_reconciling: bool = False,
) -> ExecutionSnapshot:
    snapshot = reader.get_execution(material_execution_id)
    if snapshot.material_execution_id != material_execution_id or snapshot.material_trace_id != material_trace_id:
        raise ValueError("execution identity does not match Fact")
    if snapshot.lifecycle is ExecutionLifecycle.CLOSED or (
        snapshot.lifecycle is ExecutionLifecycle.RECONCILING and not allow_reconciling
    ):
        raise ValueError(f"execution lifecycle does not allow automatic decisions: {snapshot.lifecycle.value}")
    return snapshot


def require_epoch(
    reader: EpochConfigurationSnapshotReader,
    *,
    line_run_epoch_id: str,
    workline_code: str | None = None,
) -> EpochConfigurationSnapshot:
    snapshot = reader.get_epoch_configuration(line_run_epoch_id)
    if snapshot.line_run_epoch_id != line_run_epoch_id:
        raise ValueError("epoch identity does not match Fact")
    if snapshot.plugin_key != PLUGIN_KEY or snapshot.plugin_version != PLUGIN_VERSION:
        raise ValueError("epoch plugin identity does not match rough sorter")
    if workline_code is not None and snapshot.workline_code != workline_code:
        raise ValueError("epoch WorkLine does not match Fact")
    bindings = {binding.device_role: binding for binding in snapshot.device_bindings}
    if set(bindings) != set(ROLE_CONTRACTS):
        raise ValueError("epoch must bind exactly the three rough sorter roles")
    for role, contract_key in ROLE_CONTRACTS.items():
        binding = bindings[role]
        if binding.contract_key != contract_key or binding.contract_version != "1.0":
            raise ValueError(f"epoch binding does not match approved contract: {role}")
    return snapshot


def require_source(
    reader: PositionResourceSnapshotReader,
    position: DevicePosition,
    *,
    material_trace_id: str,
) -> None:
    snapshot = reader.get_position_resource(position.location_id)
    if snapshot.resource_id != position.location_id or snapshot.resource_type != position.location_type:
        raise ValueError("source position snapshot does not match Fact")
    if snapshot.material_trace_id != material_trace_id:
        raise ValueError("source position does not contain material trace")


def require_target(reader: PositionResourceSnapshotReader, position: DevicePosition) -> None:
    snapshot = reader.get_position_resource(position.location_id)
    if snapshot.resource_id != position.location_id or snapshot.resource_type != position.location_type:
        raise ValueError("target position snapshot does not match Fact")
    if not snapshot.accepts_material or snapshot.material_trace_id is not None:
        raise ValueError("target position cannot accept material")


__all__ = [
    "PLUGIN_KEY",
    "PLUGIN_VERSION",
    "require_epoch",
    "require_execution",
    "require_source",
    "require_target",
]
