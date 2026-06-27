from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from src.core.error_handlers import register_exception_handlers


def create_test_client() -> TestClient:
    app = FastAPI(debug=False)
    register_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


def test_dbapi_programming_error_is_not_reported_as_connection_failure() -> None:
    app = FastAPI(debug=False)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise DBAPIError(
            statement="select 1",
            params={},
            orig=TypeError("invalid input for query argument"),
            connection_invalidated=False,
        )

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "5010"
    assert "数据库错误" in payload["message"]
