"""External callback 审计响应码回归测试。"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.callback.v1 import callback as callback_module
from src.app.sys.models.audit_log import OperaStatus
from src.core.conf import settings
from src.database.db import get_db
from src.database.dependencies import _get_cache_service
from tests.api import callback_test_support


@pytest.fixture
def callback_http_client(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, AsyncMock, AsyncMock]]:
    monkeypatch.setattr(settings, "APP_DEBUG", True)
    monkeypatch.setattr(settings, "SKIP_API_AUTH", True)
    app = FastAPI()
    app.include_router(callback_module.router, prefix="/api/v1/callback")
    db = callback_test_support.db_session.__wrapped__()

    async def _db_override():
        yield db

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[_get_cache_service] = lambda: object()
    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ) as callback_log,
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        TestClient(app) as client,
    ):
        yield client, callback_log, audit_log


def test_external_validation_audit_records_actual_http_status(
    callback_http_client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    client, callback_log, audit_log = callback_http_client

    response = client.post("/api/v1/callback/external", json={})

    assert response.status_code == 400
    assert callback_log.await_args.kwargs["response_status"] == 400
    assert audit_log.await_args.kwargs["status"] == OperaStatus.FAIL
    assert audit_log.await_args.kwargs["code"] == "400"
