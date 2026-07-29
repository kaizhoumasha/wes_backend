"""WMS 全工厂 35 项 operation 的静态合同门禁。"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path

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
    "wms.document.validate_rough_sorter_admission@v1",
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
    "wms.fulfillment.request_rack_transport@v1",
    "wms.fulfillment.change_rack_face@v1",
    "wms.fulfillment.full_box_exchange@v1",
    "wms.fulfillment.move_bins_to_conveyor_entry@v1",
    "wms.fulfillment.move_bins_from_conveyor_exit@v1",
    "wms.fulfillment.request_load_unit_transport@v1",
    "wms.fulfillment.publish_manual_task@v1",
    "wms.fulfillment.cancel_request@v1",
)
EXPECTED_SYNC_EFFECTS = frozenset((*EXPECTED_EFFECT_IDENTITIES[:7], *EXPECTED_EFFECT_IDENTITIES[14:]))
EXPECTED_ASYNC_EFFECTS = frozenset(EXPECTED_EFFECT_IDENTITIES[7:14])
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


def test_static_registry_freezes_exactly_35_unique_operation_identities() -> None:
    registry = _load("src.app.wms_integration.operation_registry")

    assert tuple(item.identity for item in registry.QUERY_OPERATIONS) == EXPECTED_QUERY_IDENTITIES
    assert tuple(item.identity for item in registry.EFFECT_OPERATIONS) == EXPECTED_EFFECT_IDENTITIES
    assert tuple(item.identity for item in registry.WMS_OPERATIONS) == (
        *EXPECTED_QUERY_IDENTITIES,
        *EXPECTED_EFFECT_IDENTITIES,
    )
    assert len(registry.WMS_OPERATION_BY_IDENTITY) == len(registry.WMS_OPERATIONS) == 35
    assert len({item.request_model for item in registry.WMS_OPERATIONS}) == 35
    assert len({item.result_model for item in registry.WMS_OPERATIONS}) == 35


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


def test_query_method_pagination_and_q19_contract_are_closed() -> None:
    import pytest
    from pydantic import ValidationError

    registry = _load("src.app.wms_integration.operation_registry")
    contracts = _load("src.app.wms_integration.operation_contract")
    fixtures = _load("tests.mock.wms_operation_fixtures")

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

    q19 = registry.WMS_OPERATION_BY_IDENTITY["wms.document.validate_rough_sorter_admission@v1"]
    assert q19.http_method is contracts.WmsHttpMethod.POST
    assert set(q19.request_model.model_fields) == {
        "raw_code",
        "six_in_one",
        "reel_diameter_mm",
        "reel_thickness_mm",
        "station_code",
        "workline_id",
        "session_id",
        "correlation_id",
    }
    assert set(q19.result_model.model_fields) == {
        "decision",
        "reason_code",
        "grn_id",
        "po_number",
        "po_item",
        "material_code",
        "pkg_id",
        "measurement_decision",
        "standard_reel_diameter_mm",
        "reel_diameter_tolerance_mm",
        "standard_reel_thickness_mm",
        "reel_thickness_tolerance_mm",
        "rule_version",
        "source_version",
    }
    assert q19.reject_codes == (
        "GRN_NOT_FOUND",
        "PACKAGE_NOT_FOUND",
        "PACKAGE_GRN_MISMATCH",
        "MATERIAL_MISMATCH",
        "QUANTITY_MISMATCH",
        "MEASUREMENT_OUT_OF_TOLERANCE",
        "PACKAGE_NOT_ADMISSIBLE",
    )
    invalid_reject = {
        **fixtures.RESULT_FIXTURES[q19.identity],
        "decision": "REJECT",
        "measurement_decision": "REJECT",
        "reason_code": "UNDECLARED_REASON",
    }
    with pytest.raises(ValidationError, match="reason_code"):
        q19.result_model.model_validate(invalid_reject)


def test_q19_admit_requires_complete_matched_identity_but_reject_does_not() -> None:
    import pytest
    from pydantic import ValidationError

    registry = _load("src.app.wms_integration.operation_registry")
    fixtures = _load("tests.mock.wms_operation_fixtures")
    q19 = registry.WMS_OPERATION_BY_IDENTITY["wms.document.validate_rough_sorter_admission@v1"]
    valid_admit = fixtures.RESULT_FIXTURES[q19.identity]

    for missing_field in ("grn_id", "po_number", "po_item", "material_code", "pkg_id"):
        invalid_admit = {**valid_admit, missing_field: None}
        with pytest.raises(ValidationError, match="complete matched identity"):
            q19.result_model.model_validate(invalid_admit)

    reject_without_matched_identity = {
        **valid_admit,
        "decision": "REJECT",
        "reason_code": "GRN_NOT_FOUND",
        "measurement_decision": "REJECT",
        "grn_id": None,
        "po_number": None,
        "po_item": None,
        "material_code": None,
        "pkg_id": None,
    }
    assert q19.result_model.model_validate(reject_without_matched_identity).decision == "REJECT"


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


def test_provider_conformance_scenarios_and_mock_fixtures_derive_from_registry() -> None:
    registry = _load("src.app.wms_integration.operation_registry")
    manifest = _load("src.app.wms_integration.provider_manifest")
    conformance = _load("src.app.runtime.system_capabilities.wms.conformance_manifest")
    fixtures = _load("tests.mock.wms_operation_fixtures")

    expected = tuple(item.identity for item in registry.WMS_OPERATIONS)
    assert tuple(item.identity for item in manifest.WMS_PROVIDER_OPERATION_MANIFEST) == expected
    assert tuple(item.operation.identity for item in conformance.WMS_CONFORMANCE_MANIFEST.operations) == expected
    assert set(fixtures.REQUEST_FIXTURES) == set(expected)
    assert set(fixtures.RESULT_FIXTURES) == set(expected)
    for operation in registry.WMS_OPERATIONS:
        operation.request_model.model_validate(fixtures.REQUEST_FIXTURES[operation.identity])
        operation.result_model.model_validate(fixtures.RESULT_FIXTURES[operation.identity])
    assert set().union(*(scenario.operation_identities for scenario in manifest.WMS_BUSINESS_SCENARIO_MANIFEST)) == set(
        expected
    )
    assert all("wms.transport." not in identity for identity in expected)
    assert set(manifest.LEGACY_TRANSPORT_MIGRATION_MANIFEST) == {
        "wms.transport.rack@v1",
        "wms.transport.handling@v1",
    }


def test_active_provider_profile_and_runtime_index_are_exact_registry_derivatives() -> None:
    registry = _load("src.app.wms_integration.operation_registry")
    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")
    generated = _load("src.app.runtime.system_capabilities.wms.generated_operation_index")

    expected = tuple(operation.identity for operation in registry.WMS_OPERATIONS)
    assert tuple(binding.operation.identity for binding in catalog.WMS_PROVIDER_PROFILE.bindings) == expected
    assert expected == generated.WMS_OPERATION_IDENTITIES
    assert tuple(generated.WMS_OPERATION_INDEX) == expected
    assert all(generated.WMS_OPERATION_INDEX[operation.identity] is operation for operation in registry.WMS_OPERATIONS)


def test_external_http_effect_bindings_accept_only_registry_target_codes() -> None:
    from src.app.runtime.system_capabilities.wms import provider_catalog

    registry = _load("src.app.wms_integration.operation_registry")
    expected = {
        operation.identity: (operation.target_code,)
        for operation in registry.WMS_OPERATIONS
        if operation.mode.value == "EFFECT"
    }
    actual = {
        binding.operation_identity: binding.allowed_target_codes
        for binding in provider_catalog.WMS_EXTERNAL_HTTP_EFFECT_PROFILE.bindings
    }

    assert actual == expected


def test_async_effect_runtime_classification_is_exact_registry_derivative() -> None:
    from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_IDENTITIES
    from src.app.sys.models.outbox import WMS_ASYNC_EFFECT_OPERATION_IDENTITIES
    from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS
    from src.app.wms_integration.ports.effect_status import WMS_EFFECT_OPERATION_IDENTITIES

    expected = frozenset(operation.identity for operation in WMS_OPERATIONS if operation.supports_status_query)

    assert WMS_EFFECT_OPERATION_IDENTITIES == WMS_ASYNC_EFFECT_OPERATION_IDENTITIES == expected
    assert WMS_OPERATION_BY_IDENTITY["wms.inventory.confirm_inbound@v1"].supports_status_query is False
    assert WMS_OPERATION_BY_IDENTITY["wms.fulfillment.notify_pkg_binding@v1"].supports_status_query is False
    assert WMS_OPERATION_BY_IDENTITY["wms.fulfillment.full_box_exchange@v1"].supports_status_query is True
    assert set(SYSTEM_CAPABILITY_IDENTITIES).isdisjoint(
        {
            ("wms.inventory.confirm_inbound", "v1"),
            ("wms.fulfillment.notify_pkg_binding", "v1"),
            ("wms.fulfillment.full_box_exchange", "v1"),
        }
    )


def test_definition_and_query_transport_expose_only_current_field_names() -> None:
    from src.app.wms_integration.operation_contract import WmsOperationBudget, WmsOperationDefinition
    from src.app.wms_integration.services import query_transport

    assert "timeout_seconds" not in WmsOperationBudget.__dict__
    assert "endpoint_path" not in WmsOperationDefinition.__dict__
    assert "retry_policy" not in WmsOperationDefinition.__dict__

    source = Path(query_transport.__file__).read_text()
    assert ".endpoint_path" not in source
    assert ".retry_policy" not in source
    assert ".timeout_seconds" not in source


def test_business_blueprint_uses_only_current_async_status_contract() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    blueprint = (repository_root / "docs/business/wms_rcs_interface_requirements.md").read_text()

    assert "WMS_TRANSPORT_COMPLETED" not in blueprint
    assert "WMS_FULL_BOX_EXCHANGE_RESULT" not in blueprint
    assert "/api/wes/transport-request" not in blueprint
    assert "wms_full_factory_operation_blueprint.md" in blueprint
    assert "WMS_EFFECT_STATUS_HINT" in blueprint


def test_runtime_operation_index_has_no_codegen_or_parallel_builder() -> None:
    generated = _load("src.app.runtime.system_capabilities.wms.generated_operation_index")
    repository_root = Path(__file__).resolve().parents[3]

    assert importlib.util.find_spec("src.app.runtime.system_capabilities.wms.operation_index_builder") is None
    assert not (repository_root / "scripts/generate_wms_operation_index.py").exists()
    source = inspect.getsource(generated)
    assert "src.app.wms_integration.operation_registry" in source
    assert "provider_catalog" not in source


def test_grn_and_batch_contracts_have_no_legacy_shape() -> None:
    document = _load("src.app.wms_integration.ports.document")
    registry = _load("src.app.wms_integration.operation_registry")

    assert importlib.util.find_spec("src.app.wms_integration.ports.fulfillment") is None
    assert not hasattr(document, "WmsGrnItem")
    assert not hasattr(document.WmsDocumentPort, "list_grn_items")
    assert "item_count" not in document.WmsGrnInfo.model_fields
    assert {
        "po_number",
        "po_item",
        "material_code",
        "planned_quantity",
        "received_quantity",
        "remaining_quantity",
    } <= set(document.WmsGrnInfo.model_fields)
    e11 = registry.WMS_OPERATION_BY_IDENTITY["wms.fulfillment.full_box_exchange@v1"]
    e12 = registry.WMS_OPERATION_BY_IDENTITY["wms.fulfillment.move_bins_to_conveyor_entry@v1"]
    e13 = registry.WMS_OPERATION_BY_IDENTITY["wms.fulfillment.move_bins_from_conveyor_exit@v1"]
    assert "empty_box_id" not in e11.request_model.model_fields
    assert "items" in e12.request_model.model_fields
    assert "candidate_items" in e13.request_model.model_fields


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
