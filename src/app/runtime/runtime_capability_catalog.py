"""Static runtime capability wiring for target-state runtime capability wiring."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import SorterInboundRuntimeService
from src.app.runtime.capability_dispatcher import (
    RuntimeCapabilityCatalog,
    RuntimeCapabilityDefinition,
    RuntimeCapabilityDispatcher,
    RuntimeCapabilityUndeclaredError,
)

_ROUGH_SORTER_INBOUND = "rough_sorter_inbound"
_WMS_EFFECT_CAPABILITIES = (
    "WmsFulfillmentPort.notify_pkg_binding",
    "WmsInventoryTransactionPort.confirm_inbound",
)
_SORTER_INBOUND_SERVICE = SorterInboundRuntimeService()


def _non_empty_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _payload_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _build_wms_profile(provider_code: str) -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code=provider_code,
        contract_version="2026-07-06.material-flow",
        environment="sandbox",
        runtime_capabilities_query=[
            "WmsInventoryQueryPort.query_inventory",
            "WmsMasterDataPort.get_material",
        ],
        runtime_capabilities_effect=list(_WMS_EFFECT_CAPABILITIES),
        inbound_normalizers_event=["WMS_ROUGH_SORTER_INBOUND"],
        inbound_normalizers_result=[],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/default",
        fixture_set_required_cases=["success"],
    )


def _build_rough_sorter_inbound_plan(normalized_input: Any) -> Any:
    payload = _payload_mapping(getattr(normalized_input, "payload", None))
    data = _payload_mapping(payload.get("data"))
    plan_payload = {**payload, **data}
    return _SORTER_INBOUND_SERVICE.build_rough_sorter_inbound_plan(plan_payload)


RUNTIME_CAPABILITY_CATALOG = RuntimeCapabilityCatalog(
    [
        RuntimeCapabilityDefinition(
            capability_key=_ROUGH_SORTER_INBOUND,
            contract_capability=_WMS_EFFECT_CAPABILITIES[0],
            contract_capabilities=_WMS_EFFECT_CAPABILITIES,
            handler=_build_rough_sorter_inbound_plan,
        )
    ]
)
runtime_capability_dispatcher = RuntimeCapabilityDispatcher(RUNTIME_CAPABILITY_CATALOG)
RUNTIME_CAPABILITY_PROVIDER_PROFILES = {
    "WMS": _build_wms_profile("WMS"),
}


def resolve_runtime_capability_profile(normalized_input: Any) -> ExternalContractProfile:
    """Resolve provider profile for normalized runtime capability input."""

    payload = _payload_mapping(getattr(normalized_input, "payload", None))
    candidates = (
        getattr(normalized_input, "source_system", None),
        payload.get("source_system"),
        payload.get("provider_code"),
    )
    for candidate in candidates:
        provider_code = _non_empty_str(candidate)
        if provider_code is None:
            continue
        normalized_provider = provider_code.upper()
        profile = RUNTIME_CAPABILITY_PROVIDER_PROFILES.get(normalized_provider)
        if profile is not None:
            return profile
        provider_prefix = normalized_provider.split("-", 1)[0]
        profile = RUNTIME_CAPABILITY_PROVIDER_PROFILES.get(provider_prefix)
        if profile is not None:
            return profile

    capability_key = getattr(normalized_input, "runtime_capability", None)
    raise RuntimeCapabilityUndeclaredError(f"provider profile required for runtime capability: {capability_key}")


__all__ = [
    "RUNTIME_CAPABILITY_CATALOG",
    "RUNTIME_CAPABILITY_PROVIDER_PROFILES",
    "resolve_runtime_capability_profile",
    "runtime_capability_dispatcher",
]
