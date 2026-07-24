"""应用启动必须在初始化数据库前校验 WMS transport。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.runtime.system_capabilities.wms import provider_catalog
from src.core.conf import settings
from src.register import register_init


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

    def reject_wms_transport(*, settings_source: object) -> None:
        assert settings_source is settings
        assert settings_source.APP_ENV == "prod"
        raise ValueError("WMS production transport configuration is invalid")

    monkeypatch.setattr(provider_catalog, "validate_wms_transport_configuration", reject_wms_transport)

    with pytest.raises(ValueError, match="WMS production transport"):
        async with register_init(object()):
            pytest.fail("无效 WMS transport 配置不得进入 serving 状态")

    init_db.assert_not_awaited()
    close_db.assert_awaited_once()
    close_redis.assert_awaited_once()
