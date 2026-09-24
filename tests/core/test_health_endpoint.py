from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.conf import settings
from src.database.db import get_db
from src.register import register_health_route


def _build_client() -> TestClient:
    app = FastAPI()
    register_health_route(app)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


def test_health_endpoint_returns_basic_liveness_payload() -> None:
    response = _build_client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


def test_ready_checks_this_api_instance_on_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = AsyncMock(side_effect=[{"status": "healthy"}, {"status": "unhealthy"}])
    monkeypatch.setattr("src.utils.health.check_database_health", probe)
    client = _build_client()

    first = client.get("/ready")
    second = client.get("/ready")

    assert first.status_code == 200
    assert second.status_code == 503
    assert first.json()["status"] == "ready"
    assert second.json()["status"] == "not_ready"
    for response in (first, second):
        assert set(response.json()) == {"status", "observed_at", "valid_for_seconds"}
        assert datetime.fromisoformat(response.json()["observed_at"]).tzinfo is not None
        assert response.json()["valid_for_seconds"] == 0
        assert response.headers["cache-control"] == "no-store"
    assert probe.await_count == 2


def test_ready_treats_unknown_database_fact_as_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.health.check_database_health", AsyncMock(return_value={"status": "unknown"}))

    response = _build_client().get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
