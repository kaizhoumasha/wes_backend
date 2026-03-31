import pytest

from src.app.admin.v1 import performance


@pytest.mark.asyncio()
async def test_health_check_reports_healthy_when_database_and_redis_are_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_db_health(_db: object) -> dict[str, object]:
        return {
            "status": "healthy",
            "response_time_ms": 1.2,
        }

    async def _fake_redis_health() -> dict[str, object]:
        return {
            "status": "healthy",
            "connection_status": "connected",
            "response_time_ms": 0.4,
        }

    monkeypatch.setattr(performance, "check_database_health", _fake_db_health)
    monkeypatch.setattr(performance, "check_redis_health", _fake_redis_health)

    result = await performance.health_check(db=object())

    assert result["status"] == "healthy"
    assert result["components"] == {
        "database": {
            "status": "healthy",
            "response_time_ms": 1.2,
        },
        "redis": {
            "status": "healthy",
            "connection_status": "connected",
            "response_time_ms": 0.4,
        },
    }
