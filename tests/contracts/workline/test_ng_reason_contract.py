"""Material-flow target NG reason contract."""

from __future__ import annotations

import pytest

from src.app.runtime.capabilities.material_flow.contracts.ng_reason import (
    BUILTIN_NG_REASONS,
    NgReasonDefinition,
    NgReasonSource,
    build_ng_reason_catalog,
)


def test_builtin_ng_reason_catalog_has_exact_target_identities() -> None:
    catalog = build_ng_reason_catalog()

    assert tuple(catalog.by_code) == (
        "UNKNOWN_PHYSICAL_STATE",
        "OPERATOR_JUDGED_NG",
        "RUNTIME_RECOVERY_NG",
    )
    assert {source.value for source in NgReasonSource} == {"DEVICE_ERROR", "RUNTIME", "MANUAL"}
    assert {source.value for source in catalog.by_source} == {"RUNTIME", "MANUAL"}
    assert catalog.reasons == BUILTIN_NG_REASONS


def test_ng_reason_definition_rejects_empty_identity_and_label() -> None:
    with pytest.raises(ValueError, match="canonical_code"):
        NgReasonDefinition(canonical_code="", label="reason", source=NgReasonSource.RUNTIME)
    with pytest.raises(ValueError, match="label"):
        NgReasonDefinition(canonical_code="REASON", label="", source=NgReasonSource.RUNTIME)


def test_ng_reason_catalog_groups_canonical_sources() -> None:
    catalog = build_ng_reason_catalog()

    assert tuple(reason.canonical_code for reason in catalog.by_source[NgReasonSource.RUNTIME]) == (
        "UNKNOWN_PHYSICAL_STATE",
        "RUNTIME_RECOVERY_NG",
    )
    assert tuple(reason.canonical_code for reason in catalog.by_source[NgReasonSource.MANUAL]) == (
        "OPERATOR_JUDGED_NG",
    )
    assert catalog.by_source.get(NgReasonSource.DEVICE_ERROR, ()) == ()
