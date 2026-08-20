"""生产与测试部署必须将同一份 WMS Provider profile 只读挂载到固定容器路径。"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_PROFILE_PATH = "/run/wes/wms-provider.yaml"
PRODUCTION_PROFILE_CONSUMERS = ("api", "celery", "celery-wms-fulfillment", "celery_beat")
TEST_DEPLOY_PROFILE_CONSUMERS = ("api", "celery", "celery-wms-fulfillment", "celery_beat")
REQUIRED_PROFILE_MOUNT = (
    f"${{WMS_PROVIDER_PROFILE_HOST_FILE:?WMS_PROVIDER_PROFILE_HOST_FILE is required}}:{CONTAINER_PROFILE_PATH}:ro"
)


def _load_compose(filename: str) -> dict[str, object]:
    compose_text = (REPO_ROOT / filename).read_text(encoding="utf-8")
    # PyYAML 不认识 Compose 的 sequence override 标签；静态合同只需保留其序列值。
    return yaml.safe_load(compose_text.replace("!override", ""))


def _assert_profile_mount(
    compose: dict[str, object],
    *,
    service_names: tuple[str, ...],
) -> None:
    services = compose["services"]
    for service_name in service_names:
        service = services[service_name]
        parent_name = service.get("extends", {}).get("service")
        parent = services[parent_name] if parent_name else {}
        environment = {**parent.get("environment", {}), **service.get("environment", {})}
        volumes = service.get("volumes", parent.get("volumes", []))

        assert environment["WMS_PROVIDER_PROFILE_FILE"] == CONTAINER_PROFILE_PATH
        assert volumes.count(REQUIRED_PROFILE_MOUNT) == 1


def test_production_overlay_keeps_one_read_only_profile_mount_for_all_process_roles() -> None:
    compose = _load_compose("docker-compose.deploy.yml")

    _assert_profile_mount(
        compose,
        service_names=PRODUCTION_PROFILE_CONSUMERS,
    )


def test_test_deploy_keeps_the_same_read_only_profile_mount_for_its_process_roles() -> None:
    compose = _load_compose("docker-compose.test-deploy.yml")

    _assert_profile_mount(
        compose,
        service_names=TEST_DEPLOY_PROFILE_CONSUMERS,
    )


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
