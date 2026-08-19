import importlib

import pytest

from src.app.workline.epoch_digest import canonical_configuration_snapshot, configuration_digest, topology_digest
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding, LineRunEpochPositionBinding


def _binding(
    *,
    device_id: int,
    role: str,
    line_run_epoch_id: int = 11,
    device_code: str | None = None,
    endpoint_base_url: str = "http://ecs-a:8080",
) -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        line_run_epoch_id=line_run_epoch_id,
        device_id=device_id,
        device_code=device_code or f"DEVICE-{device_id}",
        device_role=role,
        endpoint_base_url=endpoint_base_url,
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

    first_config = {"limits": {"max_weight": 30, "min_weight": 1}, "mode": "INBOUND"}
    reordered_config = {"mode": "INBOUND", "limits": {"min_weight": 1, "max_weight": 30}}
    assert configuration_digest("rough_sorter", "1.0.0", "ROUGH_SORT_INBOUND", first_config) == (
        configuration_digest("rough_sorter", "1.0.0", "ROUGH_SORT_INBOUND", reordered_config)
    )
    assert topology_digest((first, second), (inlet, outlet)) == topology_digest((second, first), (outlet, inlet))


def test_configuration_digest_changes_with_complete_snapshot() -> None:
    assert configuration_digest("plugin", "1.0", "FLOW", {"limit": 1}) != configuration_digest(
        "plugin", "1.0", "FLOW", {"limit": 2}
    )


def test_configuration_snapshot_rejects_non_json_or_non_object_values() -> None:
    with pytest.raises(ValueError):
        canonical_configuration_snapshot({"limit": float("nan")})
    with pytest.raises(TypeError, match="JSON object"):
        canonical_configuration_snapshot(["not-an-object"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("device_code", "MEASURE-2"),
        ("device_role", "TRANSFER_DEVICE"),
        ("endpoint_base_url", "http://ecs-b:8080"),
        ("contract_key", "generic.changed"),
        ("contract_version", "2.0"),
        ("status_max_age_ms", 1_001),
        ("command_timeout_ms", 5_001),
    ],
)
def test_topology_digest_changes_for_every_device_topology_field(field: str, changed_value: object) -> None:
    baseline = _binding(device_id=1, role="MEASUREMENT_DEVICE")
    changed = _binding(device_id=1, role="MEASUREMENT_DEVICE")
    setattr(changed, field, changed_value)
    position = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1")

    assert topology_digest((baseline,), (position,)) != topology_digest((changed,), (position,))


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("position_role", "PIPELINE_INLET"),
        ("location_id", "MEASUREMENT-2"),
        ("location_type", "PIPELINE_POSITION"),
    ],
)
def test_topology_digest_changes_for_every_position_topology_field(field: str, changed_value: str) -> None:
    baseline = _binding(device_id=1, role="MEASUREMENT_DEVICE")
    position = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1")
    changed_position = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1")
    setattr(changed_position, field, changed_value)

    assert topology_digest((baseline,), (position,)) != topology_digest((baseline,), (changed_position,))


def test_topology_digest_excludes_database_generated_identity() -> None:
    first = _binding(device_id=1, role="MEASUREMENT_DEVICE", line_run_epoch_id=11, device_code="MEASURE-1")
    same_topology_in_new_epoch = _binding(
        device_id=999,
        role="MEASUREMENT_DEVICE",
        line_run_epoch_id=22,
        device_code="MEASURE-1",
    )
    position = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1")
    same_position_in_new_epoch = _position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1")
    same_position_in_new_epoch.line_run_epoch_id = 22

    assert topology_digest((first,), (position,)) == topology_digest(
        (same_topology_in_new_epoch,),
        (same_position_in_new_epoch,),
    )


def test_topology_digest_accepts_stable_preinsert_inputs() -> None:
    activation = importlib.import_module("src.app.workline.epoch_activation")
    device_input = activation.LineRunEpochDeviceBindingInput(
        device_id=7,
        device_code="MEASURE-1",
        device_role="MEASUREMENT_DEVICE",
        endpoint_base_url="HTTP://ECS-A:80/",
        contract_key="rough_sorter.measurement_device",
        contract_version="1.0",
        status_max_age_ms=1_000,
        command_timeout_ms=5_000,
    )
    assert device_input.endpoint_base_url == "http://ecs-a"
    position_input = activation.LineRunEpochPositionBindingInput(
        position_role="MEASUREMENT_POSITION",
        location_id="MEASUREMENT-1",
        location_type="RACK_CELL",
    )

    assert topology_digest((device_input,), (position_input,)) == topology_digest(
        (
            _binding(
                device_id=999,
                role="MEASUREMENT_DEVICE",
                device_code="MEASURE-1",
                endpoint_base_url="http://ecs-a",
            ),
        ),
        (_position(role="MEASUREMENT_POSITION", location_id="MEASUREMENT-1"),),
    )
