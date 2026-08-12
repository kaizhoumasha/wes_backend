"""WMS typed operation registry 的共享合同门禁。"""

from __future__ import annotations

import importlib
import importlib.util
import json

from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
    build_provider_catalog,
)

EXPECTED_QUERY_IDENTITIES = (
    "wms.master_data.get_material@v1",
    "wms.master_data.list_materials@v1",
    "wms.master_data.list_zones@v1",
    "wms.master_data.list_locations@v1",
    "wms.master_data.get_rack@v1",
    "wms.master_data.list_racks@v1",
    "wms.master_data.get_bin@v1",
    "wms.document.get_grn@v1",
    "wms.document.list_grn_packages@v1",
    "wms.document.get_pick_order@v1",
    "wms.document.get_outbound_order@v1",
    "wms.document.get_wave@v1",
    "wms.document.get_task_snapshot@v1",
    "wms.inventory.query_inventory@v1",
    "wms.inventory.get_reservation@v1",
    "wms.reconciliation.check_bin_drift@v1",
    "wms.reconciliation.check_rack_drift@v1",
    "wms.reconciliation.check_full_drift@v1",
)
EXPECTED_EFFECT_IDENTITIES = (
    "wms.inventory.reserve_inventory@v1",
    "wms.inventory.release_reservation@v1",
    "wms.inventory.confirm_inbound@v1",
    "wms.inventory.confirm_outbound@v1",
    "wms.inventory.transfer_inventory@v1",
    "wms.inventory.confirm_return_putaway@v1",
    "wms.fulfillment.notify_pkg_binding@v1",
    "wms.fulfillment.request_rack_supply@v1",
    "wms.fulfillment.change_rack_face@v1",
    "wms.fulfillment.publish_manual_task@v1",
    "wms.fulfillment.cancel_request@v1",
)
EXPECTED_SYNC_EFFECTS = frozenset((*EXPECTED_EFFECT_IDENTITIES[:7], *EXPECTED_EFFECT_IDENTITIES[9:]))
EXPECTED_ASYNC_EFFECTS = frozenset(EXPECTED_EFFECT_IDENTITIES[7:9])
LIST_QUERY_IDENTITIES = frozenset(
    {
        "wms.master_data.list_materials@v1",
        "wms.master_data.list_zones@v1",
        "wms.master_data.list_locations@v1",
        "wms.master_data.list_racks@v1",
        "wms.document.list_grn_packages@v1",
        "wms.inventory.query_inventory@v1",
        "wms.reconciliation.check_bin_drift@v1",
        "wms.reconciliation.check_rack_drift@v1",
        "wms.reconciliation.check_full_drift@v1",
    }
)


def _load(module_name: str):
    assert importlib.util.find_spec(module_name) is not None, f"缺少静态合同模块: {module_name}"
    return importlib.import_module(module_name)


def test_static_registry_freezes_exactly_29_unique_operation_identities() -> None:
    registry = _load("src.app.wms_integration.operation_registry")

    assert tuple(item.identity for item in registry.QUERY_OPERATIONS) == EXPECTED_QUERY_IDENTITIES
    assert tuple(item.identity for item in registry.EFFECT_OPERATIONS) == EXPECTED_EFFECT_IDENTITIES
    assert tuple(item.identity for item in registry.WMS_OPERATIONS) == (
        *EXPECTED_QUERY_IDENTITIES,
        *EXPECTED_EFFECT_IDENTITIES,
    )
    assert len(registry.WMS_OPERATION_BY_IDENTITY) == len(registry.WMS_OPERATIONS) == 29
    assert len({item.request_model for item in registry.WMS_OPERATIONS}) == 29
    assert len({item.result_model for item in registry.WMS_OPERATIONS}) == 29


def test_legacy_wms_transport_effect_owners_are_absent_while_rack_supply_remains() -> None:
    registry = _load("src.app.wms_integration.operation_registry")

    assert "wms.fulfillment.request_rack_supply@v1" in registry.WMS_OPERATION_BY_IDENTITY
    assert "wms.fulfillment.request_rack_transport@v1" not in registry.WMS_OPERATION_BY_IDENTITY
    assert "wms.fulfillment.request_load_unit_transport@v1" not in registry.WMS_OPERATION_BY_IDENTITY


def test_every_typed_definition_owns_strict_models_and_frozen_budgets() -> None:
    registry = _load("src.app.wms_integration.operation_registry")

    for operation in registry.WMS_OPERATIONS:
        assert operation.request_model.__module__.startswith("src.app.wms_integration.ports.")
        assert operation.result_model.__module__.startswith("src.app.wms_integration.ports.")
        assert operation.request_model.model_config["extra"] == "forbid"
        assert operation.result_model.model_config["extra"] == "forbid"
        assert operation.budget.deadline_seconds == 10
        assert operation.budget.max_attempts == 3
        assert operation.error_codes
        assert operation.reject_codes


def test_operation_contract_does_not_expose_retired_candidate_window() -> None:
    contracts = _load("src.app.wms_integration.operation_contract")
    provider_contracts = _load("src.app.runtime.system_capabilities.wms.contracts")

    assert "max_candidate_count" not in contracts.WmsOperationDefinition.model_fields
    assert "max_candidate_count" not in provider_contracts.WmsProviderOperationBinding.model_computed_fields


def test_query_method_and_pagination_contracts_are_closed() -> None:
    registry = _load("src.app.wms_integration.operation_registry")
    contracts = _load("src.app.wms_integration.operation_contract")

    for operation in registry.QUERY_OPERATIONS:
        assert operation.mode is contracts.WmsOperationMode.QUERY
        assert operation.completion_mode is None
        assert operation.execution_lane is contracts.WmsExecutionLane.WMS_DATA
        assert operation.side_effect_free is True
        if operation.identity in LIST_QUERY_IDENTITIES:
            assert operation.pagination is not None
            assert {"items", "next_cursor"} <= set(operation.result_model.model_fields)
            assert operation.budget.max_rows is not None
        else:
            assert operation.pagination is None


def test_effect_completion_modes_lanes_and_status_capability_are_static() -> None:
    registry = _load("src.app.wms_integration.operation_registry")
    contracts = _load("src.app.wms_integration.operation_contract")

    for operation in registry.EFFECT_OPERATIONS:
        assert operation.mode is contracts.WmsOperationMode.EFFECT
        assert operation.http_method is contracts.WmsHttpMethod.POST
        if operation.identity in EXPECTED_SYNC_EFFECTS:
            assert operation.completion_mode is contracts.WmsCompletionMode.SYNC_RESULT
            assert operation.supports_status_query is False
        else:
            assert operation.identity in EXPECTED_ASYNC_EFFECTS
            assert operation.completion_mode is contracts.WmsCompletionMode.ASYNC_TASK
            assert operation.supports_status_query is True

    assert (
        registry.WMS_OPERATION_BY_IDENTITY["wms.fulfillment.cancel_request@v1"].execution_lane
        is contracts.WmsExecutionLane.WMS_FULFILLMENT
    )
    assert all(
        registry.WMS_OPERATION_BY_IDENTITY[identity].execution_lane is contracts.WmsExecutionLane.WMS_DATA
        for identity in EXPECTED_SYNC_EFFECTS - {"wms.fulfillment.cancel_request@v1"}
    )


def test_terminal_identity_validator_is_static_and_only_authored_for_supported_effects() -> None:
    import pytest
    from pydantic import ValidationError

    registry = _load("src.app.wms_integration.operation_registry")
    contracts = _load("src.app.wms_integration.operation_contract")

    def definition_payload(operation):
        return operation.model_dump(exclude={"supports_status_query"})

    query = registry.QUERY_OPERATIONS[0]
    with pytest.raises(ValidationError, match="QUERY must not declare terminal_identity_validator"):
        contracts.WmsOperationDefinition.model_validate(
            {
                **definition_payload(query),
                "terminal_identity_validator": lambda _request, _result: None,
            }
        )

    sync_effect = next(
        operation
        for operation in registry.EFFECT_OPERATIONS
        if operation.completion_mode is contracts.WmsCompletionMode.SYNC_RESULT
    )
    with pytest.raises(ValidationError, match="SYNC_RESULT EFFECT requires terminal_identity_validator"):
        contracts.WmsOperationDefinition.model_validate(definition_payload(sync_effect))


def test_provider_conformance_scenarios_and_mock_fixtures_derive_from_registry() -> None:
    registry = _load("src.app.wms_integration.operation_registry")
    manifest = _load("src.app.wms_integration.provider_manifest")
    conformance = _load("src.app.runtime.system_capabilities.wms.conformance_manifest")
    fixtures = _load("tests.mock.wms_operation_fixtures")

    expected = tuple(item.identity for item in registry.WMS_OPERATIONS)
    compiled_profile = build_compiled_provider_profile()
    conformance_manifest = conformance.build_wms_conformance_manifest(compiled_profile)
    assert tuple(item.identity for item in manifest.WMS_PROVIDER_OPERATION_MANIFEST) == expected
    assert tuple(item.operation.identity for item in conformance_manifest.operations) == expected
    assert set(fixtures.REQUEST_FIXTURES) == set(expected)
    assert set(fixtures.RESULT_FIXTURES) == set(expected)
    for operation in registry.WMS_OPERATIONS:
        operation.request_model.model_validate_json(json.dumps(fixtures.REQUEST_FIXTURES[operation.identity]))
        operation.result_model.model_validate_json(json.dumps(fixtures.RESULT_FIXTURES[operation.identity]))
    assert set().union(*(scenario.operation_identities for scenario in manifest.WMS_BUSINESS_SCENARIO_MANIFEST)) == set(
        expected
    )
    assert {scenario.scenario_code for scenario in manifest.WMS_BUSINESS_SCENARIO_MANIFEST} == {
        "MASTER_DATA_AND_ROUTING",
        "RECEIVING",
        "OUTBOUND_AND_RESERVATION",
        "INVENTORY_ACCOUNTING",
        "RACK_FULFILLMENT",
        "RECOVERY_AND_RECONCILIATION",
    }


def test_external_http_effect_bindings_accept_only_registry_target_codes() -> None:
    from src.app.runtime.system_capabilities.wms import provider_catalog

    registry = _load("src.app.wms_integration.operation_registry")
    expected = {
        operation.identity: (operation.target_code,)
        for operation in registry.WMS_OPERATIONS
        if operation.mode.value == "EFFECT"
    }
    catalog = build_provider_catalog(build_hmac_provider_profile_payload())
    effect_profile = provider_catalog._external_http_effect_profile(catalog)
    actual = {binding.operation_identity: binding.allowed_target_codes for binding in effect_profile.bindings}

    assert effect_profile.environment == "production"
    assert actual == expected


def test_compiled_none_profile_is_the_only_source_for_frozen_effect_security() -> None:
    from src.app.runtime.system_capabilities.wms import provider_catalog

    catalog = build_provider_catalog()
    operation = next(binding.operation for binding in catalog.bindings if binding.operation.mode.value == "EFFECT")
    frozen = provider_catalog.freeze_wms_effect_binding(
        catalog=catalog,
        profile_identity=catalog.profile_identity,
        operation_identity=operation.identity,
        target_code=operation.target_code,
    )

    assert frozen.auth_scheme == "NONE"
    assert frozen.network_trust_mode == "isolated_lan"
    assert frozen.credential_reference is None
    assert (
        frozen.target_snapshot.url == catalog.compiled_profile.operations[frozen.operation_identity].endpoint_template
    )


def test_async_effect_runtime_classification_is_exact_registry_derivative() -> None:
    from src.app.sys.models.outbox import WMS_ASYNC_EFFECT_OPERATION_IDENTITIES
    from src.app.wms_integration.operation_registry import WMS_OPERATIONS
    from src.app.wms_integration.ports.effect_status import WMS_EFFECT_OPERATION_IDENTITIES

    expected = frozenset(operation.identity for operation in WMS_OPERATIONS if operation.supports_status_query)

    assert WMS_EFFECT_OPERATION_IDENTITIES == WMS_ASYNC_EFFECT_OPERATION_IDENTITIES == expected


def test_async_ack_closed_set_is_a_registry_derivative() -> None:
    from src.app.wms_integration.operation_registry import ASYNC_EFFECT_OPERATION_IDENTITIES
    from src.app.wms_integration.ports.fulfillment_operations import ASYNC_FULFILLMENT_OPERATION_IDENTITIES

    assert ASYNC_FULFILLMENT_OPERATION_IDENTITIES == ASYNC_EFFECT_OPERATION_IDENTITIES


def test_all_wire_models_use_true_strict_validation() -> None:
    registry = _load("src.app.wms_integration.operation_registry")

    for operation in registry.WMS_OPERATIONS:
        assert operation.request_model.model_config["strict"] is True
        assert operation.result_model.model_config["strict"] is True


def test_backoff_budget_rejects_non_finite_values() -> None:
    import pytest
    from pydantic import ValidationError

    contract = _load("src.app.wms_integration.operation_contract")
    payload = contract.EFFECT_BUDGET.model_dump()
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError, match="backoff_seconds"):
            contract.WmsOperationBudget.model_validate({**payload, "backoff_seconds": (invalid, 2)})


def test_mock_validator_consumes_static_effect_definitions_and_rejects_query_submit() -> None:
    import pytest

    registry = _load("src.app.wms_integration.operation_registry")
    fixtures = _load("tests.mock.wms_operation_fixtures")
    mock_contract = _load("tests.mock.wms_northbound_contract")

    for operation in registry.EFFECT_OPERATIONS:
        validated = mock_contract.validate_typed_request(
            operation.identity,
            fixtures.REQUEST_FIXTURES[operation.identity],
        )
        assert validated["dispatch_key"] == fixtures.REQUEST_FIXTURES[operation.identity]["dispatch_key"]

    with pytest.raises(mock_contract.NorthboundPayloadValidationError, match="only accepts EFFECT"):
        mock_contract.validate_typed_request(
            registry.QUERY_OPERATIONS[0].identity,
            fixtures.REQUEST_FIXTURES[registry.QUERY_OPERATIONS[0].identity],
        )
