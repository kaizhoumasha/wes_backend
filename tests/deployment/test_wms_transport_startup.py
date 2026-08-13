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


@pytest.fixture(autouse=True)
def _device_command_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECS_BASE_URL", "http://ecs")
    monkeypatch.setenv("ECS_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ECS_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DEVICE_COMMAND_QUEUE", "device-command")


from src.register import register_init
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile


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
        async with register_init(SimpleNamespace(state=SimpleNamespace())):
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
        async with register_init(SimpleNamespace(state=SimpleNamespace())):
            pytest.fail("production simulator 配置不得进入 serving 状态")

    init_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_fastapi_startup_binds_effect_preparation_runtime_from_validated_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration import observability
    from src.app.transport import composition as transport_composition
    from src.app.wms_integration import effect_preparation_runtime, query_runtime
    from src.database import db as database
    from src.database import redis_client

    startup = SimpleNamespace(catalog=object(), compiled_profile=build_compiled_provider_profile())
    data_runtime = object()
    preparation_runtime = object()
    build_preparation = MagicMock(return_value=preparation_runtime)
    bind_preparation = MagicMock()
    close_preparation = AsyncMock()
    transport_runtime = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        provider_catalog,
        "validate_wms_transport_configuration",
        MagicMock(return_value=startup),
    )
    monkeypatch.setattr(database, "init_db", AsyncMock())
    monkeypatch.setattr(database, "close_db", AsyncMock())
    monkeypatch.setattr(database, "AsyncSessionLocal", MagicMock())
    monkeypatch.setattr(
        transport_composition,
        "build_transport_runtime",
        AsyncMock(return_value=transport_runtime),
    )
    monkeypatch.setattr(redis_client, "init_redis", AsyncMock())
    monkeypatch.setattr(redis_client, "close_redis", AsyncMock())
    monkeypatch.setattr(query_runtime, "build_wms_data_lane_query_runtime", MagicMock(return_value=data_runtime))
    monkeypatch.setattr(query_runtime, "bind_wms_data_lane_query_runtime", MagicMock())
    monkeypatch.setattr(query_runtime, "close_bound_wms_data_lane_query_runtime", AsyncMock())
    monkeypatch.setattr(effect_preparation_runtime, "build_wms_effect_preparation_runtime", build_preparation)
    monkeypatch.setattr(effect_preparation_runtime, "bind_wms_effect_preparation_runtime", bind_preparation)
    monkeypatch.setattr(effect_preparation_runtime, "close_wms_effect_preparation_runtime", close_preparation)
    monkeypatch.setattr(observability, "configure_runtime_open_telemetry_backend", MagicMock(return_value=False))
    monkeypatch.setattr(observability.runtime_observability_registry, "close", MagicMock())

    app = SimpleNamespace(state=SimpleNamespace())
    async with register_init(app):
        assert app.state.wms_effect_preparation_runtime is preparation_runtime

    build_preparation.assert_called_once_with(
        catalog=startup.catalog,
        admission_enabled=settings.WMS_EFFECT_ADMISSION_ENABLED,
    )
    bind_preparation.assert_called_once_with(preparation_runtime)
    close_preparation.assert_awaited_once_with(preparation_runtime)
    transport_runtime.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_fastapi_preparation_bind_failure_does_not_close_existing_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration import observability
    from src.app.transport import composition as transport_composition
    from src.app.wms_integration import effect_preparation_runtime, query_runtime
    from src.database import db as database
    from src.database import redis_client

    candidate = object()
    unbind_candidate = AsyncMock()
    transport_runtime = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        provider_catalog,
        "validate_wms_transport_configuration",
        MagicMock(
            return_value=SimpleNamespace(
                catalog=object(),
                compiled_profile=build_compiled_provider_profile(),
            )
        ),
    )
    monkeypatch.setattr(database, "init_db", AsyncMock())
    monkeypatch.setattr(database, "close_db", AsyncMock())
    monkeypatch.setattr(database, "AsyncSessionLocal", MagicMock())
    monkeypatch.setattr(
        transport_composition,
        "build_transport_runtime",
        AsyncMock(return_value=transport_runtime),
    )
    monkeypatch.setattr(redis_client, "init_redis", AsyncMock())
    monkeypatch.setattr(redis_client, "close_redis", AsyncMock())
    monkeypatch.setattr(query_runtime, "build_wms_data_lane_query_runtime", MagicMock(return_value=object()))
    monkeypatch.setattr(query_runtime, "bind_wms_data_lane_query_runtime", MagicMock())
    monkeypatch.setattr(query_runtime, "close_bound_wms_data_lane_query_runtime", AsyncMock())
    monkeypatch.setattr(
        effect_preparation_runtime, "build_wms_effect_preparation_runtime", MagicMock(return_value=candidate)
    )
    monkeypatch.setattr(
        effect_preparation_runtime,
        "bind_wms_effect_preparation_runtime",
        MagicMock(side_effect=RuntimeError("already bound")),
    )
    monkeypatch.setattr(effect_preparation_runtime, "close_wms_effect_preparation_runtime", unbind_candidate)
    monkeypatch.setattr(observability.runtime_observability_registry, "close", MagicMock())

    with pytest.raises(RuntimeError, match="already bound"):
        async with register_init(SimpleNamespace(state=SimpleNamespace())):
            pytest.fail("绑定失败不得进入 serving 状态")

    unbind_candidate.assert_not_awaited()
    transport_runtime.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("body_fails", [False, True])
async def test_fastapi_cleanup_contains_each_failure_and_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
    body_fails: bool,
) -> None:
    from src.app.runtime.orchestration import observability
    from src.app.transport import composition as transport_composition
    from src.app.wms_integration import effect_preparation_runtime, query_runtime
    from src.database import db as database
    from src.database import redis_client

    startup = SimpleNamespace(catalog=object(), compiled_profile=build_compiled_provider_profile())
    transport_runtime = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("transport cleanup failed")))
    close_data = AsyncMock(side_effect=RuntimeError("data cleanup failed"))
    close_preparation = AsyncMock(side_effect=RuntimeError("preparation cleanup failed"))
    close_db = AsyncMock(side_effect=RuntimeError("database cleanup failed"))
    close_redis = AsyncMock(side_effect=RuntimeError("redis cleanup failed"))
    close_observability = MagicMock(side_effect=RuntimeError("observability cleanup failed"))
    monkeypatch.setattr(
        provider_catalog,
        "validate_wms_transport_configuration",
        MagicMock(return_value=startup),
    )
    monkeypatch.setattr(database, "init_db", AsyncMock())
    monkeypatch.setattr(database, "close_db", close_db)
    monkeypatch.setattr(database, "AsyncSessionLocal", MagicMock())
    monkeypatch.setattr(
        transport_composition,
        "build_transport_runtime",
        AsyncMock(return_value=transport_runtime),
    )
    monkeypatch.setattr(redis_client, "init_redis", AsyncMock())
    monkeypatch.setattr(redis_client, "close_redis", close_redis)
    monkeypatch.setattr(query_runtime, "build_wms_data_lane_query_runtime", MagicMock(return_value=object()))
    monkeypatch.setattr(query_runtime, "bind_wms_data_lane_query_runtime", MagicMock())
    monkeypatch.setattr(query_runtime, "close_bound_wms_data_lane_query_runtime", close_data)
    monkeypatch.setattr(
        effect_preparation_runtime, "build_wms_effect_preparation_runtime", MagicMock(return_value=object())
    )
    monkeypatch.setattr(effect_preparation_runtime, "bind_wms_effect_preparation_runtime", MagicMock())
    monkeypatch.setattr(effect_preparation_runtime, "close_wms_effect_preparation_runtime", close_preparation)
    monkeypatch.setattr(observability, "configure_runtime_open_telemetry_backend", MagicMock(return_value=False))
    monkeypatch.setattr(observability.runtime_observability_registry, "close", close_observability)

    expected_error = "serving failed" if body_fails else "observability cleanup failed"
    with pytest.raises(RuntimeError, match=expected_error):
        async with register_init(SimpleNamespace(state=SimpleNamespace())):
            if body_fails:
                raise RuntimeError("serving failed")

    close_observability.assert_called_once_with()
    close_data.assert_awaited_once_with()
    close_preparation.assert_awaited_once()
    transport_runtime.aclose.assert_awaited_once_with()
    close_db.assert_awaited_once_with()
    close_redis.assert_awaited_once_with()


def test_celery_startup_rejects_production_in_process_simulation_before_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(celery_app_module, "settings", _production_simulation_settings())
    setup_logger = MagicMock()
    monkeypatch.setattr(celery_app_module, "setup_logger", setup_logger)

    with pytest.raises(WorkerTerminate, match="WMS transport configuration rejected"):
        celery_app_module.on_worker_init()

    setup_logger.assert_not_called()
