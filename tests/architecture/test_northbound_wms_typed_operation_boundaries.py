"""北向 WMS typed operation 架构边界。"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str):
    assert importlib.util.find_spec(module_name) is not None, f"缺少 T2 架构模块: {module_name}"
    return importlib.import_module(module_name)


def test_capability_catalog_reuses_port_models_as_single_operation_schema() -> None:
    generated = _load("src.app.runtime.system_capabilities.wms.generated_operation_index")

    for operation in generated.WMS_OPERATION_INDEX.values():
        assert operation.request_model.__module__.startswith("src.app.wms_integration.ports.")
        assert operation.result_model.__module__.startswith("src.app.wms_integration.ports.")

    capability_root = REPO_ROOT / "src/app/runtime/system_capabilities/wms"
    duplicated_models = []
    for path in capability_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "OperationRequest(BaseModel)" in source or "OperationResult(BaseModel)" in source:
            duplicated_models.append(path.relative_to(REPO_ROOT).as_posix())
    assert duplicated_models == []


def test_system_capability_definition_stays_transport_agnostic() -> None:
    definition = _load("src.app.runtime.system_capabilities.definition")

    fields = set(definition.SystemCapabilityDefinition.__dataclass_fields__)
    forbidden = {
        "endpoint",
        "http_method",
        "outbound_auth",
        "credential_reference",
        "payload_builder",
        "dispatch_factory",
        "fixture_set_path",
    }
    assert fields.isdisjoint(forbidden)


def test_runtime_profile_and_operation_contract_do_not_embed_build_fixtures_or_string_method_lists() -> None:
    contracts = _load("src.app.runtime.system_capabilities.wms.contracts")
    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")

    runtime_source = inspect.getsource(contracts.ExternalContractProfile)
    profile_source = inspect.getsource(catalog)
    forbidden = ("fixture_set", "cache_ttl")
    assert all(term not in runtime_source for term in forbidden)
    assert all(term not in profile_source for term in forbidden)


def test_provider_mapper_does_not_fabricate_missing_values() -> None:
    adapter = _load("src.app.wms_integration.adapters.query_inventory_operation_adapter")
    source = inspect.getsource(adapter.map_provider_query_inventory_response)

    assert '"UNKNOWN"' not in source
    assert 'or ""' not in source
    assert "float(" not in source


def test_generated_operation_index_has_no_runtime_discovery() -> None:
    generated = _load("src.app.runtime.system_capabilities.wms.generated_operation_index")
    source = inspect.getsource(generated)

    assert "rglob(" not in source
    assert "import_module(" not in source
    assert "__import__(" not in source


def test_every_authored_wms_effect_declares_status_query_capability() -> None:
    contracts = _load("src.app.runtime.system_capabilities.wms.contracts")
    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")

    effects = tuple(
        binding.operation
        for binding in catalog.WMS_PROVIDER_PROFILE.bindings
        if binding.operation.mode is contracts.WmsOperationMode.EFFECT
    )
    queries = tuple(
        binding.operation
        for binding in catalog.WMS_PROVIDER_PROFILE.bindings
        if binding.operation.mode is contracts.WmsOperationMode.QUERY
    )

    assert len(effects) == 3
    assert all(operation.supports_status_query is True for operation in effects)
    assert all(operation.supports_status_query is False for operation in queries)


def test_wms_effect_callback_is_optional_generic_hint_without_terminal_adapters() -> None:
    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")
    contracts = _load("src.app.runtime.system_capabilities.wms.contracts")

    profile_without_callback = contracts.WmsProviderProfile(
        identity=catalog.WMS_PROVIDER_PROFILE.identity,
        bindings=catalog.WMS_PROVIDER_PROFILE.bindings,
    )
    assert profile_without_callback.callbacks == ()
    assert tuple(callback.callback_type for callback in catalog.WMS_PROVIDER_PROFILE.callbacks) == (
        "WMS_EFFECT_STATUS_HINT",
    )

    capability_root = REPO_ROOT / "src/app/runtime/system_capabilities/wms"
    assert list(capability_root.rglob("callback_adapter.py")) == []
    for operation_path in (
        capability_root / "inventory/confirm_inbound",
        capability_root / "fulfillment/notify_pkg_binding",
        capability_root / "fulfillment/full_box_exchange",
    ):
        assert "CALLBACK_CONTRACT" not in (operation_path / "__init__.py").read_text(encoding="utf-8")
        assert "CALLBACK_CONTRACT" not in (operation_path / "contract.py").read_text(encoding="utf-8")


def test_wms_effect_hint_router_cannot_write_terminal_or_transport_state() -> None:
    router = _load("src.app.runtime.orchestration.services.inbox.wms_typed_effect_callback_router")
    source = inspect.getsource(router.WmsTypedEffectCallbackRouter)

    forbidden = (
        "finish_sent_external_by_dispatch_key",
        "EffectReducerEventType.CALLBACK_COMPLETED",
        "EffectReducerEventType.CALLBACK_REJECTED",
        "_callback_adapters",
        "callback_adapter",
    )
    assert all(term not in source for term in forbidden)


def test_single_deployment_builds_one_active_wms_provider_without_runtime_catalog() -> None:
    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")

    sandbox = catalog.build_active_wms_provider_profile(SimpleNamespace(APP_ENV="test"))
    production = catalog.build_active_wms_provider_profile(SimpleNamespace(APP_ENV="prod"))

    assert sandbox.identity.environment == "sandbox"
    assert production.identity.environment == "production"
    assert len(sandbox.bindings) == len(production.bindings) == 4
    assert {binding.profile for binding in sandbox.bindings} == {sandbox.identity}
    assert {binding.profile for binding in production.bindings} == {production.identity}
    assert not hasattr(catalog, "WMS_PROVIDER_PROFILES")
    assert not hasattr(catalog, "WMS_EXTERNAL_HTTP_EFFECT_PROFILES")

    source = inspect.getsource(catalog)
    assert "MappingProxyType" not in source
    assert "runtime_profile_environment" not in source


def test_endpoint_or_secret_rotation_does_not_change_active_provider_identity() -> None:
    catalog = _load("src.app.runtime.system_capabilities.wms.provider_catalog")
    first = SimpleNamespace(
        APP_ENV="test",
        WMS_SYNC_BASE_URL="https://wms-one.invalid/api",
        WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1="first-secret",
    )
    rotated = SimpleNamespace(
        APP_ENV="test",
        WMS_SYNC_BASE_URL="https://wms-two.invalid/api",
        WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1="rotated-secret",
    )

    assert (
        catalog.build_active_wms_provider_profile(first).identity
        == catalog.build_active_wms_provider_profile(rotated).identity
    )
