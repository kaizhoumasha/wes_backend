"""数据库连接容量与部署拓扑合同。"""

from __future__ import annotations

import os
import shutil
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
    _read_api_uvicorn_workers,
    build_capacity_plan,
    calculate_capacity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _ComposeSafeLoader(yaml.SafeLoader):
    """解析 Compose 的 YAML 标签，同时保留普通 SafeLoader 语义。"""


def _construct_compose_sequence(loader: _ComposeSafeLoader, node: yaml.nodes.SequenceNode) -> list[object]:
    return loader.construct_sequence(node)


_ComposeSafeLoader.add_constructor("!override", _construct_compose_sequence)


def test_local_compose_build_does_not_require_ci_provenance_arguments() -> None:
    compose = yaml.load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"),
        Loader=_ComposeSafeLoader,  # noqa: S506 - 仓库内受控 Compose，需解析 !override 标签
    )
    build_args = compose["services"]["api"]["build"]["args"]
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "WES_VCS_REVISION" not in build_args
    assert "WES_SOURCE_TREE" not in build_args
    assert 'test -n "${WES_VCS_REVISION}"' not in dockerfile_text
    assert 'test -n "${WES_SOURCE_TREE}"' not in dockerfile_text


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
        compose = yaml.load(
            (REPO_ROOT / compose_name).read_text(encoding="utf-8"),
            Loader=_ComposeSafeLoader,  # noqa: S506 -- 仅扩展 SafeLoader 解析 Compose 的 !override 标签。
        )
        api_env = compose["services"]["api"]["environment"]
        worker_env = compose["services"]["celery"]["environment"]
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
    pytest_command = test_compose["services"]["pytest"]["command"]
    assert "integration-$$(date +%s)-$$$$" in pytest_command


def test_production_compose_uses_image_source_without_host_override() -> None:
    prod_text = (REPO_ROOT / ".env.prod").read_text(encoding="utf-8")
    deploy_text = (REPO_ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert "SOURCE_MOUNT=" not in prod_text
    assert deploy_text.count("volumes: !override") == 5

    env = os.environ.copy()
    env["BACKEND_IMAGE"] = "example.invalid/wes/wes_backend:test"
    env["FRONTEND_IMAGE"] = "example.invalid/wes/wes_frontend:test"
    env["WMS_PROVIDER_PROFILE_HOST_FILE"] = str(REPO_ROOT / ".env.prod")
    docker_path = shutil.which("docker")
    if docker_path is None:
        pytest.skip("Docker CLI is required to render the merged production Compose contract")
    completed = subprocess.run(
        [
            docker_path,
            "compose",
            "--env-file",
            ".env.prod",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.deploy.yml",
            "--profile",
            "*",
            "config",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    merged = yaml.safe_load(completed.stdout)
    for service_name in ("api", "celery", "celery-wms-fulfillment", "celery_beat", "flower"):
        targets = {volume["target"] for volume in merged["services"][service_name].get("volumes", [])}
        assert "/app/src" not in targets

    for service_name in ("api", "flower"):
        published_ports = merged["services"][service_name]["ports"]
        assert len(published_ports) == 1


def test_deployment_script_runs_live_guard_before_application_start_and_scale() -> None:
    script_text = (REPO_ROOT / "scripts/docker-deploy-simple.sh").read_text(encoding="utf-8")

    assert "celery=8" not in script_text
    assert "capacity_guard.py" in script_text
    cmd_up_body = script_text.split("cmd_up()", maxsplit=1)[1].split("cmd_down()", maxsplit=1)[0]
    assert "--wait db redis" in cmd_up_body
    assert cmd_up_body.index("--wait db redis") < cmd_up_body.index("run_capacity_guard")
    assert "run_capacity_guard" in script_text
    assert "validate_complete_scale_targets" in script_text
    scale_body = script_text.split("cmd_scale()", maxsplit=1)[1].split("cmd_migrate()", maxsplit=1)[0]
    assert scale_body.index("validate_complete_scale_targets") < scale_body.index("run_capacity_guard")


def test_dockerfile_keeps_four_uvicorn_processes_as_capacity_input() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert f'"--workers", "{API_UVICORN_WORKERS}"' in dockerfile_text
    assert '"--log-config", "null"' not in dockerfile_text
    assert '"--loop", "uvloop"' not in dockerfile_text
    assert "1 x 4 x 5" in dockerfile_text


def test_capacity_guard_reads_worker_count_from_dockerfile(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text('CMD ["uvicorn", "main:app", "--workers", "7"]', encoding="utf-8")

    assert _read_api_uvicorn_workers(dockerfile) == 7


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("API_UVICORN_WORKERS", "3"),
        ("API_DATABASE_POOL_SIZE", "4"),
        ("CELERY_DATABASE_POOL_SIZE", "2"),
    ],
)
def test_capacity_plan_does_not_accept_removed_topology_override_contract(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    plan = build_capacity_plan(services={"api", "celery"}, scales={})

    assert plan.api_processes == API_UVICORN_WORKERS
    assert plan.api_pool_size == API_DATABASE_POOL_SIZE
    assert plan.celery_pool_size == CELERY_DATABASE_POOL_SIZE


def test_capacity_plan_uses_only_actual_docker_process_and_pool_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("API_UVICORN_WORKERS", "API_DATABASE_POOL_SIZE", "CELERY_DATABASE_POOL_SIZE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_REPLICAS", "1")
    monkeypatch.setenv("CELERY_WORKER_REPLICAS", "4")
    monkeypatch.setenv("CELERY_CONCURRENCY", "4")

    plan = build_capacity_plan(services={"api", "celery"}, scales={})

    assert plan.api_processes == 4
    assert plan.api_pool_size == 5
    assert plan.celery_pool_size == 1
    assert calculate_capacity(plan, max_connections=100).application_connections == 36


def test_capacity_plan_fixes_fulfillment_worker_to_one_replica_and_rejects_removed_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WMS_FULFILLMENT_CELERY_REPLICAS", raising=False)

    plan = build_capacity_plan(services={"celery-wms-fulfillment"}, scales={})

    assert plan.celery_replicas == 1

    monkeypatch.setenv("WMS_FULFILLMENT_CELERY_REPLICAS", "2")
    with pytest.raises(CapacityViolation, match=r"WMS_FULFILLMENT_CELERY_REPLICAS.*removed"):
        build_capacity_plan(services={"celery-wms-fulfillment"}, scales={})


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


@pytest.mark.parametrize("scale", [["api=3"], ["celery=10"]])
def test_scale_requires_complete_api_and_celery_target_topology(scale: list[str]) -> None:
    with pytest.raises(CapacityViolation, match=r"complete.*api.*celery"):
        _parse_scale(scale)


def test_scale_calculates_complete_api_then_worker_target_without_prior_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CELERY_CONCURRENCY", raising=False)
    scales = _parse_scale(["api=3", "celery=10"])

    plan = build_capacity_plan(services={"api", "celery"}, scales=scales)
    result = calculate_capacity(plan, max_connections=200)

    assert scales == {"api": 3, "celery": 10}
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
        ["api=1", "celery=4"],
        ["--scale=api=1", "--scale=celery=4"],
        ["--scale", "api=1", "--scale", "celery=4"],
    ],
)
def test_shell_scale_validation_accepts_all_documented_complete_forms(arguments: list[str]) -> None:
    completed = _run_shell_scale_validation(arguments)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("arguments", [["--scale", "api=1"], ["--scale", "celery=4"]])
def test_shell_scale_validation_rejects_each_incomplete_target(arguments: list[str]) -> None:
    completed = _run_shell_scale_validation(arguments)

    assert completed.returncode != 0
    assert "api=<n> celery=<n>" in completed.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ["api=1", "celery=4", "celery-wms-fulfillment=2"],
        ["--scale=api=1", "--scale=celery=4", "--scale=celery-wms-fulfillment=2"],
        ["--scale", "api=1", "--scale", "celery=4", "--scale", "celery-wms-fulfillment=2"],
    ],
)
def test_shell_scale_validation_rejects_fulfillment_replica_target(arguments: list[str]) -> None:
    completed = _run_shell_scale_validation(arguments)

    assert completed.returncode != 0
    assert "celery-wms-fulfillment" in completed.stdout
