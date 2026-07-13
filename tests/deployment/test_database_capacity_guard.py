"""数据库连接容量与部署拓扑合同。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.capacity_guard import (
    API_DATABASE_POOL_SIZE,
    API_UVICORN_WORKERS,
    CELERY_DATABASE_POOL_SIZE,
    CapacityPlan,
    CapacityViolation,
    _parse_scale,
    build_capacity_plan,
    calculate_capacity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_production_capacity_formula_matches_single_api_and_four_celery_containers() -> None:
    plan = CapacityPlan(
        api_replicas=1,
        api_processes=4,
        api_pool_size=5,
        celery_replicas=4,
        celery_processes=4,
        celery_pool_size=1,
        reserve=10,
    )

    result = calculate_capacity(plan, max_connections=100)

    assert result.api_connections == 20
    assert result.celery_connections == 16
    assert result.application_connections == 36
    assert result.available_connections == 90


def test_capacity_guard_rejects_budget_above_live_postgresql_limit() -> None:
    plan = CapacityPlan(
        api_replicas=2,
        api_processes=4,
        api_pool_size=5,
        celery_replicas=4,
        celery_processes=4,
        celery_pool_size=1,
        reserve=10,
    )

    with pytest.raises(CapacityViolation, match="exceeds"):
        calculate_capacity(plan, max_connections=60)


def test_capacity_guard_rejects_nonzero_overflow_and_illegal_role_pool() -> None:
    with pytest.raises(CapacityViolation, match="max_overflow"):
        CapacityPlan(
            api_replicas=1,
            api_processes=4,
            api_pool_size=5,
            celery_replicas=4,
            celery_processes=4,
            celery_pool_size=1,
            reserve=10,
            max_overflow=1,
        )

    with pytest.raises(CapacityViolation, match="celery_pool_size"):
        CapacityPlan(
            api_replicas=1,
            api_processes=4,
            api_pool_size=5,
            celery_replicas=4,
            celery_processes=4,
            celery_pool_size=2,
            reserve=10,
        )


def test_environment_profiles_and_compose_declare_explicit_database_roles() -> None:
    for env_name in (".env.dev", ".env.test", ".env.prod"):
        env_text = (REPO_ROOT / env_name).read_text(encoding="utf-8")
        assert "DATABASE_RUNTIME_ROLE=cli" in env_text
        assert "DATABASE_POOL_SIZE=1" in env_text
        assert "DATABASE_MAX_OVERFLOW=0" in env_text

    prod_text = (REPO_ROOT / ".env.prod").read_text(encoding="utf-8")
    assert "API_REPLICAS=1" in prod_text

    for compose_name in ("docker-compose.yml", "docker-compose.deploy.yml"):
        compose = yaml.safe_load((REPO_ROOT / compose_name).read_text(encoding="utf-8"))
        api_env = compose["services"]["api"]["environment"]
        worker_env = compose["services"]["celery_worker"]["environment"]
        assert api_env["DATABASE_RUNTIME_ROLE"] == "api"
        assert api_env["DATABASE_POOL_SIZE"] == API_DATABASE_POOL_SIZE
        assert api_env["DATABASE_MAX_OVERFLOW"] == 0
        assert worker_env["DATABASE_RUNTIME_ROLE"] == "celery"
        assert worker_env["DATABASE_POOL_SIZE"] == CELERY_DATABASE_POOL_SIZE
        assert worker_env["DATABASE_MAX_OVERFLOW"] == 0
        assert compose["services"]["celery_beat"]["environment"]["DATABASE_RUNTIME_ROLE"] == "cli"
        assert compose["services"]["flower"]["environment"]["DATABASE_RUNTIME_ROLE"] == "cli"

    test_compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    pytest_env = test_compose["services"]["pytest"]["environment"]
    assert pytest_env["DATABASE_RUNTIME_ROLE"] == "integration"
    assert pytest_env["DATABASE_POOL_SIZE"] == 1
    assert pytest_env["DATABASE_MAX_OVERFLOW"] == 0
    assert pytest_env["DATABASE_APPLICATION_RUN_ID"] == "${INTEGRATION_RUN_ID:-}"


def test_deployment_script_runs_live_guard_before_application_start_and_scale() -> None:
    script_text = (REPO_ROOT / "scripts/docker-deploy-simple.sh").read_text(encoding="utf-8")

    assert "celery_worker=8" not in script_text
    assert "capacity_guard.py" in script_text
    cmd_up_body = script_text.split("cmd_up()", maxsplit=1)[1].split("cmd_down()", maxsplit=1)[0]
    assert "--wait db redis" in cmd_up_body
    assert cmd_up_body.index("--wait db redis") < cmd_up_body.index("run_capacity_guard")
    assert "run_capacity_guard" in script_text
    assert "validate_complete_scale_targets" in script_text
    scale_body = script_text.split("cmd_scale()", maxsplit=1)[1].split("cmd_migrate()", maxsplit=1)[0]
    assert scale_body.index("validate_complete_scale_targets") < scale_body.index("run_capacity_guard")


def test_jenkins_and_production_runbook_require_live_guard_before_application_start() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    deploy_body = jenkins_text.split("stage('Deploy Runtime')", maxsplit=1)[1].split("post {", maxsplit=1)[0]
    guard_command = (
        "run --rm --no-deps "
        "-e DATABASE_RUNTIME_ROLE=cli "
        "-e DATABASE_POOL_SIZE=1 "
        "-e DATABASE_MAX_OVERFLOW=0 "
        "api python scripts/capacity_guard.py --services api,celery_worker"
    )

    assert (
        'COMPOSE_CMD="docker compose -f docker-compose.yml -f ${DEPLOY_COMPOSE_FILE} '
        '--env-file ${DEPLOY_ENV_FILE}"' in deploy_body
    )
    assert "$COMPOSE_CMD up -d --wait db redis" in deploy_body
    assert guard_command in deploy_body
    infra_index = deploy_body.index("$COMPOSE_CMD up -d --wait db redis")
    application_command = "$COMPOSE_CMD up -d --no-build --no-deps ${DEPLOY_SERVICES}"
    first_guard_index = deploy_body.index("run_capacity_guard", infra_index)
    first_application_index = deploy_body.index(application_command, first_guard_index)
    assert infra_index < first_guard_index < first_application_index

    automatic_rollback = deploy_body.split('if [ "$HEALTH_CHECK_PASSED" = false ]', maxsplit=1)[1]
    assert automatic_rollback.index("run_capacity_guard") < automatic_rollback.index(application_command)

    runbook_text = (REPO_ROOT / "docs/devops/prod-release-deploy.md").read_text(encoding="utf-8")
    standard_release = runbook_text.split("### 4.5", maxsplit=1)[1].split("### 4.7", maxsplit=1)[0]
    rollback = runbook_text.split("## 6. 回滚策略", maxsplit=1)[1].split("## 7.", maxsplit=1)[0]
    for section in (standard_release, rollback):
        assert "DATABASE_RUNTIME_ROLE=cli" in section
        assert "DATABASE_POOL_SIZE=1" in section
        assert "DATABASE_MAX_OVERFLOW=0" in section
        assert "python scripts/capacity_guard.py --services api,celery_worker" in section
        assert section.index("python scripts/capacity_guard.py") < section.index(
            "up -d api celery_worker celery_beat flower nginx"
        )
    assert "基础设施保持在线" in rollback


def test_dockerfile_keeps_four_uvicorn_processes_as_capacity_input() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert f'"--workers", "{API_UVICORN_WORKERS}"' in dockerfile_text
    assert "1 x 4 x 5" in dockerfile_text


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("API_UVICORN_WORKERS", "3"),
        ("API_DATABASE_POOL_SIZE", "4"),
        ("CELERY_DATABASE_POOL_SIZE", "2"),
    ],
)
def test_capacity_plan_rejects_topology_overrides_that_disagree_with_runtime(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(CapacityViolation, match=name):
        build_capacity_plan(services={"api", "celery_worker"}, scales={})


def test_capacity_plan_uses_only_actual_docker_process_and_pool_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("API_UVICORN_WORKERS", "API_DATABASE_POOL_SIZE", "CELERY_DATABASE_POOL_SIZE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_REPLICAS", "1")
    monkeypatch.setenv("CELERY_WORKER_REPLICAS", "4")
    monkeypatch.setenv("CELERY_CONCURRENCY", "4")

    plan = build_capacity_plan(services={"api", "celery_worker"}, scales={})

    assert plan.api_processes == 4
    assert plan.api_pool_size == 5
    assert plan.celery_pool_size == 1
    assert calculate_capacity(plan, max_connections=100).application_connections == 36


def test_capacity_plan_rejects_reserve_below_ten() -> None:
    with pytest.raises(CapacityViolation, match=r"reserve.*10"):
        CapacityPlan(
            api_replicas=1,
            api_processes=4,
            api_pool_size=5,
            celery_replicas=4,
            celery_processes=4,
            celery_pool_size=1,
            reserve=9,
        )


@pytest.mark.parametrize(
    ("arguments", "environment"),
    [(["--reserve", "9"], {}), ([], {"DATABASE_CONNECTION_RESERVE": "9"})],
)
def test_capacity_guard_cli_rejects_reserve_below_ten(
    arguments: list[str],
    environment: dict[str, str],
) -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/capacity_guard.py"), "--max-connections", "100", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "reserve" in completed.stderr
    assert "10" in completed.stderr


@pytest.mark.parametrize("scale", [["api=3"], ["celery_worker=10"]])
def test_scale_requires_complete_api_and_celery_target_topology(scale: list[str]) -> None:
    with pytest.raises(CapacityViolation, match=r"complete.*api.*celery_worker"):
        _parse_scale(scale)


def test_scale_calculates_complete_api_then_worker_target_without_prior_state() -> None:
    scales = _parse_scale(["api=3", "celery_worker=10"])

    plan = build_capacity_plan(services={"api", "celery_worker"}, scales=scales)
    result = calculate_capacity(plan, max_connections=200)

    assert scales == {"api": 3, "celery_worker": 10}
    assert result.api_connections == 60
    assert result.celery_connections == 40
    assert result.application_connections == 100


def test_capacity_plan_rejects_zero_celery_concurrency_when_workers_exist() -> None:
    with pytest.raises(CapacityViolation, match=r"celery_processes.*at least 1"):
        CapacityPlan(
            api_replicas=1,
            api_processes=4,
            api_pool_size=5,
            celery_replicas=1,
            celery_processes=0,
            celery_pool_size=1,
            reserve=10,
        )


def test_capacity_guard_env_rejects_zero_celery_concurrency() -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/capacity_guard.py"), "--max-connections", "100"],
        cwd=REPO_ROOT,
        env={**os.environ, "CELERY_WORKER_REPLICAS": "1", "CELERY_CONCURRENCY": "0"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "celery_processes" in completed.stderr


def _run_shell_scale_validation(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; shift; validate_complete_scale_targets "$@"',
            "scale-contract",
            str(REPO_ROOT / "scripts/docker-deploy-simple.sh"),
            *arguments,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["api=1", "celery_worker=4"],
        ["--scale=api=1", "--scale=celery_worker=4"],
        ["--scale", "api=1", "--scale", "celery_worker=4"],
    ],
)
def test_shell_scale_validation_accepts_all_documented_complete_forms(arguments: list[str]) -> None:
    completed = _run_shell_scale_validation(arguments)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("arguments", [["--scale", "api=1"], ["--scale", "celery_worker=4"]])
def test_shell_scale_validation_rejects_each_incomplete_target(arguments: list[str]) -> None:
    completed = _run_shell_scale_validation(arguments)

    assert completed.returncode != 0
    assert "api=<n> celery_worker=<n>" in completed.stdout
