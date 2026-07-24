"""production 启动必须执行 inventory QUERY cutover readiness gate。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.system_capabilities.query_inventory_cutover import (
    query_inventory_cutover_readiness_service,
)
from src.app.runtime.system_capabilities.shadow_readiness import ReadinessGateError
from src.app.runtime.system_capabilities.wms import provider_catalog
from src.core.conf import settings
from src.register import register_init


@pytest.mark.asyncio
async def test_production_startup_fails_before_redis_when_cutover_gate_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.database import db as database
    from src.database import redis_client

    init_db = AsyncMock()
    close_db = AsyncMock()
    init_redis = AsyncMock()
    close_redis = AsyncMock()
    monkeypatch.setattr(database, "init_db", init_db)
    monkeypatch.setattr(database, "close_db", close_db)
    monkeypatch.setattr(redis_client, "init_redis", init_redis)
    monkeypatch.setattr(redis_client, "close_redis", close_redis)
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(provider_catalog, "validate_wms_transport_configuration", lambda *, app_env: None)

    @asynccontextmanager
    async def db_context():
        yield object()

    monkeypatch.setattr(database, "get_db_context", db_context)
    gate = AsyncMock(side_effect=ReadinessGateError("missing READY+GO"))
    monkeypatch.setattr(query_inventory_cutover_readiness_service, "require_ready", gate)

    with pytest.raises(ReadinessGateError, match=r"missing READY\+GO"):
        async with register_init(object()):
            pytest.fail("被拒绝的 production startup 不得进入 serving 状态")

    gate.assert_awaited_once()
    init_redis.assert_not_awaited()
    close_redis.assert_awaited_once()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_rejects_invalid_wms_transport_before_database_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.database import db as database
    from src.database import redis_client

    init_db = AsyncMock()
    close_db = AsyncMock()
    close_redis = AsyncMock()
    monkeypatch.setattr(database, "init_db", init_db)
    monkeypatch.setattr(database, "close_db", close_db)
    monkeypatch.setattr(redis_client, "close_redis", close_redis)
    monkeypatch.setattr(settings, "APP_ENV", "prod")

    def reject_wms_transport(*, app_env: str) -> None:
        assert app_env == "prod"
        raise ValueError("WMS production transport configuration is invalid")

    monkeypatch.setattr(provider_catalog, "validate_wms_transport_configuration", reject_wms_transport)

    with pytest.raises(ValueError, match="WMS production transport"):
        async with register_init(object()):
            pytest.fail("无效 WMS transport 配置不得进入 serving 状态")

    init_db.assert_not_awaited()
    close_db.assert_awaited_once()
    close_redis.assert_awaited_once()
