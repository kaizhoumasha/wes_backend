"""可靠事实查询的身份、权限与 HTTP 状态合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.wms_diagnostics.v1.execution import get_observation_service, router
from src.core.error_handlers import register_exception_handlers
from src.core.uuid7 import new_uuid7


def app_and_service():
    app = FastAPI()
    register_exception_handlers(app)
    service = SimpleNamespace(get_confirmation=AsyncMock(return_value=None), get_evidence=AsyncMock(return_value=None))
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_observation_service] = lambda: service
    for route in router.routes:
        for dependency in route.dependencies:
            assert dependency.dependency.permission_required == (
                "ops:wms-confirmation:read" if route.path.endswith("/confirmations") else "ops:wms-evidence:read"
            )
            app.dependency_overrides[dependency.dependency] = lambda: None
    return app, service


@pytest.mark.parametrize("path,method", [("confirmations", "get_confirmation"), ("evidences", "get_evidence")])
def test_exact_identity_missing_unavailable_and_validation(path, method):
    app, service = app_and_service()
    query = {"operation": "sample.action@v1", "operation_id": new_uuid7()}
    with TestClient(app) as client:
        url = f"/api/v1/wms-diagnostics/{path}"
        missing = client.get(url, params=query)
        assert missing.status_code == 404
        assert missing.json()["code"] == "3000"
        getattr(service, method).assert_awaited_once_with(query["operation"], query["operation_id"])
        getattr(service, method).side_effect = ConnectionError("unavailable")
        unavailable = client.get(url, params=query)
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "5030"
        getattr(service, method).reset_mock()
        assert client.get(url, params=query | {"operation_id": "invalid"}).status_code == 422
        assert client.get(url, params=query | {"operation": " "}).status_code == 422
        getattr(service, method).assert_not_awaited()


def test_persisted_evidence_is_returned_without_diagnostic_cache():
    app, service = app_and_service()
    identity = new_uuid7()
    service.get_evidence.return_value = {
        "operation": "sample.action@v1",
        "operation_id": identity,
        "apply_status": "RECONCILING",
        "received_at": "2026-09-09T12:00:00+00:00",
        "processed_at": None,
        "published_at": None,
        "decision_attempt_count": 1,
        "decision_next_attempt_at": None,
    }
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/wms-diagnostics/evidences", params={"operation": "sample.action@v1", "operation_id": identity}
        )
    assert response.status_code == 200
    assert response.json()["data"]["apply_status"] == "RECONCILING"
    assert "can_retry" not in response.json()["data"]


def test_application_permission_catalog_remains_unambiguous():
    from src.register import create_app
    from src.utils.permission_scanner import build_permission_catalog

    catalog = build_permission_catalog(create_app())
    assert len({row["name"] for row in catalog}) == len(catalog)
    expected = {
        "ops:transport-callback-receipt:read": "/api/v1/transport/callback-receipts",
        "ops:wms-confirmation:read": "/api/v1/wms-diagnostics/confirmations",
        "ops:wms-evidence:read": "/api/v1/wms-diagnostics/evidences",
    }
    assert {row["name"]: (row["method"], row["path"]) for row in catalog if row["name"] in expected} == {
        permission: ("GET", path) for permission, path in expected.items()
    }
