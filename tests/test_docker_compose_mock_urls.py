from __future__ import annotations

from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_docker_compose_uses_container_urls_for_mock_wms_flow() -> None:
    compose = yaml.safe_load((BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    api_env = services["api"]["environment"]
    celery_env = services["celery_worker"]["environment"]
    mock_wms_env = services["mock_wms"]["environment"]

    assert api_env["WMS_SYNC_BASE_URL"] == "${CONTAINER_WMS_SYNC_BASE_URL:-http://wms/api}"
    assert (
        api_env["WMS_RCS_RACK_OPERATION_URL"]
        == "${CONTAINER_WMS_RCS_RACK_OPERATION_URL:-http://wms-rcs/api/wes/rack-operation}"
    )
    assert celery_env["WMS_SYNC_BASE_URL"] == api_env["WMS_SYNC_BASE_URL"]
    assert celery_env["WMS_RCS_RACK_OPERATION_URL"] == api_env["WMS_RCS_RACK_OPERATION_URL"]
    assert (
        mock_wms_env["WES_EXTERNAL_CALLBACK_URL"]
        == "${CONTAINER_WES_EXTERNAL_CALLBACK_URL:-http://api:8001/api/v1/callback/external}"
    )


def test_dev_and_test_env_declare_container_mock_urls() -> None:
    for env_file in (".env.dev", ".env.test"):
        env_text = (BACKEND_ROOT / env_file).read_text(encoding="utf-8")

        assert "CONTAINER_WMS_SYNC_BASE_URL=http://mock_wms:8011/api/wms" in env_text
        assert "CONTAINER_WMS_RCS_RACK_OPERATION_URL=http://mock_wms:8011/api/wms/rack-operation" in env_text
        assert "CONTAINER_WES_EXTERNAL_CALLBACK_URL=http://api:8001/api/v1/callback/external" in env_text
