from __future__ import annotations

from pathlib import Path

import yaml

from src.core.conf import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_uses_one_target_endpoint_for_outbound_processes() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    api_env = services["api"]["environment"]
    celery_env = services["celery"]["environment"]
    celery_beat_env = services["celery_beat"]["environment"]
    assert api_env["WMS_BASE_URL"] == "${WMS_BASE_URL}"
    assert api_env["TRANSPORT_SUBMIT_PATH"] == "${TRANSPORT_SUBMIT_PATH}"
    assert celery_env["WMS_BASE_URL"] == api_env["WMS_BASE_URL"]
    assert celery_env["TRANSPORT_SUBMIT_PATH"] == api_env["TRANSPORT_SUBMIT_PATH"]
    assert "WMS_BASE_URL" not in celery_beat_env


def test_wms_transport_acceptance_uses_current_callback_and_health_contract() -> None:
    acceptance_compose = yaml.safe_load(
        (BACKEND_ROOT / "docker-compose.wms-acceptance.yml").read_text(encoding="utf-8")
    )
    acceptance_mock = acceptance_compose["services"]["mock_wms_acceptance"]

    assert acceptance_mock["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-f",
        "http://localhost:8011/",
    ]
    assert acceptance_mock["environment"]["WES_TRANSPORT_EVENT_URL"] == (
        "${CONTAINER_WES_TRANSPORT_EVENT_URL:?required}"
    )
    assert set(acceptance_mock["environment"]) == {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
        "LOG_LEVEL",
        "WES_TRANSPORT_EVENT_URL",
    }


def test_development_wms_mock_uses_only_the_current_transport_callback_contract() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    mock_environment = compose["services"]["mock_wms"]["environment"]

    assert mock_environment["WES_TRANSPORT_EVENT_URL"] == (
        "${CONTAINER_WES_TRANSPORT_EVENT_URL:-http://api:8001/api/v1/wms/events}"
    )
    assert set(mock_environment) == {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "LOG_LEVEL",
        "WES_TRANSPORT_EVENT_URL",
    }


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


def test_mock_wms_disables_query_bearing_uvicorn_access_log() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    mock_wms_command = compose["services"]["mock_wms"]["command"]

    assert "--no-access-log" in mock_wms_command


def test_mock_dockerfile_does_not_copy_plugin_catalog_bridges() -> None:
    dockerfile_text = (BACKEND_ROOT / "tests/mock/Dockerfile").read_text(encoding="utf-8")

    assert "src/app/runtime/orchestration/sandbox_catalog_bridge.py" not in dockerfile_text
    assert "src/workline_runtime/sandbox_catalog.py" not in dockerfile_text
    assert "src/workline_runtime/runtime_events.py" not in dockerfile_text


def test_mock_package_does_not_eagerly_import_peer_servers() -> None:
    package_text = (BACKEND_ROOT / "tests/mock/__init__.py").read_text(encoding="utf-8")

    assert "from tests.mock.ecs_mock_server import" not in package_text
    assert "from tests.mock.wms_mock_server import" not in package_text


def test_dev_and_test_env_declare_the_target_wms_endpoint() -> None:
    for env_file in (".env.dev", ".env.test"):
        env_text = (BACKEND_ROOT / env_file).read_text(encoding="utf-8")

        assert "WMS_BASE_URL=http://mock_wms:8011" in env_text
        assert "TRANSPORT_SUBMIT_PATH=/api/v1/wes/transport-requests" in env_text
        assert "API_APP_ID=app_local_mock" in env_text
        assert "API_APP_SECRET=local_mock_change_me" in env_text
        assert "MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1=" not in env_text


def test_local_settings_load_target_wms_endpoint_from_generated_dotenv() -> None:
    local_settings = Settings(_env_file=BACKEND_ROOT / ".env.dev")  # pyright: ignore[reportCallIssue]

    assert local_settings.WMS_BASE_URL == "http://mock_wms:8011"
    assert local_settings.TRANSPORT_SUBMIT_PATH == "/api/v1/wes/transport-requests"


def test_prod_env_requires_an_explicit_target_wms_origin() -> None:
    env_text = (BACKEND_ROOT / ".env.prod").read_text(encoding="utf-8")

    assert "\nWMS_BASE_URL=\n" in env_text
    assert "TRANSPORT_SUBMIT_PATH=/api/v1/wes/transport-requests" in env_text
