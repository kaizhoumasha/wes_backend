"""数据库连接容量与部署拓扑合同。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.capacity_guard import CapacityPlan, CapacityViolation, calculate_capacity

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
        assert api_env["DATABASE_POOL_SIZE"] == 5
        assert api_env["DATABASE_MAX_OVERFLOW"] == 0
        assert worker_env["DATABASE_RUNTIME_ROLE"] == "celery"
        assert worker_env["DATABASE_POOL_SIZE"] == 1
        assert worker_env["DATABASE_MAX_OVERFLOW"] == 0
        assert compose["services"]["celery_beat"]["environment"]["DATABASE_RUNTIME_ROLE"] == "cli"
        assert compose["services"]["flower"]["environment"]["DATABASE_RUNTIME_ROLE"] == "cli"

    test_compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    pytest_env = test_compose["services"]["pytest"]["environment"]
    assert pytest_env["DATABASE_RUNTIME_ROLE"] == "integration"
    assert pytest_env["DATABASE_POOL_SIZE"] == 1
    assert pytest_env["DATABASE_MAX_OVERFLOW"] == 0


def test_deployment_script_runs_live_guard_before_application_start_and_scale() -> None:
    script_text = (REPO_ROOT / "scripts/docker-deploy-simple.sh").read_text(encoding="utf-8")

    assert "celery_worker=8" not in script_text
    assert "capacity_guard.py" in script_text
    cmd_up_body = script_text.split("cmd_up()", maxsplit=1)[1].split("cmd_down()", maxsplit=1)[0]
    assert "--wait db redis" in cmd_up_body
    assert cmd_up_body.index("--wait db redis") < cmd_up_body.index("run_capacity_guard")
    assert "run_capacity_guard" in script_text


def test_dockerfile_keeps_four_uvicorn_processes_as_capacity_input() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '"--workers", "4"' in dockerfile_text
    assert "1 x 4 x 5" in dockerfile_text
