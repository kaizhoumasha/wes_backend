"""Material identity contract tests."""

from src.workline_runtime.material_identity import (
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    hash_material_evidence,
)
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest


def _business_key_resolver(payload_json: dict) -> str | None:
    data = payload_json.get("data")
    return data.get("PkgID") if isinstance(data, dict) else None


def test_manifest_without_material_identity_resolver_returns_missing() -> None:
    manifest = WorklinePluginManifest(
        plugin_key="test_plugin",
        contract_version="1.0",
        required_device_roles=(DeviceRoleRequirement("SCANNER"),),
        business_key_resolver=_business_key_resolver,
    )

    identity = manifest.resolve_material_identity(
        MaterialIdentityInput(
            source_payload={
                "data": {
                    "PkgID": "PKG-001",
                    "HHPN": "620100L00-011-G",
                    "LotCode": "8904936031",
                }
            }
        )
    )

    assert identity.resolution_status == MaterialIdentityResolutionStatus.MISSING
    assert identity.idempotency_key is None
    assert identity.display == {}


def test_hash_material_evidence_is_order_insensitive() -> None:
    first = hash_material_evidence({"b": 2, "a": {"x": 1}})
    second = hash_material_evidence({"a": {"x": 1}, "b": 2})

    assert first.startswith("sha256:")
    assert first == second
