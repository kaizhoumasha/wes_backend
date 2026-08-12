"""Compiled WMS profile 到共享 frozen HTTP binding 的结构合同。"""

from __future__ import annotations

import pytest

from src.app.runtime.system_capabilities.wms import provider_catalog
from src.app.wms_integration.operation_registry import ASYNC_EFFECT_OPERATIONS, QUERY_OPERATIONS
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog


@pytest.mark.parametrize("operation", QUERY_OPERATIONS, ids=lambda operation: operation.identity)
def test_all_18_queries_freeze_only_from_compiled_profile(operation) -> None:
    catalog = build_provider_catalog()
    compiled_endpoint = catalog.compiled_profile.operations[operation.identity]

    frozen = provider_catalog.freeze_wms_query_binding(
        catalog=catalog,
        operation_identity=operation.identity,
    )

    assert len(QUERY_OPERATIONS) == 18
    assert frozen.operation_identity == operation.identity
    assert frozen.target_snapshot.code == operation.target_code
    assert frozen.target_snapshot.url == compiled_endpoint.endpoint_template
    assert frozen.target_snapshot.http_method == operation.http_method.value
    assert frozen.target_snapshot.timeout_seconds == operation.budget.deadline_seconds
    assert frozen.auth_scheme == catalog.compiled_profile.profile.outbound_auth.scheme.value
    assert frozen.network_trust_mode == catalog.compiled_profile.profile.network_trust_mode
    assert frozen.credential_reference is catalog.compiled_profile.profile.outbound_auth.credential_reference


@pytest.mark.parametrize("operation", ASYNC_EFFECT_OPERATIONS, ids=lambda operation: operation.identity)
def test_all_async_effects_freeze_get_status_binding_from_compiled_profile(operation) -> None:
    catalog = build_provider_catalog()
    compiled_endpoint = catalog.compiled_profile.operations[operation.identity]

    frozen = provider_catalog.freeze_wms_effect_status_binding(
        catalog=catalog,
        operation_identity=operation.identity,
    )

    assert len(ASYNC_EFFECT_OPERATIONS) == 2
    assert compiled_endpoint.status_endpoint is not None
    assert frozen.operation_identity == operation.identity
    assert frozen.target_snapshot.code == operation.target_code
    assert frozen.target_snapshot.url == compiled_endpoint.status_endpoint
    assert frozen.target_snapshot.http_method == "GET"
    assert frozen.target_snapshot.timeout_seconds == operation.budget.deadline_seconds
    assert frozen.auth_scheme == catalog.compiled_profile.profile.outbound_auth.scheme.value
    assert frozen.network_trust_mode == catalog.compiled_profile.profile.network_trust_mode
    assert frozen.credential_reference is catalog.compiled_profile.profile.outbound_auth.credential_reference


def test_sync_effect_cannot_gain_status_binding() -> None:
    catalog = build_provider_catalog()
    sync_effect = next(
        binding.operation
        for binding in catalog.bindings
        if binding.operation.mode.value == "EFFECT" and not binding.operation.supports_status_query
    )

    with pytest.raises(ValueError, match="status binding requires an async EFFECT"):
        provider_catalog.freeze_wms_effect_status_binding(
            catalog=catalog,
            operation_identity=sync_effect.identity,
        )
