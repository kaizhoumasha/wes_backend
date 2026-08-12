"""Transport 生产 FastAPI lifespan 的真实 PostgreSQL/Redis owner。"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from src import register as register_module
from src.core.conf import settings
from src.register import register_init
from tests.contracts.wms_integration.provider_profile_support import write_provider_profile

pytestmark = pytest.mark.integration


def _integration_settings(profile_file) -> object:
    database_url = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    redis_url = urlparse(os.environ["INTEGRATION_REDIS_URL"])
    return settings.model_copy(
        update={
            "APP_ENV": "test",
            "WMS_PROVIDER_PROFILE_FILE": profile_file,
            "POSTGRES_HOST": database_url.host or "127.0.0.1",
            "POSTGRES_PORT": database_url.port or 5432,
            "POSTGRES_USER": database_url.username or "postgres",
            "POSTGRES_PASSWORD": database_url.password or "",
            "POSTGRES_DB": database_url.database or "test",
            "DATABASE_RUNTIME_ROLE": "api",
            "DATABASE_APPLICATION_NAME": "it-transport-lifespan",
            "DATABASE_APPLICATION_RUN_ID": uuid.uuid4().hex[:12],
            "REDIS_HOST": redis_url.hostname or "127.0.0.1",
            "REDIS_PORT": redis_url.port or 6379,
            "REDIS_PASSWORD": redis_url.password or "",
            "REDIS_DB": int(redis_url.path.lstrip("/") or "0"),
        }
    )


@pytest.mark.asyncio
async def test_register_init_owns_real_transport_database_and_redis_lifecycle(
    tmp_path,
    integration_guard: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.database import db as database
    from src.database import redis_client

    profile_file = write_provider_profile(tmp_path / "provider.yaml")
    integration_settings = _integration_settings(profile_file)
    monkeypatch.setattr(register_module, "settings", integration_settings)
    monkeypatch.setattr(database, "settings", integration_settings)
    monkeypatch.setattr(redis_client, "settings", integration_settings)

    assert database.engine is None
    assert database.AsyncSessionLocal is None
    assert redis_client.redis_manager.redis_client is None
    app = FastAPI()
    runtime = None

    async with register_init(app):
        runtime = app.state.transport_runtime
        assert runtime is not None and runtime.closed is False
        assert app.state.wms_inbound_auth_policy.allows_unsigned_wms_callbacks is True
        assert database.AsyncSessionLocal is not None
        async with database.AsyncSessionLocal() as session:
            assert await session.scalar(text("SELECT 1")) == 1
        assert redis_client.redis_manager.redis_client is not None
        assert await redis_client.redis_manager.redis_client.ping() is True

    assert runtime is not None and runtime.closed is True
    assert app.state.transport_runtime is None
    assert app.state.wms_inbound_auth_policy is None
    assert database.engine is None
    assert database.AsyncSessionLocal is None
    assert redis_client.redis_manager.redis_client is None
