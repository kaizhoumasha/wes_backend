from dataclasses import dataclass

import pytest

from src.workline_runtime.material_target_resolver import resolve_destination_device
from src.workline_runtime.runtime_intent import Destination


@dataclass
class Device:
    id: int
    device_role: str
    upstream_device_id: int | None = None
    sort_order: int = 0
    role_index: int = 1


def test_resolves_next_device_from_topology():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    weigh = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)

    resolved = resolve_destination_device(
        destination=Destination.next(),
        source_device=source,
        devices=[source, weigh],
    )

    assert resolved == weigh


def test_resolves_role_within_downstream_candidates():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    conveyor = Device(id=2, device_role="CONVEYOR", upstream_device_id=1)
    weigh = Device(id=3, device_role="WEIGH_SCALE", upstream_device_id=1)

    resolved = resolve_destination_device(
        destination=Destination.role("WEIGH_SCALE"),
        source_device=source,
        devices=[source, conveyor, weigh],
    )

    assert resolved == weigh


def test_resolves_role_within_source_branch_before_global_candidates():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    branch_weigh = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)
    other_branch = Device(id=3, device_role="WEIGH_SCALE", upstream_device_id=99)

    resolved = resolve_destination_device(
        destination=Destination.role("WEIGH_SCALE"),
        source_device=source,
        devices=[source, branch_weigh, other_branch],
    )

    assert resolved == branch_weigh


def test_resolves_role_across_all_devices_without_upstream_link():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    weigh = Device(id=3, device_role="WEIGH_SCALE")

    resolved = resolve_destination_device(
        destination=Destination.role("WEIGH_SCALE"),
        source_device=source,
        devices=[source, weigh],
    )

    assert resolved == weigh


def test_resolves_role_to_current_device_when_source_has_target_role():
    source = Device(id=1, device_role="INPUT_ARM")
    conveyor = Device(id=2, device_role="CONVEYOR", upstream_device_id=1)

    resolved = resolve_destination_device(
        destination=Destination.role("INPUT_ARM"),
        source_device=source,
        devices=[source, conveyor],
    )

    assert resolved == source


def test_resolves_role_to_current_device_before_other_same_role_devices():
    source = Device(id=1, device_role="INPUT_ARM")
    other_input = Device(id=2, device_role="INPUT_ARM", upstream_device_id=99)

    resolved = resolve_destination_device(
        destination=Destination.role("INPUT_ARM"),
        source_device=source,
        devices=[source, other_input],
    )

    assert resolved == source


def test_raises_for_ambiguous_role():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    left = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)
    right = Device(id=3, device_role="WEIGH_SCALE", upstream_device_id=1)

    with pytest.raises(ValueError, match="Ambiguous"):
        resolve_destination_device(
            destination=Destination.role("WEIGH_SCALE"),
            source_device=source,
            devices=[source, left, right],
        )


def test_resolves_current_to_source_device():
    source = Device(id=1, device_role="ENTRY_SCANNER")

    resolved = resolve_destination_device(
        destination=Destination.current(),
        source_device=source,
        devices=[source],
    )

    assert resolved == source


def test_resolves_device_by_id_across_all_devices():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    upstream = Device(id=2, device_role="BUFFER")
    target = Device(id=3, device_role="WEIGH_SCALE", upstream_device_id=2)

    resolved = resolve_destination_device(
        destination=Destination.device(3),
        source_device=source,
        devices=[source, upstream, target],
    )

    assert resolved == target


def test_resolves_ng_route_from_configured_role():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    ng_target = Device(id=3, device_role="NG_BUFFER", upstream_device_id=1)

    resolved = resolve_destination_device(
        destination=Destination.ng_route(),
        source_device=source,
        devices=[source, ng_target],
        route_roles={"NG_ROUTE": "NG_BUFFER"},
    )

    assert resolved == ng_target


def test_raises_for_non_concrete_destination():
    source = Device(id=1, device_role="ENTRY_SCANNER")

    with pytest.raises(ValueError, match="does not resolve to a concrete device"):
        resolve_destination_device(
            destination=Destination.exit(),
            source_device=source,
            devices=[source],
        )
