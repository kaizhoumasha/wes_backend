"""粗分机 WMS 准入 capability、adapter 与 profile 的跨域合同。"""

from __future__ import annotations

import inspect
from dataclasses import fields
from decimal import Decimal

import pytest

from src.app.contracts.external_contract_profile_catalog import (
    WMS_MATERIAL_FLOW_PRODUCTION_PROFILE,
    WMS_MATERIAL_FLOW_SANDBOX_PROFILE,
    ExternalContractProfileCatalog,
)
from src.app.runtime.capability_port_registry import CapabilityPortRegistry
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import PROFILE_FAMILY
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.definition import DEFINITION
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.handler import (
    RoughSorterInventoryAdmissionHandler,
)
from src.app.wms_integration.adapters.inventory_query_port_adapter import (
    WmsInventoryQueryPortAdapter,
    build_wms_inventory_query_port_factory,
)
from src.app.wms_integration.models import QueryInventoryResponse
from src.app.wms_integration.ports.inventory_query import (
    WmsInventoryQueryContractError,
    WmsInventoryQueryPort,
    WmsInventoryQueryRejected,
    WmsInventoryQueryUnavailable,
)
from src.app.wms_integration.services.exceptions import (
    WmsBusinessRejectedError,
    WmsCircuitOpenError,
    WmsEvidencePersistenceError,
    WmsIntegrationError,
    WmsTimeoutError,
    WmsUnavailableError,
)

RUNTIME_CAPABILITY_PROVIDER_PROFILES = {"WMS": WMS_MATERIAL_FLOW_SANDBOX_PROFILE}


class FakeTypedClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[object] = []

    async def query_inventory(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def typed_error(
    error_type: type[WmsIntegrationError],
    *,
    reason_code: str = "PROVIDER_SECRET_REASON",
) -> WmsIntegrationError:
    return error_type(
        "provider secret message: pallet=SECRET-42",
        operation_name="query_inventory",
        evidence_key="provider-secret-evidence",
        reason_code=reason_code,
        target_code="provider-secret-target",
    )


@pytest.mark.asyncio
async def test_production_adapter_maps_public_port_arguments_to_current_wms_dto() -> None:
    client = FakeTypedClient(
        QueryInventoryResponse.model_validate(
            {
                "request_id": "attempt-7:query-1",
                "items": [
                    {
                        "sku": "MAT-001",
                        "warehouse_code": "WH-A",
                        "lot_no": "LOT-1",
                        "available_qty": "4.25",
                    }
                ],
            }
        )
    )
    adapter = WmsInventoryQueryPortAdapter(client, request_id_factory=lambda: "attempt-7:query-1")

    items = await adapter.query_inventory("MAT-001", warehouse_code="WH-A")

    request = client.requests[0]
    assert request.model_dump(mode="json", exclude_none=True) == {
        "request_id": "attempt-7:query-1",
        "sku": "MAT-001",
        "warehouse_code": "WH-A",
    }
    assert len(items) == 1
    assert items[0].material_code == "MAT-001"
    assert items[0].batch_no == "LOT-1"
    assert items[0].quantity == Decimal("4.25")


@pytest.mark.asyncio
async def test_adapter_float_quantity_boundary_is_explicit_and_deterministic() -> None:
    precise_quantity = Decimal("0.12345678901234567890123456789")
    client = FakeTypedClient(
        QueryInventoryResponse.model_validate(
            {
                "items": [
                    {
                        "sku": "MAT-001",
                        "warehouse_code": "WH-A",
                        "lot_no": "LOT-1",
                        "available_qty": precise_quantity,
                    }
                ]
            }
        )
    )
    adapter = WmsInventoryQueryPortAdapter(client, request_id_factory=lambda: "attempt-precision")

    items = await adapter.query_inventory("MAT-001")

    assert items[0].quantity == float(precise_quantity)
    assert Decimal(str(items[0].quantity)) != precise_quantity


@pytest.mark.parametrize(
    "provider_error",
    [
        TimeoutError("provider secret timeout"),
        typed_error(WmsTimeoutError),
        typed_error(WmsUnavailableError),
        typed_error(WmsCircuitOpenError),
        typed_error(WmsEvidencePersistenceError),
        typed_error(WmsIntegrationError),
    ],
)
@pytest.mark.asyncio
async def test_adapter_translates_all_typed_technical_exception_families_without_leaking_details(
    provider_error: BaseException,
) -> None:
    adapter = WmsInventoryQueryPortAdapter(
        FakeTypedClient(provider_error),
        request_id_factory=lambda: "attempt-unavailable",
    )
    with pytest.raises(WmsInventoryQueryUnavailable) as exc_info:
        await adapter.query_inventory("MAT-001")

    assert str(exc_info.value) == "WMS inventory query unavailable"
    assert exc_info.value.__cause__ is provider_error
    assert "SECRET" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_adapter_translates_business_rejection_to_stable_redacted_port_error() -> None:
    provider_error = typed_error(WmsBusinessRejectedError)
    adapter = WmsInventoryQueryPortAdapter(
        FakeTypedClient(provider_error),
        request_id_factory=lambda: "attempt-rejected",
    )

    with pytest.raises(WmsInventoryQueryRejected) as exc_info:
        await adapter.query_inventory("MAT-001")

    assert str(exc_info.value) == "WMS inventory query rejected"
    assert exc_info.value.__cause__ is provider_error
    assert "SECRET" not in str(exc_info.value)


@pytest.mark.parametrize("reason_code", ["WMS_RESPONSE_PARSE_ERROR", "WMS_UNSUPPORTED_HTTP_METHOD"])
@pytest.mark.asyncio
async def test_adapter_translates_provider_protocol_failures_to_contract_error(reason_code: str) -> None:
    provider_error = typed_error(WmsUnavailableError, reason_code=reason_code)
    adapter = WmsInventoryQueryPortAdapter(
        FakeTypedClient(provider_error),
        request_id_factory=lambda: "attempt-contract",
    )

    with pytest.raises(WmsInventoryQueryContractError) as exc_info:
        await adapter.query_inventory("MAT-001")

    assert str(exc_info.value) == "invalid WMS inventory response"
    assert exc_info.value.__cause__ is provider_error
    assert "SECRET" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_adapter_translates_invalid_shape_at_port_boundary() -> None:
    invalid_adapter = WmsInventoryQueryPortAdapter(
        FakeTypedClient({"items": [{"wrong": "shape"}]}),
        request_id_factory=lambda: "attempt-invalid",
    )
    with pytest.raises(WmsInventoryQueryContractError):
        await invalid_adapter.query_inventory("MAT-001")


def test_adapter_factory_is_attempt_scoped_and_registry_does_not_cache_instances() -> None:
    factory = build_wms_inventory_query_port_factory(
        FakeTypedClient(QueryInventoryResponse(items=[])),
        request_id_factory=lambda: "attempt-scoped",
    )
    registry = CapabilityPortRegistry()
    registry.register(WmsInventoryQueryPort, factory)

    assert registry.get(WmsInventoryQueryPort) is not registry.get(WmsInventoryQueryPort)


def test_definition_profile_and_handler_keep_the_capability_boundary_closed() -> None:
    profile = RUNTIME_CAPABILITY_PROVIDER_PROFILES["WMS"]
    catalog = ExternalContractProfileCatalog([profile])
    resolved = catalog.resolve(
        provider_code="WMS",
        contract_version="2026-07-06.material-flow",
        environment="sandbox",
    )

    assert catalog.resolve_identity(resolved.identity) is resolved
    assert resolved.identity == "wms.2026-07-06.material-flow.sandbox"
    assert "WmsMasterDataPort.get_material" in resolved.runtime_capabilities_query
    assert "WmsInventoryQueryPort.query_inventory" in resolved.runtime_capabilities_query
    assert resolved.timeout_retry_query_timeout_seconds == 10
    assert DEFINITION.capability_key == "wms.rough_sorter_inventory_admission"
    assert DEFINITION.contract_version == "v1"
    assert DEFINITION.mode is SystemCapabilityMode.QUERY
    assert DEFINITION.required_ports == (WmsInventoryQueryPort,)
    assert DEFINITION.admission == PROFILE_FAMILY
    assert resolved.identity == f"{DEFINITION.admission}.sandbox"
    assert DEFINITION.timeout_seconds == resolved.timeout_retry_query_timeout_seconds
    assert DEFINITION.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL
    assert "profile" not in {field.name for field in fields(DEFINITION)}

    source = inspect.getsource(RoughSorterInventoryAdmissionHandler)
    forbidden = ("wms_integration.services", "wms_integration.models", "http_client", "WmsTimeoutError")
    assert all(token not in source for token in forbidden)


def test_production_profile_requires_explicit_authentication_material() -> None:
    sandbox_data = WMS_MATERIAL_FLOW_SANDBOX_PROFILE.model_dump(mode="python")

    with pytest.raises(ValueError, match=r"production.*security_profile"):
        type(WMS_MATERIAL_FLOW_SANDBOX_PROFILE).model_validate({**sandbox_data, "environment": "production"})

    assert WMS_MATERIAL_FLOW_PRODUCTION_PROFILE.security_profile.secret_kid == "wms-material-flow-production"
    assert WMS_MATERIAL_FLOW_PRODUCTION_PROFILE.security_profile.signature_algo == "HS256"

    outbound_only = type(WMS_MATERIAL_FLOW_SANDBOX_PROFILE).model_validate(
        {
            **sandbox_data,
            "environment": "production",
            "inbound_normalizers_event": [],
            "inbound_normalizers_result": [],
        }
    )
    assert outbound_only.environment == "production"
    assert outbound_only.security_profile.secret_kid is None


def test_profile_catalog_rejects_canonical_provider_identity_collision() -> None:
    profile = RUNTIME_CAPABILITY_PROVIDER_PROFILES["WMS"]
    lowercase_duplicate = type(profile).model_validate(
        {
            **profile.model_dump(mode="python"),
            "provider_code": "  wms  ",
        }
    )

    with pytest.raises(ValueError, match="重复 external contract profile identity"):
        ExternalContractProfileCatalog([profile, lowercase_duplicate])


def test_profile_catalog_rejects_same_canonical_identity_even_when_provider_and_version_boundaries_differ() -> None:
    profile = RUNTIME_CAPABILITY_PROVIDER_PROFILES["WMS"]
    first = type(profile).model_validate(
        {**profile.model_dump(mode="python"), "provider_code": "WMS", "contract_version": "a.b"}
    )
    second = type(profile).model_validate(
        {**profile.model_dump(mode="python"), "provider_code": "WMS.a", "contract_version": "b"}
    )

    with pytest.raises(ValueError, match="重复 external contract profile identity"):
        ExternalContractProfileCatalog([first, second])


def test_profile_catalog_resolves_provider_case_and_whitespace_canonically_but_keeps_version_environment_exact() -> (
    None
):
    profile = RUNTIME_CAPABILITY_PROVIDER_PROFILES["WMS"]
    catalog = ExternalContractProfileCatalog([profile])

    assert (
        catalog.resolve(
            provider_code="  wMs  ",
            contract_version=profile.contract_version,
            environment=profile.environment,
        )
        is profile
    )
    assert catalog.resolve_identity("WMS.2026-07-06.material-flow.sandbox") is profile
    with pytest.raises(LookupError):
        catalog.resolve(
            provider_code="WMS",
            contract_version=profile.contract_version.upper(),
            environment=profile.environment,
        )
