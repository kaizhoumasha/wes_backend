import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.conf import settings
from src.core.health import system_health
from src.register import register_health_route


@pytest.fixture(autouse=True)
def restore_system_health() -> None:
    previous = {
        "db_ok": system_health.db_ok,
        "redis_ok": system_health.redis_ok,
        "celery_ok": system_health.celery_ok,
        "last_check": system_health.last_check,
        "ttl": system_health.ttl,
    }
    try:
        yield
    finally:
        system_health.db_ok = previous["db_ok"]
        system_health.redis_ok = previous["redis_ok"]
        system_health.celery_ok = previous["celery_ok"]
        system_health.last_check = previous["last_check"]
        system_health.ttl = previous["ttl"]


def _build_client() -> TestClient:
    app = FastAPI()
    register_health_route(app)
    return TestClient(app, raise_server_exceptions=False)


def test_health_endpoint_returns_healthy_when_ready_and_fresh() -> None:
    system_health.db_ok = True
    system_health.redis_ok = True
    system_health.celery_ok = True
    system_health.last_check = time.time()

    response = _build_client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "ready": True,
        "stale": False,
        "components": {
            "database": True,
            "redis": True,
            "celery": True,
        },
        "version": settings.VERSION,
    }


def test_health_endpoint_returns_503_when_unhealthy_and_fresh() -> None:
    system_health.db_ok = False
    system_health.redis_ok = True
    system_health.celery_ok = False
    system_health.last_check = time.time()

    response = _build_client().get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "ready": False,
        "stale": False,
        "components": {
            "database": False,
            "redis": True,
            "celery": False,
        },
        "version": settings.VERSION,
    }


def test_health_endpoint_returns_200_when_status_cache_is_stale() -> None:
    system_health.db_ok = False
    system_health.redis_ok = False
    system_health.celery_ok = False
    system_health.last_check = time.time() - system_health.ttl - 1

    response = _build_client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "stale",
        "ready": False,
        "stale": True,
        "components": {
            "database": False,
            "redis": False,
            "celery": False,
        },
        "version": settings.VERSION,
    }
