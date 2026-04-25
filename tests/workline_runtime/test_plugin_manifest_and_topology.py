"""插件 manifest 与工作线拓扑视图测试。"""

from types import SimpleNamespace

import pytest

from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.topology import WorklineTopologyView, validate_topology_manifest


def _device(
    device_id: int,
    *,
    code: str,
    role: str,
    upstream_device_id: int | None = None,
    capabilities_json: dict | None = None,
):
    return SimpleNamespace(
        id=device_id,
        device_code=code,
        device_role=role,
        role_index=device_id,
        sort_order=device_id,
        upstream_device_id=upstream_device_id,
        capabilities_json=capabilities_json or {},
    )


def _business_key_resolver(payload_json: dict) -> str | None:
    data = payload_json.get("data")
    return data.get("tote_id") if isinstance(data, dict) else None


def test_manifest_normalizes_role_maps_and_resolves_business_key() -> None:
    manifest = WorklinePluginManifest(
        plugin_key="inbound_tote_qc",
        contract_version="spike",
        required_device_roles=(DeviceRoleRequirement("ENTRY_SCANNER"),),
        business_key_resolver=_business_key_resolver,
        event_source_roles={"TOTE_ARRIVED": "ENTRY_SCANNER"},
        command_target_roles={"WEIGH_TOTE": ["WEIGH_SCALE"]},
    )

    assert manifest.event_source_roles["TOTE_ARRIVED"] == ("ENTRY_SCANNER",)
    assert manifest.command_target_roles["WEIGH_TOTE"] == ("WEIGH_SCALE",)
    assert manifest.resolve_business_key({"data": {"tote_id": "TOTE-001"}}) == "TOTE-001"


def test_topology_view_derives_roles_and_upstream_downstream() -> None:
    scanner = _device(1, code="SCAN01", role="ENTRY_SCANNER")
    scale = _device(2, code="SCALE01", role="WEIGH_SCALE", upstream_device_id=1)
    conveyor = _device(3, code="CONV01", role="DIVERT_CONVEYOR", upstream_device_id=2)

    topology = WorklineTopologyView.from_devices([conveyor, scale, scanner])

    assert topology.devices_for_role("ENTRY_SCANNER")[0].device_code == "SCAN01"
    assert topology.device_by_id[2].upstream_device_id == 1
    assert topology.upstream_by_device_id[3] == 2
    assert topology.downstream_by_device_id[1] == (2,)
    assert topology.downstream_by_device_id[2] == (3,)


def test_validate_topology_manifest_accepts_permissive_device_capabilities() -> None:
    manifest = WorklinePluginManifest(
        plugin_key="inbound_tote_qc",
        contract_version="spike",
        required_device_roles=(
            DeviceRoleRequirement("ENTRY_SCANNER", min_count=1, max_count=1),
            DeviceRoleRequirement("WEIGH_SCALE", min_count=1, max_count=1),
        ),
        business_key_resolver=_business_key_resolver,
        event_source_roles={"TOTE_ARRIVED": "ENTRY_SCANNER"},
        command_target_roles={"WEIGH_TOTE": "WEIGH_SCALE"},
    )
    topology = WorklineTopologyView.from_devices(
        [
            _device(1, code="SCAN01", role="ENTRY_SCANNER"),
            _device(2, code="SCALE01", role="WEIGH_SCALE", upstream_device_id=1),
        ]
    )

    validate_topology_manifest(manifest, topology)


def test_validate_topology_manifest_rejects_missing_role() -> None:
    manifest = WorklinePluginManifest(
        plugin_key="inbound_tote_qc",
        contract_version="spike",
        required_device_roles=(
            DeviceRoleRequirement("ENTRY_SCANNER", min_count=1, max_count=1),
            DeviceRoleRequirement("WEIGH_SCALE", min_count=1, max_count=1),
        ),
        business_key_resolver=_business_key_resolver,
    )
    topology = WorklineTopologyView.from_devices([_device(1, code="SCAN01", role="ENTRY_SCANNER")])

    with pytest.raises(ValueError, match="角色 WEIGH_SCALE 至少 1 个设备"):
        validate_topology_manifest(manifest, topology)


def test_validate_topology_manifest_rejects_unsupported_command_type() -> None:
    manifest = WorklinePluginManifest(
        plugin_key="inbound_tote_qc",
        contract_version="spike",
        required_device_roles=(DeviceRoleRequirement("WEIGH_SCALE", min_count=1, max_count=1),),
        business_key_resolver=_business_key_resolver,
        command_target_roles={"WEIGH_TOTE": "WEIGH_SCALE"},
    )
    topology = WorklineTopologyView.from_devices(
        [
            _device(
                2,
                code="SCALE01",
                role="WEIGH_SCALE",
                capabilities_json={"supports_command_types": ["CALIBRATE"]},
            )
        ]
    )

    with pytest.raises(ValueError, match="命令 WEIGH_TOTE 没有可用目标设备角色"):
        validate_topology_manifest(manifest, topology)
