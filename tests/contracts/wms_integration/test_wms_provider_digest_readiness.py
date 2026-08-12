"""WMS Provider profile digest 与 lane readiness 合同。"""

from __future__ import annotations

from copy import deepcopy

from src.app.wms_integration.operation_contract import WmsExecutionLane
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from tests.contracts.wms_integration.provider_profile_support import build_provider_profile_payload


def _compile(payload: dict | None = None):
    from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    return compile_wms_provider_profile(
        WmsProviderProfileSettings.model_validate(payload or build_provider_profile_payload())
    )


def test_profile_revision_and_digest_are_stable_and_cover_all_supported_profile_fields() -> None:
    first = _compile()
    second = _compile(deepcopy(build_provider_profile_payload()))

    assert first.profile_revision == second.profile_revision
    assert first.profile_digest == second.profile_digest
    assert len(first.profile_revision) == len(first.profile_digest) == 64

    changed_auth = build_provider_profile_payload()
    changed_auth["outbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/factory@v3",
    }
    assert _compile(changed_auth).profile_digest != first.profile_digest


def test_endpoint_drift_changes_only_the_affected_operation_digest() -> None:
    original = _compile()
    changed_payload = build_provider_profile_payload()
    changed_identity = "wms.master_data.get_material@v1"
    changed_payload["operations"][changed_identity]["path"] = "/provider/v2/materials/{material_code}"
    changed = _compile(changed_payload)

    assert changed.profile_digest != original.profile_digest
    changed_operation_digests = {
        identity
        for identity in original.operations
        if original.operations[identity].endpoint_digest != changed.operations[identity].endpoint_digest
    }
    assert changed_operation_digests == {changed_identity}


def test_wes_and_fulfillment_assembly_share_digest_with_exact_lane_readiness() -> None:
    from src.app.wms_integration.provider_readiness import (
        WmsProviderProcessRole,
        build_wms_provider_readiness,
    )

    wes_compiled = _compile()
    fulfillment_compiled = _compile()
    wes = build_wms_provider_readiness(wes_compiled, process_role=WmsProviderProcessRole.WES)
    fulfillment = build_wms_provider_readiness(
        fulfillment_compiled,
        process_role=WmsProviderProcessRole.FULFILLMENT,
    )

    assert wes.profile_digest == fulfillment.profile_digest == wes_compiled.profile_digest
    expected_data = tuple(
        operation.identity for operation in WMS_OPERATIONS if operation.execution_lane is WmsExecutionLane.WMS_DATA
    )
    expected_fulfillment = tuple(
        operation.identity
        for operation in WMS_OPERATIONS
        if operation.execution_lane is WmsExecutionLane.WMS_FULFILLMENT
    )
    assert wes.operation_identities == expected_data
    assert fulfillment.operation_identities == expected_fulfillment
    assert len(wes.operation_identities) == 26
    assert len(wes.endpoint_keys) == 26
    assert len(fulfillment.operation_identities) == 3
    assert len(fulfillment.endpoint_keys) == 5

    async_fulfillment = {
        operation.identity
        for operation in WMS_OPERATIONS
        if operation.execution_lane is WmsExecutionLane.WMS_FULFILLMENT and operation.supports_status_query
    }
    assert {key.removesuffix(":status") for key in fulfillment.endpoint_keys if key.endswith(":status")} == (
        async_fulfillment
    )


def test_compiled_readiness_contains_only_frozen_contract_data() -> None:
    from src.app.wms_integration.provider_readiness import (
        WmsProviderProcessRole,
        build_wms_provider_readiness,
    )

    readiness = build_wms_provider_readiness(_compile(), process_role=WmsProviderProcessRole.WES)

    assert readiness.__dataclass_params__.frozen is True
    assert not hasattr(readiness, "http_client")
    assert not hasattr(readiness, "connection_pool")
    assert not hasattr(readiness, "breaker")
    assert not hasattr(readiness, "transport")
