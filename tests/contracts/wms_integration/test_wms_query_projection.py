"""共享 WMS QUERY request projection 合同。"""

from __future__ import annotations

import importlib
from dataclasses import replace

import pytest

from src.app.wms_integration.operation_contract import WmsHttpMethod, WmsOperationMode
from src.app.wms_integration.ports.master_data_operations import (
    GET_MATERIAL,
    LIST_MATERIALS,
    GetMaterialRequest,
    ListMaterialsRequest,
)
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile

COMPILED_PROFILE = build_compiled_provider_profile()


def _project(operation: object, request: object):
    module = importlib.import_module("src.app.wms_integration.query_projection")
    return module.project_wms_query_request(
        operation=operation,
        endpoint=COMPILED_PROFILE.operations[operation.identity],
        request=request,
    )


def test_get_projection_percent_encodes_path_and_repeats_tuple_query_values() -> None:
    material_request = GetMaterialRequest(material_code="MAT /A?%")
    list_request = ListMaterialsRequest(
        page_size=50,
        material_codes=("MAT 1", "MAT/2"),
        batch_managed=True,
    )

    path_projection = _project(GET_MATERIAL, material_request)
    list_projection = _project(LIST_MATERIALS, list_request)

    assert path_projection.operation_identity == GET_MATERIAL.identity
    assert path_projection.url.endswith("/master-data/materials/MAT%20%2FA%3F%25")
    assert path_projection.query_params == ()
    assert path_projection.json_body is None
    assert list_projection.query_params == (
        ("page_size", "50"),
        ("material_codes", "MAT 1"),
        ("material_codes", "MAT/2"),
        ("batch_managed", "true"),
    )
    assert list_projection.json_body is None


def test_projection_rejects_identity_and_typed_request_mismatch() -> None:
    request = GetMaterialRequest(material_code="MAT-001")
    module = importlib.import_module("src.app.wms_integration.query_projection")

    with pytest.raises(ValueError, match="identity"):
        module.project_wms_query_request(
            operation=GET_MATERIAL,
            endpoint=COMPILED_PROFILE.operations[LIST_MATERIALS.identity],
            request=request,
        )
    with pytest.raises(TypeError, match="typed request"):
        module.project_wms_query_request(
            operation=GET_MATERIAL,
            endpoint=COMPILED_PROFILE.operations[GET_MATERIAL.identity],
            request=ListMaterialsRequest(),
        )


def test_projection_rejects_non_query_and_compiled_semantic_drift() -> None:
    endpoint = COMPILED_PROFILE.operations[GET_MATERIAL.identity]
    request = GetMaterialRequest(material_code="MAT-001")
    module = importlib.import_module("src.app.wms_integration.query_projection")

    with pytest.raises(ValueError, match="QUERY operation semantics"):
        module.project_wms_query_request(
            operation=GET_MATERIAL.model_copy(update={"mode": WmsOperationMode.EFFECT}),
            endpoint=endpoint,
            request=request,
        )
    with pytest.raises(ValueError, match="semantics differ"):
        module.project_wms_query_request(
            operation=GET_MATERIAL,
            endpoint=replace(endpoint, http_method=WmsHttpMethod.POST),
            request=request,
        )


def test_projection_rejects_non_scalar_query_values() -> None:
    module = importlib.import_module("src.app.wms_integration.query_projection")

    with pytest.raises(TypeError, match="typed scalar"):
        module._query_scalar({"not": "scalar"})
