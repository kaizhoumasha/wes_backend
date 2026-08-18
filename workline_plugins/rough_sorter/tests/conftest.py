from __future__ import annotations

from wes_plugin_sdk import (
    DeviceBindingSnapshot,
    EpochConfigurationSnapshot,
    ExecutionLifecycle,
    ExecutionSnapshot,
    PositionBindingSnapshot,
)

from rough_sorter.facts import RoughSorterRuntimeSnapshot

EXECUTION_ID = "rough-execution-1"
TRACE_ID = "trace-1"
EPOCH_ID = "epoch-1"
WORKLINE_CODE = "ROUGH-LINE-1"


def execution_snapshot(*, lifecycle: ExecutionLifecycle = ExecutionLifecycle.RUNNING) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        material_execution_id=EXECUTION_ID,
        material_trace_id=TRACE_ID,
        line_run_epoch_id=EPOCH_ID,
        lifecycle=lifecycle,
        version=1,
    )


def epoch_snapshot() -> EpochConfigurationSnapshot:
    return EpochConfigurationSnapshot(
        line_run_epoch_id=EPOCH_ID,
        workline_code=WORKLINE_CODE,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        config_digest="config-digest",
        topology_digest="topology-digest",
        device_bindings=tuple(
            DeviceBindingSnapshot(
                device_role=role,
                device_code=f"device-{index}",
                contract_key=contract_key,
                contract_version="1.0",
            )
            for index, (role, contract_key) in enumerate(
                (
                    ("MEASUREMENT_DEVICE", "rough_sorter.measurement_device"),
                    ("TRANSFER_DEVICE", "rough_sorter.transfer_device"),
                    ("PLACEMENT_DEVICE", "rough_sorter.placement_device"),
                ),
                start=1,
            )
        ),
        position_bindings=tuple(
            PositionBindingSnapshot(position_role=role, location_id=location_id, location_type="RACK_CELL")
            for role, location_id in (
                ("MEASUREMENT_POSITION", "MEASUREMENT-1"),
                ("PIPELINE_INLET", "INLET-1"),
                ("PIPELINE_OUTLET", "OUTLET-1"),
                ("NG_POSITION", "NG-1"),
            )
        ),
    )


def runtime_snapshot(*, lifecycle: ExecutionLifecycle = ExecutionLifecycle.RUNNING) -> RoughSorterRuntimeSnapshot:
    return RoughSorterRuntimeSnapshot(execution=execution_snapshot(lifecycle=lifecycle), epoch=epoch_snapshot())
