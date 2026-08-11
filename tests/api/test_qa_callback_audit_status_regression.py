"""QA Callback 审计响应码回归测试。"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.callback.v1 import callback as callback_module
from src.app.sys.models.audit_log import OperaStatus
from src.core.conf import settings
from src.database.db import get_db
from src.database.redis_cache import get_cache
from tests.api import callback_test_support

CallbackHTTPClient = tuple[TestClient, AsyncMock, AsyncMock]


@pytest.fixture
def callback_http_client(monkeypatch: pytest.MonkeyPatch) -> Generator[CallbackHTTPClient]:
    monkeypatch.setattr(settings, "APP_DEBUG", True)
    monkeypatch.setattr(settings, "SKIP_API_AUTH", True)
    app = FastAPI()
    app.include_router(callback_module.router, prefix="/api/v1/callback")
    db_session = callback_test_support.db_session.__wrapped__()

    async def _db_override():
        yield db_session

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_cache] = lambda: object()
    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ) as callback_log,
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        patch(
            "src.app.callback.services.callback_ingress_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
        TestClient(app) as client,
    ):
        yield client, callback_log, audit_log


def _assert_logged_http_status(
    callback_log: AsyncMock,
    audit_log: AsyncMock,
    *,
    expected_status: int,
) -> None:
    callback_kwargs = callback_test_support._await_kwargs(callback_log)
    assert callback_kwargs["response_status"] == expected_status
    audit_kwargs = callback_test_support._await_kwargs(audit_log)
    assert audit_kwargs["status"] == OperaStatus.FAIL
    assert audit_kwargs["code"] == str(expected_status)


def test_external_validation_audit_records_actual_http_status(callback_http_client: CallbackHTTPClient) -> None:
    # Regression: ISSUE-002 — Callback 失败审计不得把所有 4xx 固定记录为 500
    # Found by /qa on 2026-08-11
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-8011-2026-08-11.md
    client, callback_log, audit_log = callback_http_client

    response = client.post("/api/v1/callback/external", json={})

    assert response.status_code == 400
    _assert_logged_http_status(callback_log, audit_log, expected_status=400)


def test_result_validation_audit_records_actual_http_status(callback_http_client: CallbackHTTPClient) -> None:
    client, callback_log, audit_log = callback_http_client

    response = client.post("/api/v1/callback/result", json={})

    assert response.status_code == 200
    _assert_logged_http_status(callback_log, audit_log, expected_status=200)


def test_event_validation_audit_records_actual_http_status(callback_http_client: CallbackHTTPClient) -> None:
    client, callback_log, audit_log = callback_http_client

    response = client.post("/api/v1/callback/event", json={})

    assert response.status_code == 200
    _assert_logged_http_status(callback_log, audit_log, expected_status=200)


def test_result_command_not_found_audit_records_actual_http_status(callback_http_client: CallbackHTTPClient) -> None:
    client, callback_log, audit_log = callback_http_client

    with patch(
        "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
        new=AsyncMock(return_value=None),
    ):
        response = client.post("/api/v1/callback/result", json=callback_test_support.create_result_payload())

    assert response.status_code == 200
    _assert_logged_http_status(callback_log, audit_log, expected_status=200)


def test_wms_event_validation_audit_records_actual_http_status(callback_http_client: CallbackHTTPClient) -> None:
    client, callback_log, audit_log = callback_http_client
    payload = {
        "source_system": "WMS",
        "event_type": "WMS_GRN_RECEIVED",
        "source_event_id": "event-audit-status",
        "occurred_at": "2026-07-30T08:00:00Z",
        "data": {},
    }

    response = client.post("/api/v1/callback/event", json=payload)

    assert response.status_code == 400
    _assert_logged_http_status(callback_log, audit_log, expected_status=400)
