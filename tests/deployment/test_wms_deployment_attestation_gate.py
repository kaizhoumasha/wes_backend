"""生产部署必须在迁移与启动前执行四角色 WMS attestation gate。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROLE_PAIRS = (
    ("api", "api"),
    ("celery", "wes-worker"),
    ("celery-wms-fulfillment", "fulfillment-worker"),
    ("celery_beat", "beat"),
)


def test_production_compose_exposes_all_four_attestation_roles() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert all(service in compose["services"] for service, _role in SERVICE_ROLE_PAIRS)


def test_production_compose_freezes_roles_and_worker_command_against_host_override(tmp_path: Path) -> None:
    host_profile = tmp_path / "provider.yaml"
    host_profile.touch()
    environment = os.environ.copy()
    environment.update(
        {
            "BACKEND_IMAGE": "example.invalid/wes:test",
            "WMS_PROVIDER_PROFILE_HOST_FILE": str(host_profile),
            "CELERY_WORKER_QUEUES": "wms-fulfillment",
            "CELERY_WORKER_CONCURRENCY": "99",
            "CELERY_CONCURRENCY": "7",
        }
    )
    docker_executable = shutil.which("docker")
    assert docker_executable is not None
    completed = subprocess.run(
        [
            docker_executable,
            "compose",
            "--env-file",
            ".env.prod",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.deploy.yml",
            "--profile",
            "prod",
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    services = json.loads(completed.stdout)["services"]

    assert services["api"]["environment"]["WMS_DEPLOYMENT_ROLE"] == "api"
    assert services["celery"]["environment"]["WMS_DEPLOYMENT_ROLE"] == "wes-worker"
    assert services["celery-wms-fulfillment"]["environment"]["WMS_DEPLOYMENT_ROLE"] == "fulfillment-worker"
    assert services["celery_beat"]["environment"]["WMS_DEPLOYMENT_ROLE"] == "beat"
    assert services["celery"]["environment"]["CELERY_WORKER_QUEUES"] == "default,celery,device"
    assert services["celery"]["environment"]["CELERY_WORKER_CONCURRENCY"] == "7"
    rendered_command = " ".join(services["celery"]["command"])
    assert "--queues=$${CELERY_WORKER_QUEUES}" in rendered_command
    assert "--concurrency=$${CELERY_WORKER_CONCURRENCY}" in rendered_command


def test_local_deploy_gate_uses_each_service_actual_image_and_environment() -> None:
    script_text = (REPO_ROOT / "scripts/docker-deploy-simple.sh").read_text(encoding="utf-8")
    gate_body = script_text.split("run_wms_deployment_attestation()", maxsplit=1)[1].split(
        "validate_complete_scale_targets()", maxsplit=1
    )[0]

    assert "bash scripts/run_wms_deployment_attestation.sh" in gate_body
    assert "local runner_args=(--compose-file docker-compose.yml)" in gate_body
    assert "runner_args+=(--compose-file docker-compose.deploy.yml)" in gate_body
    assert 'runner_args+=(--env-file "$env_file")' in gate_body
    assert '--profile "$env"' in gate_body
    assert "mktemp" not in gate_body
    assert "jq" not in gate_body
    assert "--role" not in gate_body
    assert "--image-identity" not in gate_body
    assert "|| true" not in gate_body


def test_local_capacity_gate_and_start_share_the_same_production_compose_target() -> None:
    script_text = (REPO_ROOT / "scripts/docker-deploy-simple.sh").read_text(encoding="utf-8")
    capacity_body = script_text.split("run_capacity_guard()", maxsplit=1)[1].split(
        "run_wms_deployment_attestation()", maxsplit=1
    )[0]
    cmd_up_body = script_text.split("cmd_up()", maxsplit=1)[1].split("cmd_down()", maxsplit=1)[0]

    assert 'get_deployment_compose_files "$env"' in capacity_body
    assert 'get_deployment_compose_files "$env"' in cmd_up_body
    assert capacity_body.count("$compose_files") == 1
    assert cmd_up_body.count("$compose_files") == 3
    assert 'run_wms_deployment_attestation "$env"' in cmd_up_body


def test_local_gate_runs_after_capacity_and_before_application_start() -> None:
    script_text = (REPO_ROOT / "scripts/docker-deploy-simple.sh").read_text(encoding="utf-8")
    cmd_up_body = script_text.split("cmd_up()", maxsplit=1)[1].split("cmd_down()", maxsplit=1)[0]

    capacity_index = cmd_up_body.index('run_capacity_guard "$env" "$@"')
    attestation_index = cmd_up_body.index('run_wms_deployment_attestation "$env"')
    application_index = cmd_up_body.rindex("up -d")
    assert capacity_index < attestation_index < application_index


def test_jenkins_uses_shared_attestation_runner_before_migration_and_start() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    deploy_body = jenkins_text.split("stage('Deploy Runtime')", maxsplit=1)[1].split("post {", maxsplit=1)[0]
    runner_call = "bash scripts/run_wms_deployment_attestation.sh"
    assert deploy_body.count(runner_call) == 1
    assert "--compose-file docker-compose.yml" in deploy_body
    assert "--compose-file ${DEPLOY_COMPOSE_FILE}" in deploy_body
    assert "--env-file ${DEPLOY_ENV_FILE}" in deploy_body
    assert "mktemp" not in deploy_body
    assert "jq " not in deploy_body
    assert "--role" not in deploy_body
    assert "--image-identity" not in deploy_body

    capacity_index = deploy_body.index("run_capacity_guard", deploy_body.index("docker pull"))
    attestation_index = deploy_body.index(runner_call, capacity_index)
    migration_index = deploy_body.index("api alembic upgrade head", attestation_index)
    application_index = deploy_body.index(
        "$COMPOSE_CMD up -d --no-build --no-deps ${DEPLOY_SERVICES}",
        migration_index,
    )
    assert capacity_index < attestation_index < migration_index < application_index
