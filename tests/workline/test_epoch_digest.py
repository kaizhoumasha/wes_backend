from src.app.workline.epoch_digest import configuration_digest, topology_digest
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding, LineRunEpochPositionBinding


def _binding(*, device_id: int, role: str) -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        line_run_epoch_id=11,
        device_id=device_id,
        device_code=f"DEVICE-{device_id}",
        device_role=role,
        contract_key=f"rough_sorter.{role.lower()}",
        contract_version="1.0",
        status_max_age_ms=1000,
        command_timeout_ms=5000,
    )


def _position(*, role: str, location_id: str) -> LineRunEpochPositionBinding:
    return LineRunEpochPositionBinding(
        line_run_epoch_id=11,
        position_role=role,
        location_id=location_id,
        location_type="RACK_CELL",
    )


def test_epoch_digests_use_only_frozen_identity_and_canonical_binding_order() -> None:
    first = _binding(device_id=2, role="TRANSFER_DEVICE")
    second = _binding(device_id=1, role="MEASUREMENT_DEVICE")
    inlet = _position(role="PIPELINE_INLET", location_id="INLET-1")
    outlet = _position(role="PIPELINE_OUTLET", location_id="OUTLET-1")

    assert configuration_digest("rough_sorter", "1.0.0", "ROUGH_SORT_INBOUND") == (
        "0084c069fe3c5bfd14b8a5231a636c969c80a1ac826071b3315ddc61fc5e6dbe"
    )
    assert topology_digest((first, second), (inlet, outlet)) == topology_digest((second, first), (outlet, inlet))


def test_topology_digest_changes_for_every_binding_identity_field() -> None:
    baseline = _binding(device_id=1, role="MEASUREMENT_DEVICE")
    changed = _binding(device_id=1, role="MEASUREMENT_DEVICE")
    changed.command_timeout_ms = 5001
    position = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1")

    assert topology_digest((baseline,), (position,)) != topology_digest((changed,), (position,))
    changed_position = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-2")
    assert topology_digest((baseline,), (position,)) != topology_digest((baseline,), (changed_position,))
