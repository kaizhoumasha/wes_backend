"""WMS typed operation Foundation 合同。"""

from __future__ import annotations

import importlib
import importlib.util
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.app.runtime.system_capabilities.wms import provider_catalog
from src.app.runtime.system_capabilities.wms.contracts import (
    WmsProviderProfile,
    WmsRetryPolicy,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.gateway import (
    FullBoxExchangeDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.gateway import ConfirmInboundDispatchGateway
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE
from src.app.wms_integration.adapters.query_inventory_operation_adapter import (
    ProviderQueryInventoryResponseDTO,
    map_provider_query_inventory_response,
)
from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest
from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest
from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationResult


def _load(module_name: str):
    assert importlib.util.find_spec(module_name) is not None, f"缺少 T2 合同模块: {module_name}"
    return importlib.import_module(module_name)


def test_four_real_operations_are_independent_decimal_contracts() -> None:
    query = _load("src.app.wms_integration.ports.query_inventory_operation")
    inbound = _load("src.app.wms_integration.ports.confirm_inbound_operation")
    binding = _load("src.app.wms_integration.ports.notify_pkg_binding_operation")
    exchange = _load("src.app.wms_integration.ports.full_box_exchange_operation")

    assert {
        query.OPERATION_IDENTITY,
        inbound.OPERATION_IDENTITY,
        binding.OPERATION_IDENTITY,
        exchange.OPERATION_IDENTITY,
    } == {
        "wms.inventory.query_inventory@v1",
        "wms.inventory.confirm_inbound@v1",
        "wms.fulfillment.notify_pkg_binding@v1",
        "wms.fulfillment.full_box_exchange@v1",
    }
    assert query.InventoryAuthorityItem.model_fields["available_quantity"].annotation is Decimal
    assert inbound.ConfirmInboundOperationRequest.model_fields["quantity"].annotation is Decimal
    assert (
        len(
            {
                query.InventoryQueryOperationRequest,
                inbound.ConfirmInboundOperationRequest,
                binding.NotifyPackageBindingOperationRequest,
                exchange.FullBoxExchangeOperationRequest,
            }
        )
        == 4
    )


def test_provider_query_mapping_preserves_missing_facts_and_decimal_precision() -> None:
    result = map_provider_query_inventory_response(
        {
            "items": [
                {
                    "sku": "MAT-001",
                    "available_qty": "9007199254740993.125",
                }
            ]
        }
    )

    item = result.items[0]
    assert item.available_quantity == Decimal("9007199254740993.125")
    assert item.warehouse_code is None
    assert item.storage_location_code is None
    assert item.owner_code is None
    assert result.source_version is None


@pytest.mark.parametrize("response_model", [ProviderQueryInventoryResponseDTO, InventoryQueryOperationResult])
def test_query_response_items_are_required(response_model: type) -> None:
    with pytest.raises(ValidationError, match="items"):
        response_model.model_validate({})


@pytest.mark.parametrize("response_model", [ProviderQueryInventoryResponseDTO, InventoryQueryOperationResult])
def test_query_response_accepts_explicit_empty_items(response_model: type) -> None:
    response = response_model.model_validate({"items": []})

    assert response.items == ()


@pytest.mark.parametrize(
    ("gateway_type", "request_type", "request_kwargs", "expected_payload"),
    [
        (
            ConfirmInboundDispatchGateway,
            ConfirmInboundOperationRequest,
            {
                "dispatch_key": "inbound:001",
                "inbound_key": "IN-001",
                "material_code": "MAT-001",
                "quantity": Decimal("1.250"),
                "warehouse_code": None,
            },
            {
                "dispatch_key": "inbound:001",
                "inbound_key": "IN-001",
                "material_code": "MAT-001",
                "quantity": "1.250",
            },
        ),
        (
            NotifyPackageBindingDispatchGateway,
            NotifyPackageBindingOperationRequest,
            {
                "dispatch_key": "binding:001",
                "provider_code": "WMS",
                "package_id": "PKG-001",
                "pallet_id": "PLT-001",
                "station_code": "ST-A",
            },
            {
                "dispatch_key": "binding:001",
                "package_id": "PKG-001",
                "pallet_id": "PLT-001",
                "station_code": "ST-A",
            },
        ),
        (
            FullBoxExchangeDispatchGateway,
            FullBoxExchangeOperationRequest,
            {
                "dispatch_key": "exchange:001",
                "provider_code": "WMS",
                "rack_id": "RACK-001",
                "empty_box_id": "EMPTY-001",
                "full_box_id": "FULL-001",
            },
            {
                "dispatch_key": "exchange:001",
                "rack_id": "RACK-001",
                "empty_box_id": "EMPTY-001",
                "full_box_id": "FULL-001",
            },
        ),
    ],
)
def test_each_effect_gateway_maps_typed_request_to_dispatch_envelope_once(
    monkeypatch: pytest.MonkeyPatch,
    gateway_type: type[
        ConfirmInboundDispatchGateway | NotifyPackageBindingDispatchGateway | FullBoxExchangeDispatchGateway
    ],
    request_type: type[
        ConfirmInboundOperationRequest | NotifyPackageBindingOperationRequest | FullBoxExchangeOperationRequest
    ],
    request_kwargs: dict[str, object],
    expected_payload: dict[str, object],
) -> None:
    monkeypatch.setattr(provider_catalog.settings, "WMS_SYNC_BASE_URL", "http://mock_wms:8011/api/wms")
    request = request_type(**request_kwargs)

    envelope = gateway_type().build_envelope(request, idempotency_key="intent-foundation-001")

    assert envelope.dispatch_key == request.dispatch_key
    assert envelope.idempotency_key == "intent-foundation-001"
    assert envelope.dispatch_type.value == "EXTERNAL_HTTP"
    assert envelope.target_type.value == "HTTP_ENDPOINT"
    assert envelope.payload_json == expected_payload
    assert "UNKNOWN" not in repr(envelope.payload_json)
    assert "" not in envelope.payload_json.values()


def test_provider_profile_rejects_duplicate_callback_for_effect() -> None:
    with pytest.raises(ValidationError, match="duplicate callback type"):
        WmsProviderProfile(
            identity=WMS_PROVIDER_PROFILE.identity,
            bindings=WMS_PROVIDER_PROFILE.bindings,
            callbacks=(*WMS_PROVIDER_PROFILE.callbacks, WMS_PROVIDER_PROFILE.callbacks[0]),
        )


@pytest.mark.parametrize("backoff", [float("nan"), float("inf"), float("-inf")])
def test_retry_policy_rejects_non_finite_backoff(backoff: float) -> None:
    with pytest.raises(ValidationError, match="backoff_seconds"):
        WmsRetryPolicy(max_attempts=2, backoff_seconds=(backoff,))


def test_runtime_profile_is_identity_only_and_production_rejects_none_auth() -> None:
    contracts = _load("src.app.runtime.system_capabilities.wms.contracts")

    assert set(contracts.ExternalContractProfile.model_fields) == {
        "provider_code",
        "contract_version",
        "environment",
    }
    profile = contracts.ExternalContractProfile(
        provider_code="WMS",
        contract_version="northbound.v1",
        environment="production",
    )
    assert profile.identity == "wms.northbound.v1.production"

    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")
    operation = catalog.WMS_PROVIDER_PROFILE.bindings[0].operation
    with pytest.raises(ValidationError, match=r"production.*NONE"):
        contracts.WmsProviderOperationBinding(
            profile=profile,
            operation=operation,
            outbound_auth=contracts.OutboundAuthProfile(scheme="NONE"),
        )


def test_provider_and_conformance_manifests_share_static_registry_identity() -> None:
    registry = _load("src.app.wms_integration.operation_registry")
    provider_manifest = _load("src.app.wms_integration.provider_manifest")
    manifest = _load("src.app.runtime.system_capabilities.wms.conformance_manifest")

    authored = tuple(operation.identity for operation in registry.WMS_OPERATIONS)
    provided = tuple(operation.identity for operation in provider_manifest.WMS_PROVIDER_OPERATION_MANIFEST)
    manifested = tuple(requirement.operation.identity for requirement in manifest.WMS_CONFORMANCE_MANIFEST.operations)

    assert authored == provided == manifested
    assert tuple(registry.WMS_OPERATION_BY_IDENTITY) == authored
    assert all("Port." not in identity for identity in authored)
