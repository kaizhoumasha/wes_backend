"""生产与测试部署的最小 WMS target endpoint 合同。"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTBOUND_CONSUMERS = ("api", "celery", "celery-wms-fulfillment")


def _load_compose(filename: str) -> dict[str, object]:
    compose_text = (REPO_ROOT / filename).read_text(encoding="utf-8")
    return yaml.safe_load(compose_text.replace("!override", ""))


def _effective_environment(compose: dict[str, object], service_name: str) -> dict[str, str]:
    services = compose["services"]
    service = services[service_name]
    parent_name = service.get("extends", {}).get("service")
    parent = services[parent_name] if parent_name else {}
    return {**parent.get("environment", {}), **service.get("environment", {})}


def _target_readiness_preflight_source() -> str:
    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")
    function_start = pipeline.index("business_preflight() {")
    source_start_marker = "candidate_backend_python -c '\n"
    source_start = pipeline.index(source_start_marker, function_start) + len(source_start_marker)
    source_stop = pipeline.index("\n' || return 1", source_start)
    return textwrap.dedent(pipeline[source_start:source_stop])


def test_production_and_test_deploy_share_only_minimal_target_endpoint_inputs() -> None:
    for filename in ("docker-compose.deploy.yml", "docker-compose.test-deploy.yml"):
        compose = _load_compose(filename)
        for service_name in OUTBOUND_CONSUMERS:
            environment = _effective_environment(compose, service_name)
            assert environment["WMS_BASE_URL"] == "${WMS_BASE_URL}"
            assert environment["TRANSPORT_SUBMIT_PATH"] == "${TRANSPORT_SUBMIT_PATH}"
        assert "WMS_BASE_URL" not in compose["services"]["celery_beat"].get("environment", {})


def test_prod_env_exposes_endpoint_and_documents_fixed_none_security() -> None:
    env_lines = (REPO_ROOT / ".env.prod").read_text(encoding="utf-8").splitlines()

    assert "WMS_BASE_URL=" in env_lines
    assert "TRANSPORT_SUBMIT_PATH=/api/v1/wes/transport-requests" in env_lines
    assert any("isolated LAN + NONE" in line for line in env_lines)


def test_test_deploy_pipeline_digests_env_and_runs_target_readiness_before_maintenance() -> None:
    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")
    deploy_body = pipeline.split("stage('Deploy Test Environment')", maxsplit=1)[1].split("post {", maxsplit=1)[0]

    preflight_start = deploy_body.index('echo "🔎 在维护态前校验 WMS target client/queue readiness"')
    maintenance_start = deploy_body.index('echo "🔒 进入维护态并停止旧应用容器"')
    business_preflight = deploy_body.index("business_preflight || abort_pre_cutover wms-target-readiness-preflight")

    assert 'runtime = [item("runtime/.env", sys.argv[2])]' in deploy_body
    assert "build_wms_client(base_url=settings.WMS_BASE_URL, timeout_seconds=10.0)" in deploy_body
    assert "WMS_TARGET_PREFLIGHT=valid" in deploy_body
    assert "wms-fulfillment" in deploy_body
    assert preflight_start < business_preflight < maintenance_start


def test_test_deploy_target_readiness_preflight_loads_only_target_wms_tasks_from_fresh_app() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _target_readiness_preflight_source()],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.splitlines() == ["WMS_TARGET_PREFLIGHT=valid"]
