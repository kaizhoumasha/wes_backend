"""NG reason taxonomy tests."""

from src.workline_runtime.ng_reason import (
    BUILTIN_NG_REASONS,
    NgReasonDefinition,
    NgReasonSource,
    build_ng_reason_catalog,
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
        plugin_key="smt_classifier",
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
        plugin_key="smt_classifier",
        contract_version="1.0",
        maps_from=("SCAN_NG",),
    )

    catalog = build_ng_reason_catalog([plugin_reason])

    assert catalog.by_code["SCAN_NG"] == plugin_reason
    assert catalog.by_source[NgReasonSource.PLUGIN] == (plugin_reason,)
    assert "UNKNOWN_PHYSICAL_STATE" in catalog.by_code
