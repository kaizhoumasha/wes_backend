"""北向 WMS typed operation 架构边界。"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path

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
