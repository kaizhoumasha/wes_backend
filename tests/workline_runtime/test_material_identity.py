"""Material identity contract tests."""

import src.workline_plugin_registry as registry
from src.workline_runtime.material_identity import (
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    hash_material_evidence,
)


def test_registry_material_identity_helper_returns_missing_default() -> None:
    identity = registry.resolve_workline_material_identity(
        "unknown_plugin",
        MaterialIdentityInput(
            source_payload={
                "data": {
                    "PkgID": "PKG-001",
                    "HHPN": "620100L00-011-G",
                    "LotCode": "8904936031",
                }
            }
        ),
    )

    assert identity.resolution_status == MaterialIdentityResolutionStatus.MISSING
    assert identity.idempotency_key is None
    assert identity.display == {}


def test_hash_material_evidence_is_order_insensitive() -> None:
    first = hash_material_evidence({"b": 2, "a": {"x": 1}})
    second = hash_material_evidence({"a": {"x": 1}, "b": 2})

    assert first.startswith("sha256:")
    assert first == second
