"""应用启动必须在初始化数据库前校验 WMS transport。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from celery.exceptions import WorkerTerminate

from src import register as register_module
from src.app.runtime.system_capabilities.wms import provider_catalog
from src.celery_app import app as celery_app_module
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


def _production_simulation_settings() -> SimpleNamespace:
    return SimpleNamespace(
        APP_ENV="prod",
        PROJECT_NAME="wes-test",
        APP_HOST="127.0.0.1",
        APP_PORT=8001,
        DOCS_URL="/docs",
        WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=True,
        WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v2",
        WMS_SYNC_BASE_URL="https://wms.example/api",
        WMS_EFFECT_STATUS_URL="https://wms.example/api/status",
        WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1="old-secret",
        WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2="active-secret",
        WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS=100,
        WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2,
        WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=80,
        WES_EFFECT_NOT_FOUND_GRACE_SECONDS=3,
        WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS=20,
        WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES="",
    )


@pytest.mark.asyncio
async def test_fastapi_startup_rejects_production_in_process_simulation_before_database_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.database import db as database
    from src.database import redis_client

    monkeypatch.setattr(register_module, "settings", _production_simulation_settings())
    init_db = AsyncMock()
    monkeypatch.setattr(database, "init_db", init_db)
    monkeypatch.setattr(database, "close_db", AsyncMock())
    monkeypatch.setattr(redis_client, "init_redis", AsyncMock())
    monkeypatch.setattr(redis_client, "close_redis", AsyncMock())

    with pytest.raises(ValueError, match=r"production.*simulation"):
        async with register_init(object()):
            pytest.fail("production simulator 配置不得进入 serving 状态")

    init_db.assert_not_awaited()


def test_celery_startup_rejects_production_in_process_simulation_before_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(celery_app_module, "settings", _production_simulation_settings())
    setup_logger = MagicMock()
    monkeypatch.setattr(celery_app_module, "setup_logger", setup_logger)

    with pytest.raises(WorkerTerminate, match="WMS transport configuration rejected"):
        celery_app_module.on_worker_init()

    setup_logger.assert_not_called()
