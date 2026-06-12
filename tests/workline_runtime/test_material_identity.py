"""Material identity contract tests."""

import src.workline_plugin_registry as registry
from src.workline_plugin_registry import WorklinePluginDefinition
from src.workline_runtime.material_identity import (
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    hash_material_evidence,
)


class MaterialIdentityMissingCapabilityPlugin:
    """不提供 material identity resolver 的 registry 插件。"""


def test_registry_material_identity_helper_returns_missing_default() -> None:
    plugin_key = "material_identity_missing_capability"
    old_definition = registry.WORKLINE_PLUGIN_REGISTRY.get(plugin_key)
    registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = WorklinePluginDefinition(
        plugin_key=plugin_key,
        plugin_module=__name__,
        plugin_class_name="MaterialIdentityMissingCapabilityPlugin",
    )
    try:
        identity = registry.resolve_workline_material_identity(
            plugin_key,
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
    finally:
        if old_definition is None:
            registry.WORKLINE_PLUGIN_REGISTRY.pop(plugin_key, None)
        else:
            registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = old_definition

    assert identity.resolution_status == MaterialIdentityResolutionStatus.MISSING
    assert identity.idempotency_key is None
    assert identity.display == {}


def test_hash_material_evidence_is_order_insensitive() -> None:
    first = hash_material_evidence({"b": 2, "a": {"x": 1}})
    second = hash_material_evidence({"a": {"x": 1}, "b": 2})

    assert first.startswith("sha256:")
    assert first == second
