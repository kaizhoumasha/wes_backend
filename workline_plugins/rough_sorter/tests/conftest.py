from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    DeviceBindingSnapshot,
    EpochConfigurationSnapshot,
    ExecutionLifecycle,
    ExecutionSnapshot,
    PositionResourceSnapshot,
)

EXECUTION_ID = "rough-execution-1"
TRACE_ID = "trace-1"
EPOCH_ID = "epoch-1"
WORKLINE_CODE = "ROUGH-LINE-1"


@dataclass(frozen=True, slots=True)
class FakeExecutionReader:
    snapshot: ExecutionSnapshot

    def get_execution(self, material_execution_id: str) -> ExecutionSnapshot:
        if material_execution_id != self.snapshot.material_execution_id:
            raise LookupError(material_execution_id)
        return self.snapshot


@dataclass(frozen=True, slots=True)
class FakePositionReader:
    snapshots: tuple[PositionResourceSnapshot, ...]

    def get_position_resource(self, resource_id: str) -> PositionResourceSnapshot:
        for snapshot in self.snapshots:
            if snapshot.resource_id == resource_id:
                return snapshot
        raise LookupError(resource_id)


@dataclass(frozen=True, slots=True)
class FakeEpochReader:
    snapshot: EpochConfigurationSnapshot

    def get_epoch_configuration(self, line_run_epoch_id: str) -> EpochConfigurationSnapshot:
        if line_run_epoch_id != self.snapshot.line_run_epoch_id:
            raise LookupError(line_run_epoch_id)
        return self.snapshot


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
                endpoint_code="ecs-1",
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
    )


def position_snapshot(
    resource_id: str,
    resource_type: str,
    *,
    material_trace_id: str | None,
    accepts_material: bool,
) -> PositionResourceSnapshot:
    return PositionResourceSnapshot(
        resource_id=resource_id,
        resource_type=resource_type,
        state_version=1,
        material_trace_id=material_trace_id,
        accepts_material=accepts_material,
    )
