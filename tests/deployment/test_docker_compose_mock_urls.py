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
)
REVOKED_CREDENTIAL_REFERENCES_NAME = "WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES"
MOCK_WMS_MATERIAL_FLOW_SECRET_NAMES = (
    "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1",
    "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2",
)
ACCEPTANCE_CONTRACT_CONFIG_NAMES = (
    "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS",
    "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS",
    "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES",
    "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS",
    "WMS_EFFECT_STATUS_TIMEOUT_SECONDS",
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
    "WES_EFFECT_STATUS_MAX_IN_FLIGHT",
    "WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS",
    "WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS",
    "WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS",
    "WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS",
)


def test_docker_compose_uses_one_provider_profile_path_for_wms_processes() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    acceptance_compose = yaml.safe_load(
        (BACKEND_ROOT / "docker-compose.wms-acceptance.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    api_env = services["api"]["environment"]
    celery_env = services["celery"]["environment"]
    celery_beat_env = services["celery_beat"]["environment"]
    mock_wms_env = services["mock_wms"]["environment"]

    assert api_env["WMS_PROVIDER_PROFILE_FILE"] == "${WMS_PROVIDER_PROFILE_FILE:-}"
    assert celery_env["WMS_PROVIDER_PROFILE_FILE"] == api_env["WMS_PROVIDER_PROFILE_FILE"]
    assert celery_beat_env["WMS_PROVIDER_PROFILE_FILE"] == api_env["WMS_PROVIDER_PROFILE_FILE"]
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
    for secret_name in MOCK_WMS_MATERIAL_FLOW_SECRET_NAMES:
        assert mock_wms_env[secret_name] == f"${{{secret_name}:-}}"
    assert "MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1" not in mock_wms_env
    assert acceptance_compose["services"]["mock_wms_acceptance"]["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-f",
        "http://localhost:8011/northbound/contract",
    ]
    acceptance_mock_env = acceptance_compose["services"]["mock_wms_acceptance"]["environment"]
    for setting_name in ACCEPTANCE_CONTRACT_CONFIG_NAMES:
        assert acceptance_mock_env[setting_name] == f"${{{setting_name}}}"


def test_wms_acceptance_compose_is_isolated_from_the_development_mock() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    acceptance_compose = yaml.safe_load(
        (BACKEND_ROOT / "docker-compose.wms-acceptance.yml").read_text(encoding="utf-8")
    )
    development_mock = compose["services"]["mock_wms"]
    acceptance_services = acceptance_compose["services"]

    assert "mock_wms" not in acceptance_services
    acceptance_mock = acceptance_services["mock_wms_acceptance"]
    assert "container_name" not in acceptance_mock
    assert acceptance_mock["ports"] == ["${DOCKER_HOST_BIND_IP:-127.0.0.1}:${MOCK_WMS_ACCEPTANCE_PORT:-18011}:8011"]
    assert acceptance_mock["ports"] != development_mock["ports"]


def test_mock_wms_receives_the_same_public_northbound_contract_settings_as_wes() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    api_env = services["api"]["environment"]
    mock_wms_env = services["mock_wms"]["environment"]

    for setting_name in (
        "WMS_EFFECT_STATUS_TIMEOUT_SECONDS",
        "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES",
        "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS",
        "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS",
    ):
        assert mock_wms_env[setting_name] == api_env[setting_name]


def test_mock_wms_submit_deadline_is_independently_configurable() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    mock_wms_env = compose["services"]["mock_wms"]["environment"]

    assert mock_wms_env["WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS"] == "${WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS}"
    for env_file in (".env.dev", ".env.test", ".env.prod"):
        env_text = (BACKEND_ROOT / env_file).read_text(encoding="utf-8")
        assert "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS=30" in env_text


def test_mock_wms_disables_query_bearing_uvicorn_access_log() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    mock_wms_command = compose["services"]["mock_wms"]["command"]

    assert "--no-access-log" in mock_wms_command


def test_mock_dockerfile_copies_only_the_current_sandbox_catalog_bridge() -> None:
    dockerfile_text = (BACKEND_ROOT / "tests/mock/Dockerfile").read_text(encoding="utf-8")

    assert (
        "COPY src/app/runtime/orchestration/sandbox_catalog_bridge.py "
        "/app/src/app/runtime/orchestration/sandbox_catalog_bridge.py"
    ) in dockerfile_text
    assert "src/workline_runtime/sandbox_catalog.py" not in dockerfile_text
    assert "src/workline_runtime/runtime_events.py" not in dockerfile_text


def test_mock_package_does_not_eagerly_import_peer_servers() -> None:
    package_text = (BACKEND_ROOT / "tests/mock/__init__.py").read_text(encoding="utf-8")

    assert "from tests.mock.ecs_mock_server import" not in package_text
    assert "from tests.mock.wms_mock_server import" not in package_text


def test_dev_and_test_env_declare_the_provider_profile_entry() -> None:
    for env_file in (".env.dev", ".env.test"):
        env_text = (BACKEND_ROOT / env_file).read_text(encoding="utf-8")

        assert "WMS_PROVIDER_PROFILE_FILE=" in env_text
        for setting_name in STATUS_CONFIG_NAMES:
            assert f"{setting_name}=" in env_text
        assert "CONTAINER_WES_EXTERNAL_CALLBACK_URL=http://api:8001/api/v1/callback/external" in env_text
        assert "API_APP_ID=app_local_mock" in env_text
        assert "API_APP_SECRET=local_mock_change_me" in env_text
        assert "MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1=" not in env_text
        assert "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=true" in env_text
        assert "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1=" in env_text
        assert "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2=" in env_text
        assert f"{REVOKED_CREDENTIAL_REFERENCES_NAME}=" in env_text


def test_local_settings_load_wms_budgets_and_credentials_from_generated_dotenv() -> None:
    local_settings = Settings(_env_file=BACKEND_ROOT / ".env.dev")  # pyright: ignore[reportCallIssue]

    assert local_settings.WMS_PROVIDER_PROFILE_FILE is None
    assert local_settings.WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS >= (
        local_settings.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS + local_settings.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS
    )
    assert local_settings.WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED is True
    assert local_settings.WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1
    assert local_settings.WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2


def test_prod_env_requires_an_explicit_provider_profile_and_keeps_secret_slots() -> None:
    env_text = (BACKEND_ROOT / ".env.prod").read_text(encoding="utf-8")

    assert "WMS_PROVIDER_PROFILE_HOST_FILE=" in env_text
    assert "\nWMS_PROVIDER_PROFILE_FILE=" not in env_text
    for setting_name in STATUS_CONFIG_NAMES:
        assert f"{setting_name}=" in env_text
    assert "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=false" in env_text
    assert "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1=" in env_text
    assert "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2=" in env_text
    assert f"{REVOKED_CREDENTIAL_REFERENCES_NAME}=" in env_text
