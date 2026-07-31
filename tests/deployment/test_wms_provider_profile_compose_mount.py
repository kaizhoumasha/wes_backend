"""生产与测试部署必须将同一份 WMS Provider profile 只读挂载到固定容器路径。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_PROFILE_PATH = "/run/wes/wms-provider.yaml"
PRODUCTION_PROFILE_CONSUMERS = ("api", "celery", "celery-wms-fulfillment", "celery_beat")
TEST_DEPLOY_PROFILE_CONSUMERS = ("api", "celery", "celery-wms-fulfillment")


def _compose_command(*, test_deploy: bool) -> list[str]:
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env.test" if test_deploy else ".env.prod",
        "-f",
        "docker-compose.test-deploy.yml" if test_deploy else "docker-compose.yml",
    ]
    if not test_deploy:
        command.extend(("-f", "docker-compose.deploy.yml", "--profile", "prod"))
    command.extend(("config", "--format", "json"))
    return command


def _render_compose(*, test_deploy: bool, host_profile: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "BACKEND_IMAGE": "example.invalid/wes-backend:test",
            "WMS_PROVIDER_PROFILE_HOST_FILE": str(host_profile),
        }
    )
    completed = subprocess.run(
        _compose_command(test_deploy=test_deploy),
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _assert_profile_mount(
    compose: dict[str, object],
    *,
    service_names: tuple[str, ...],
    host_profile: Path,
) -> None:
    services = compose["services"]
    for service_name in service_names:
        service = services[service_name]
        assert service["environment"]["WMS_PROVIDER_PROFILE_FILE"] == CONTAINER_PROFILE_PATH
        profile_mounts = [volume for volume in service["volumes"] if volume.get("target") == CONTAINER_PROFILE_PATH]
        assert profile_mounts == [
            {
                "type": "bind",
                "source": str(host_profile),
                "target": CONTAINER_PROFILE_PATH,
                "read_only": True,
                "bind": {},
            }
        ]


def test_production_overlay_keeps_one_read_only_profile_mount_for_all_process_roles(tmp_path: Path) -> None:
    host_profile = tmp_path / "factory-wms-provider.yaml"
    host_profile.touch()

    compose = _render_compose(test_deploy=False, host_profile=host_profile)

    _assert_profile_mount(
        compose,
        service_names=PRODUCTION_PROFILE_CONSUMERS,
        host_profile=host_profile,
    )


def test_test_deploy_keeps_the_same_read_only_profile_mount_for_its_process_roles(tmp_path: Path) -> None:
    host_profile = tmp_path / "factory-wms-provider.yaml"
    host_profile.touch()

    compose = _render_compose(test_deploy=True, host_profile=host_profile)

    _assert_profile_mount(
        compose,
        service_names=TEST_DEPLOY_PROFILE_CONSUMERS,
        host_profile=host_profile,
    )


@pytest.mark.parametrize("test_deploy", (False, True))
def test_compose_config_fails_closed_without_the_host_profile_variable(test_deploy: bool) -> None:
    environment = os.environ.copy()
    environment["BACKEND_IMAGE"] = "example.invalid/wes-backend:test"
    environment.pop("WMS_PROVIDER_PROFILE_HOST_FILE", None)

    completed = subprocess.run(
        _compose_command(test_deploy=test_deploy),
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "WMS_PROVIDER_PROFILE_HOST_FILE is required" in completed.stderr


def test_prod_env_exposes_only_the_host_profile_path_and_documents_profile_driven_http_security() -> None:
    env_lines = (REPO_ROOT / ".env.prod").read_text(encoding="utf-8").splitlines()

    assert "WMS_PROVIDER_PROFILE_HOST_FILE=" in env_lines
    assert "WMS_PROVIDER_PROFILE_FILE=" not in env_lines
    assert any("isolated_lan + NONE" in line and "HTTP" in line for line in env_lines)


def test_test_deploy_pipeline_validates_and_exports_the_host_profile_before_compose() -> None:
    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")
    pipeline_environment = pipeline.split("    environment {", maxsplit=1)[1].split("    options {", maxsplit=1)[0]
    deploy_body = pipeline.split("stage('Deploy Test Environment')", maxsplit=1)[1].split("post {", maxsplit=1)[0]

    assert "WMS_PROVIDER_PROFILE_HOST_FILE = '/etc/wes/wms-provider.yaml'" in {
        line.strip() for line in pipeline_environment.splitlines()
    }
    profile_path_preflight = 'if [ -z "${WMS_PROVIDER_PROFILE_HOST_FILE:-}" ]'
    profile_file_preflight = '[ ! -f "${WMS_PROVIDER_PROFILE_HOST_FILE}" ]'
    profile_readable_preflight = '[ ! -r "${WMS_PROVIDER_PROFILE_HOST_FILE}" ]'
    profile_path_export = 'export WMS_PROVIDER_PROFILE_HOST_FILE="${WMS_PROVIDER_PROFILE_HOST_FILE}"'
    first_compose_index = deploy_body.index("docker compose")

    assert (
        deploy_body.index(profile_path_preflight)
        < deploy_body.index(profile_file_preflight)
        < deploy_body.index(profile_readable_preflight)
        < deploy_body.index(profile_path_export)
        < first_compose_index
    )
