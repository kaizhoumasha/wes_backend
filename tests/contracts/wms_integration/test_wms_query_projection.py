"""19 项 WMS QUERY 的 registry 驱动 request projection 合同。"""

from __future__ import annotations

import importlib
import json
from copy import deepcopy

import pytest

from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

Q19 = "wms.document.validate_rough_sorter_admission@v1"
COMPILED_PROFILE = build_compiled_provider_profile()


def _project(operation, request):
    module = importlib.import_module("src.app.wms_integration.query_projection")
    assert hasattr(module, "project_wms_query_request"), "缺少统一 QUERY request projector"
    return module.project_wms_query_request(
        operation=operation,
        endpoint=COMPILED_PROFILE.operations[operation.identity],
        request=request,
    )


@pytest.mark.parametrize("operation", QUERY_OPERATIONS, ids=lambda operation: operation.identity)
def test_all_19_query_projections_are_driven_by_static_definition(operation) -> None:
    request = operation.request_model.model_validate_json(json.dumps(REQUEST_FIXTURES[operation.identity]))

    projection = _project(operation, request)

    assert len(QUERY_OPERATIONS) == 19
    assert projection.operation_identity == operation.identity
    assert projection.method == operation.http_method.value
    assert projection.url == COMPILED_PROFILE.operations[operation.identity].render_endpoint(request)
    if operation.identity == Q19:
        assert projection.query_params == ()
        assert projection.json_body == request.model_dump(mode="json", exclude_none=True)
    else:
        assert projection.json_body is None
        assert all(name not in dict(projection.query_params) for name in projection.path_field_names)


def test_get_projection_percent_encodes_path_and_repeats_tuple_query_values() -> None:
    get_material = QUERY_OPERATIONS[0]
    material_request = get_material.request_model.model_validate({"material_code": "MAT /A?%"})
    list_materials = QUERY_OPERATIONS[1]
    list_request = list_materials.request_model.model_validate(
        {
            "page_size": 50,
            "material_codes": ("MAT 1", "MAT/2"),
            "batch_managed": True,
        }
    )

    path_projection = _project(get_material, material_request)
    list_projection = _project(list_materials, list_request)

    assert path_projection.url.endswith("/master-data/materials/MAT%20%2FA%3F%25")
    assert path_projection.query_params == ()
    assert list_projection.query_params == (
        ("page_size", "50"),
        ("material_codes", "MAT 1"),
        ("material_codes", "MAT/2"),
        ("batch_managed", "true"),
    )


def test_q19_projection_keeps_sensitive_values_only_in_post_body() -> None:
    operation = QUERY_OPERATIONS[-1]
    payload = deepcopy(REQUEST_FIXTURES[Q19])
    payload["raw_code"] = "RAW-SECRET-654321"
    payload["six_in_one"] = {
        "HHPN": "HHPN-SECRET",
        "MfrPN": "MFR-SECRET",
        "Qty": "654321",
        "DateCode": "2629",
        "LotCode": "LOT-SECRET",
        "PkgID": "PKG-SECRET",
    }
    request = operation.request_model.model_validate_json(json.dumps(payload))

    projection = _project(operation, request)
    serialized_evidence = json.dumps(projection.evidence_snapshot, ensure_ascii=False, sort_keys=True)

    assert projection.method == "POST"
    assert projection.query_params == ()
    assert "RAW-SECRET" not in projection.url
    assert "654321" not in projection.url
    for secret in (
        "RAW-SECRET-654321",
        "HHPN-SECRET",
        "MFR-SECRET",
        "654321",
        "LOT-SECRET",
        "PKG-SECRET",
    ):
        assert secret not in serialized_evidence
    assert projection.evidence_snapshot == {
        "station_code": "ROUGH-IN",
        "workline_id": 1,
        "session_id": 1,
        "correlation_id": "CORR-001",
        "raw_code": {"present": True, "length": len("RAW-SECRET-654321")},
        "six_in_one": {
            "HHPN": {"present": True, "length": len("HHPN-SECRET")},
            "MfrPN": {"present": True, "length": len("MFR-SECRET")},
            "Qty": {"present": True, "length": len("654321")},
            "DateCode": {"present": True, "length": len("2629")},
            "LotCode": {"present": True, "length": len("LOT-SECRET")},
            "PkgID": {"present": True, "length": len("PKG-SECRET")},
        },
        "measurement": {
            "reel_diameter_mm_present": True,
            "reel_thickness_mm_present": True,
        },
        "request_canonical_hash": projection.request_canonical_hash,
    }


def test_projection_rejects_identity_and_typed_request_mismatch() -> None:
    operation = QUERY_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    other_endpoint = COMPILED_PROFILE.operations[QUERY_OPERATIONS[1].identity]
    module = importlib.import_module("src.app.wms_integration.query_projection")
    assert hasattr(module, "project_wms_query_request"), "缺少统一 QUERY request projector"

    with pytest.raises(ValueError, match="identity"):
        module.project_wms_query_request(
            operation=operation,
            endpoint=other_endpoint,
            request=request,
        )
    with pytest.raises(TypeError, match="typed request"):
        module.project_wms_query_request(
            operation=operation,
            endpoint=COMPILED_PROFILE.operations[operation.identity],
            request=QUERY_OPERATIONS[1].request_model(),
        )
