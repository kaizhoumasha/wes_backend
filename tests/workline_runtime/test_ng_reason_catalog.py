"""NG reason taxonomy tests."""

import src.workline_plugin_registry as registry
from src.workline_plugin_registry import WorklinePluginDefinition
from src.workline_runtime.ng_reason import (
    BUILTIN_NG_REASONS,
    NgReasonDefinition,
    NgReasonSource,
    build_ng_reason_catalog,
)


class NgReasonMissingCatalogPlugin:
    """仅保留旧字段的 registry 插件。"""

    ng_reason_catalog = (
        NgReasonDefinition(
            canonical_code="LEGACY_ONLY_NG",
            label="旧字段 NG",
            source=NgReasonSource.PLUGIN,
            plugin_key="ng_reason_missing_catalog",
            contract_version="1.0",
            maps_from=("LEGACY_ONLY_NG",),
        ),
    )


def test_builtin_ng_reasons_cover_runtime_manual_and_unknown_physical_state() -> None:
    codes = {reason.canonical_code for reason in BUILTIN_NG_REASONS}

    assert {"UNKNOWN_PHYSICAL_STATE", "OPERATOR_JUDGED_NG", "RUNTIME_RECOVERY_NG"} <= codes
    assert all(reason.plugin_key is None for reason in BUILTIN_NG_REASONS)


def test_ng_reason_catalog_rejects_duplicate_canonical_codes() -> None:
    duplicate = NgReasonDefinition(
        canonical_code="SCAN_NG",
        label="扫码异常",
        source=NgReasonSource.PLUGIN,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        maps_from=("SCAN_NG",),
    )

    try:
        build_ng_reason_catalog([duplicate, duplicate])
    except ValueError as exc:
        assert "duplicate NG reason canonical_code" in str(exc)
    else:
        raise AssertionError("duplicate NG reason catalog entries must be rejected")


def test_ng_reason_catalog_keeps_plugin_and_builtin_groups() -> None:
    plugin_reason = NgReasonDefinition(
        canonical_code="SCAN_NG",
        label="扫码异常",
        source=NgReasonSource.PLUGIN,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        maps_from=("SCAN_NG",),
    )

    catalog = build_ng_reason_catalog([plugin_reason])

    assert catalog.by_code["SCAN_NG"] == plugin_reason
    assert catalog.by_source[NgReasonSource.PLUGIN] == (plugin_reason,)
    assert "UNKNOWN_PHYSICAL_STATE" in catalog.by_code


def test_registry_ng_reason_helper_returns_empty_catalog_default() -> None:
    plugin_key = "ng_reason_missing_catalog"
    old_definition = registry.WORKLINE_PLUGIN_REGISTRY.get(plugin_key)
    registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = WorklinePluginDefinition(
        plugin_key=plugin_key,
        plugin_module=__name__,
        plugin_class_name="NgReasonMissingCatalogPlugin",
    )
    try:
        assert registry.list_workline_ng_reasons(plugin_key) == ()
    finally:
        if old_definition is None:
            registry.WORKLINE_PLUGIN_REGISTRY.pop(plugin_key, None)
        else:
            registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = old_definition
