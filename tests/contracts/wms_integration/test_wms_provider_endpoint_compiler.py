"""WMS Provider endpoint 编译与 typed path 渲染合同。"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
    WmsHttpMethod,
    WmsOperationMode,
)
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.ports.master_data_operations import GetMaterialRequest, GetRackRequest
from tests.contracts.wms_integration.provider_profile_support import build_provider_profile_payload


def _compile(payload: dict | None = None):
    from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    profile = WmsProviderProfileSettings.model_validate(payload or build_provider_profile_payload())
    return compile_wms_provider_profile(profile)


@pytest.mark.parametrize(
    "server_url",
    [
        "ftp://factory-wms.example",
        "http://user:secret@factory-wms.example",
        "http://factory-wms.example?tenant=a",
        "http://factory-wms.example#fragment",
        "http://factory-wms.example?",
        "http://factory-wms.example#",
        "http://factory-wms.example/api",
        "http://factory-wms.example:",
        "http://factory-wms.example:notaport",
        "http://[::1",
        "http://factory wms.example",
        "http://factory\\wms.example",
        "http://factory|wms.example",
        "http://factory<wms.example",
        "http://factory>wms.example",
        "http://factory^wms.example",
        "http://factory`wms.example",
        "http://factory_wms.example",
        "http://",
    ],
)
def test_compiler_rejects_server_url_that_is_not_a_bare_http_origin(server_url: str) -> None:
    payload = build_provider_profile_payload()
    payload["server_url"] = server_url

    with pytest.raises(ValueError, match="HTTP\\(S\\) origin"):
        _compile(payload)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/api/materials", "placeholder"),
        ("/api/materials/{material_code}/{unexpected}", "placeholder"),
        ("/api/materials/{material_code}/{material_code}", "repeated"),
        ("/api/materials/{material-code}", "illegal"),
        ("/api/materials/{material_code", "illegal"),
        ("/api/materials/prefix-{material_code}", "complete path segment"),
        ("/api/materials/{material_code} suffix", "safe relative path"),
        ("//evil.example/materials/{material_code}", "origin"),
        ("https://evil.example/materials/{material_code}", "relative path"),
        ("urn:factory:{material_code}", "relative path"),
        ("api/materials/{material_code}", "without query"),
        ("/api/../escape/{material_code}", "dot segment"),
        ("/api/materials/{material_code}?", "without query"),
        ("/api/materials/{material_code}#", "without query"),
    ],
)
def test_compiler_rejects_invalid_query_path_templates(path: str, message: str) -> None:
    payload = build_provider_profile_payload()
    payload["operations"]["wms.master_data.get_material@v1"]["path"] = path

    with pytest.raises(ValueError, match=message):
        _compile(payload)


def test_compiler_percent_encodes_each_typed_path_segment_without_mapping_formatting() -> None:
    compiled = _compile()
    endpoint = compiled.operations["wms.master_data.get_material@v1"]
    request = GetMaterialRequest(material_code="A/B ?#%")

    assert endpoint.render_endpoint(request) == (
        "http://factory-wms.example:8080/api/wms/master-data/materials/A%2FB%20%3F%23%25"
    )
    with pytest.raises(TypeError, match="typed request"):
        endpoint.render_endpoint(GetRackRequest(rack_id="RACK-1"))


@pytest.mark.parametrize(
    "material_code",
    [".", "..", "%2e", "%2E%2E", "%252e%252e", "%2f..%2fadmin", "%252f..%252fadmin", "safe%2f.."],
)
def test_compiler_rejects_typed_dot_segments_before_http_url_normalization(material_code: str) -> None:
    endpoint = _compile().operations["wms.master_data.get_material@v1"]

    with pytest.raises(ValueError, match="dot segment"):
        endpoint.render_endpoint(GetMaterialRequest(material_code=material_code))


def test_rendered_endpoint_preserves_the_compiled_origin_invariant() -> None:
    endpoint = _compile().operations["wms.master_data.get_material@v1"]
    rendered = endpoint.render_endpoint(GetMaterialRequest(material_code="MAT/合法"))

    assert rendered.startswith("http://factory-wms.example:8080/")
    assert rendered == "http://factory-wms.example:8080/api/wms/master-data/materials/MAT%2F%E5%90%88%E6%B3%95"


def test_compiler_derives_all_endpoint_semantics_from_static_registry() -> None:
    compiled = _compile()

    assert tuple(compiled.operations) == tuple(operation.identity for operation in WMS_OPERATIONS)
    assert len(compiled.operations) == 35
    query_bindings = tuple(
        endpoint for endpoint in compiled.operations.values() if endpoint.mode is WmsOperationMode.QUERY
    )
    effect_bindings = tuple(
        endpoint for endpoint in compiled.operations.values() if endpoint.mode is WmsOperationMode.EFFECT
    )
    assert len(query_bindings) == 19
    assert sum(endpoint.http_method is WmsHttpMethod.GET for endpoint in query_bindings) == 18
    assert sum(endpoint.http_method is WmsHttpMethod.POST for endpoint in query_bindings) == 1
    assert len(effect_bindings) == 16

    synchronous = tuple(
        endpoint for endpoint in effect_bindings if endpoint.completion_mode is WmsCompletionMode.SYNC_RESULT
    )
    asynchronous = tuple(
        endpoint for endpoint in effect_bindings if endpoint.completion_mode is WmsCompletionMode.ASYNC_TASK
    )
    assert len(synchronous) == 9
    assert all(endpoint.status_endpoint is None for endpoint in synchronous)
    assert len(asynchronous) == 7
    assert {endpoint.status_endpoint for endpoint in asynchronous} == {
        "http://factory-wms.example:8080/api/wms/operations/status"
    }

    for static_operation in WMS_OPERATIONS:
        endpoint = compiled.operations[static_operation.identity]
        assert endpoint.http_method is static_operation.http_method
        assert endpoint.mode is static_operation.mode
        assert endpoint.completion_mode is static_operation.completion_mode
        assert endpoint.execution_lane is static_operation.execution_lane
        assert endpoint.budget is static_operation.budget
        assert endpoint.pagination is static_operation.pagination
        assert endpoint.result_model is static_operation.result_model


def test_profile_cannot_override_static_operation_semantics() -> None:
    payload = deepcopy(build_provider_profile_payload())
    payload["operations"]["wms.master_data.get_material@v1"]["http_method"] = "POST"

    with pytest.raises(ValidationError, match="http_method"):
        _compile(payload)


def test_async_status_path_is_required_and_must_be_a_safe_relative_path() -> None:
    missing = build_provider_profile_payload()
    missing.pop("effect_status_path")
    with pytest.raises(ValidationError, match="effect_status_path"):
        _compile(missing)

    escaped = build_provider_profile_payload()
    escaped["effect_status_path"] = "//evil.example/status"
    with pytest.raises(ValueError, match="origin"):
        _compile(escaped)


def test_status_entry_point_fails_closed_without_compiled_endpoint_injection() -> None:
    from src.app.wms_integration.ports.effect_status import build_wms_effect_status_binding
    from src.core.conf import settings

    with pytest.raises(RuntimeError, match="compiled WMS EFFECT profile"):
        build_wms_effect_status_binding(settings_source=settings)
