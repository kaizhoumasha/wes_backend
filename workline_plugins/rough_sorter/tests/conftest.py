from __future__ import annotations

from wes_plugin_sdk import (
    DeviceBindingSnapshot,
    ExecutionLifecycle,
    ExecutionSnapshot,
    PositionBindingSnapshot,
    WorkLineConfigurationSnapshot,
)

from rough_sorter.facts import RoughSorterRuntimeSnapshot

EXECUTION_ID = "rough-execution-1"
TRACE_ID = "trace-1"
WORKLINE_ID = "workline-1"
WORKLINE_CODE = "ROUGH-LINE-1"


def execution_snapshot(*, lifecycle: ExecutionLifecycle = ExecutionLifecycle.RUNNING) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        material_execution_id=EXECUTION_ID,
        material_trace_id=TRACE_ID,
        workline_id=WORKLINE_ID,
        lifecycle=lifecycle,
        version=1,
    )


def workline_snapshot() -> WorkLineConfigurationSnapshot:
    return WorkLineConfigurationSnapshot(
        workline_id=WORKLINE_ID,
        workline_code=WORKLINE_CODE,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
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
                ("MEASUREMENT_POSITION", "MEASUREMENT_POSITION"),
                ("PIPELINE_INLET", "PIPELINE_INLET"),
                ("PIPELINE_OUTLET", "PIPELINE_OUTLET"),
                ("NG_POSITION", "NG_POSITION"),
            )
        ),
    )


def runtime_snapshot(*, lifecycle: ExecutionLifecycle = ExecutionLifecycle.RUNNING) -> RoughSorterRuntimeSnapshot:
    return RoughSorterRuntimeSnapshot(execution=execution_snapshot(lifecycle=lifecycle), workline=workline_snapshot())
