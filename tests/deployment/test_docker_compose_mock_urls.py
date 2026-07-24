from __future__ import annotations

from pathlib import Path

import yaml

from src.core.conf import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
HMAC_SECRET_NAMES = (
    "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1",
    "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V1",
    "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",
    "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2",
    "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V2",
    "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2",
    "WMS_LEGACY_TRANSPORT_SANDBOX_HMAC_SECRET_V1",
    "WMS_LEGACY_TRANSPORT_STAGING_HMAC_SECRET_V1",
    "WMS_LEGACY_TRANSPORT_PRODUCTION_HMAC_SECRET_V1",
    "WORKLINE_PLUGIN_RUNTIME_SANDBOX_HMAC_SECRET_V1",
    "WORKLINE_PLUGIN_RUNTIME_STAGING_HMAC_SECRET_V1",
    "WORKLINE_PLUGIN_RUNTIME_PRODUCTION_HMAC_SECRET_V1",
)
REVOKED_CREDENTIAL_REFERENCES_NAME = "WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES"
LEGACY_ENDPOINT_NAMES = (
    "WMS_RCS_RACK_OPERATION_URL",
    "WMS_RCS_BIN_OPERATION_URL",
    "WMS_RCS_FULL_BOX_EXCHANGE_URL",
)
STATUS_CONFIG_NAMES = (
    "WMS_EFFECT_STATUS_TIMEOUT_SECONDS",
    "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES",
    "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS",
    "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS",
    "WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS",
    "WES_EFFECT_NOT_FOUND_GRACE_SECONDS",
    "WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS",
    "WES_EFFECT_STATUS_SCAN_BATCH_SIZE",
    "WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS",
    "WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS",
    "WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS",
    "WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS",
)


def test_docker_compose_uses_container_urls_for_mock_wms_flow() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    api_env = services["api"]["environment"]
    celery_env = services["celery_worker"]["environment"]
    celery_beat_env = services["celery_beat"]["environment"]
    mock_wms_env = services["mock_wms"]["environment"]

    assert api_env["WMS_SYNC_BASE_URL"] == "${CONTAINER_WMS_SYNC_BASE_URL:-}"
    for endpoint_name in LEGACY_ENDPOINT_NAMES:
        container_name = f"CONTAINER_{endpoint_name}"
        assert api_env[endpoint_name] == f"${{{container_name}:-}}"
        assert celery_env[endpoint_name] == api_env[endpoint_name]
    assert celery_env["WMS_SYNC_BASE_URL"] == api_env["WMS_SYNC_BASE_URL"]
    assert api_env["WMS_EFFECT_STATUS_URL"] == "${CONTAINER_WMS_EFFECT_STATUS_URL:-}"
    assert celery_env["WMS_EFFECT_STATUS_URL"] == api_env["WMS_EFFECT_STATUS_URL"]
    assert celery_beat_env["WMS_EFFECT_STATUS_URL"] == api_env["WMS_EFFECT_STATUS_URL"]
    for setting_name in STATUS_CONFIG_NAMES:
        assert api_env[setting_name] == f"${{{setting_name}}}"
        assert celery_env[setting_name] == api_env[setting_name]
        assert celery_beat_env[setting_name] == api_env[setting_name]
    for secret_name in HMAC_SECRET_NAMES:
        assert api_env[secret_name] == f"${{{secret_name}:-}}"
        assert celery_env[secret_name] == api_env[secret_name]
        assert celery_beat_env[secret_name] == api_env[secret_name]
    assert api_env[REVOKED_CREDENTIAL_REFERENCES_NAME] == f"${{{REVOKED_CREDENTIAL_REFERENCES_NAME}:-}}"
    assert celery_env[REVOKED_CREDENTIAL_REFERENCES_NAME] == api_env[REVOKED_CREDENTIAL_REFERENCES_NAME]
    assert celery_beat_env[REVOKED_CREDENTIAL_REFERENCES_NAME] == api_env[REVOKED_CREDENTIAL_REFERENCES_NAME]
    assert (
        mock_wms_env["WES_EXTERNAL_CALLBACK_URL"]
        == "${CONTAINER_WES_EXTERNAL_CALLBACK_URL:-http://api:8001/api/v1/callback/external}"
    )
    assert mock_wms_env["API_APP_ID"] == "${API_APP_ID:-app_local_mock}"
    assert mock_wms_env["API_APP_SECRET"] == "${API_APP_SECRET:-local_mock_change_me}"


def test_dev_and_test_env_declare_container_mock_urls() -> None:
    for env_file in (".env.dev", ".env.test"):
        env_text = (BACKEND_ROOT / env_file).read_text(encoding="utf-8")

        assert "CONTAINER_WMS_SYNC_BASE_URL=http://mock_wms:8011/api/wms" in env_text
        assert "CONTAINER_WMS_EFFECT_STATUS_URL=http://mock_wms:8011/northbound/operations/status" in env_text
        assert "WMS_EFFECT_STATUS_URL=http://localhost:8011/northbound/operations/status" in env_text
        for setting_name in STATUS_CONFIG_NAMES:
            assert f"{setting_name}=" in env_text
        for endpoint_name in LEGACY_ENDPOINT_NAMES:
            assert f"CONTAINER_{endpoint_name}=http://mock_wms:8011/" in env_text
            assert f"{endpoint_name}=http://localhost:8011/" in env_text
        assert "CONTAINER_WES_EXTERNAL_CALLBACK_URL=http://api:8001/api/v1/callback/external" in env_text
        assert "API_APP_ID=app_local_mock" in env_text
        assert "API_APP_SECRET=local_mock_change_me" in env_text
        assert "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=true" in env_text
        assert "WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION=v2" in env_text
        assert "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1=" in env_text
        assert "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2=" in env_text
        assert "WMS_LEGACY_TRANSPORT_SANDBOX_HMAC_SECRET_V1=" in env_text
        assert "WORKLINE_PLUGIN_RUNTIME_SANDBOX_HMAC_SECRET_V1=" in env_text
        assert f"{REVOKED_CREDENTIAL_REFERENCES_NAME}=" in env_text


def test_local_settings_load_all_active_wms_credentials_from_generated_dotenv() -> None:
    local_settings = Settings(_env_file=BACKEND_ROOT / ".env.dev")  # pyright: ignore[reportCallIssue]

    assert local_settings.WMS_SYNC_BASE_URL
    assert local_settings.WMS_EFFECT_STATUS_URL
    assert local_settings.WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS >= (
        local_settings.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS + local_settings.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS
    )
    for endpoint_name in LEGACY_ENDPOINT_NAMES:
        assert getattr(local_settings, endpoint_name)
    assert local_settings.WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED is True
    assert local_settings.WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION == "v2"
    assert local_settings.WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1
    assert local_settings.WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2
    assert local_settings.WMS_LEGACY_TRANSPORT_SANDBOX_HMAC_SECRET_V1
    assert local_settings.WORKLINE_PLUGIN_RUNTIME_SANDBOX_HMAC_SECRET_V1


def test_prod_env_requires_explicit_wms_https_and_production_hmac_secret() -> None:
    env_text = (BACKEND_ROOT / ".env.prod").read_text(encoding="utf-8")

    assert "CONTAINER_WMS_SYNC_BASE_URL=" in env_text
    assert "WMS_SYNC_BASE_URL=" in env_text
    assert "CONTAINER_WMS_EFFECT_STATUS_URL=" in env_text
    assert "WMS_EFFECT_STATUS_URL=" in env_text
    for setting_name in STATUS_CONFIG_NAMES:
        assert f"{setting_name}=" in env_text
    for endpoint_name in LEGACY_ENDPOINT_NAMES:
        assert f"CONTAINER_{endpoint_name}=" in env_text
        assert f"{endpoint_name}=" in env_text
    assert "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=false" in env_text
    assert "WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION=v2" in env_text
    assert "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1=" in env_text
    assert "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2=" in env_text
    assert "WMS_LEGACY_TRANSPORT_PRODUCTION_HMAC_SECRET_V1=" in env_text
    assert "WORKLINE_PLUGIN_RUNTIME_PRODUCTION_HMAC_SECRET_V1=" in env_text
    assert f"{REVOKED_CREDENTIAL_REFERENCES_NAME}=" in env_text
