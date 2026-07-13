"""RuntimeInbox PostgreSQL CI 验收入口合同。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_runtime_inbox_postgresql_acceptance import (
    REQUIRED_FREE_CONNECTION_SLOTS,
    AcceptanceFailure,
    run_acceptance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = REPO_ROOT / "Jenkinsfile.backend-ci"
LIFECYCLE_SCRIPT = REPO_ROOT / "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh"
RUNNER = REPO_ROOT / "scripts/run_runtime_inbox_postgresql_acceptance.py"


def test_ci_uses_isolated_postgresql_and_archives_contract_artifacts():
    jenkins = JENKINSFILE.read_text(encoding="utf-8")
    lifecycle = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert "stage('RuntimeInbox PostgreSQL Acceptance')" in jenkins
    assert "run_runtime_inbox_postgresql_acceptance_ci.sh run" in jenkins
    assert "run_runtime_inbox_postgresql_acceptance_ci.sh cleanup" in jenkins
    assert "reports/runtime-inbox-acceptance/junit/*.xml" in jenkins
    assert "reports/runtime-inbox-acceptance/**/*" in jenkins
    assert "allowEmptyArchive: true" in jenkins
    assert "fingerprint: true" in jenkins
    assert "timescale/timescaledb:latest-pg17" in lifecycle
    assert "docker network create" in lifecycle
    assert "docker volume create" in lifecycle
    assert "ALTER ROLE runtime_acceptance CREATEDB" in lifecycle
    assert "max_connections=100" in lifecycle
    assert "INTEGRATION_DATABASE_URL=postgresql://runtime_acceptance:" in lifecycle
    assert "INTEGRATION_DATABASE_SAFE_HOSTS=runtime-inbox-postgres" in lifecycle
    assert "POSTGRES_HOST=runtime-inbox-postgres" in lifecycle
    assert '--env-file "${WORKSPACE}/.env.test"' in lifecycle
    assert "\n    -p " not in lifecycle
    assert "trap cleanup EXIT" in lifecycle
    assert 'docker rm -f "${ACCEPTANCE_CONTAINER}"' in lifecycle
    assert 'docker rm -f "${POSTGRES_CONTAINER}"' in lifecycle
    assert 'docker network rm "${POSTGRES_NETWORK}"' in lifecycle
    assert 'docker volume rm "${POSTGRES_VOLUME}"' in lifecycle
    assert "tests/integration/test_runtime_inbox_migration_postgresql.py" in runner
    assert "tests/integration/test_runtime_inbox_processing_postgresql.py" in runner
    assert "test_claim_crash_recovers_with_new_owner_and_rejects_old_fence" in runner
    assert "test_writeback_crash_rolls_back_effects_before_reprocessing_once" in runner
    assert "tests/load/test_runtime_inbox_claim_benchmark.py" in runner
    assert "validate_runtime_inbox_benchmark_evidence" in runner
    assert 'source_environment.get("GIT_COMMIT") != expected_commit' in runner


def test_acceptance_runner_runs_required_suites_in_order_and_validates_evidence(tmp_path: Path):
    events: list[str] = []

    def preflight(_environment, required_free_slots):
        events.append("preflight")
        assert required_free_slots == REQUIRED_FREE_CONNECTION_SLOTS == 5

    def execute(command, environment):
        events.append(command.name)
        assert command.argv[:3] == ("uv", "run", "--no-sync")
        if command.name == "benchmark":
            Path(environment["RUNTIME_INBOX_BENCHMARK_EVIDENCE"]).write_text("{}", encoding="utf-8")

    def validate(path, expected_commit):
        events.append("validator")
        assert path == tmp_path / "runtime-inbox-claim-benchmark.json"
        assert expected_commit == "a" * 40

    run_acceptance(
        tmp_path,
        "a" * 40,
        environment={
            "GIT_COMMIT": "a" * 40,
            "INTEGRATION_DATABASE_URL": "postgresql://user:secret@runtime-inbox-postgres/postgres",
        },
        preflight_check=preflight,
        executor=execute,
        evidence_validator=validate,
    )

    assert events == [
        "preflight",
        "migration_matrix",
        "processing_integration",
        "crash_after_claim",
        "crash_before_terminal",
        "benchmark",
        "validator",
    ]
    diagnostic = json.loads((tmp_path / "diagnostic.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "passed"


def test_acceptance_runner_rejects_commit_mismatch_before_postgresql_preflight(tmp_path: Path):
    preflight_called = False

    def preflight(_environment, _required_free_slots):
        nonlocal preflight_called
        preflight_called = True

    with pytest.raises(AcceptanceFailure, match="GIT_COMMIT does not match"):
        run_acceptance(
            tmp_path,
            "a" * 40,
            environment={"GIT_COMMIT": "b" * 40},
            preflight_check=preflight,
        )

    assert preflight_called is False


@pytest.mark.parametrize("failure_at", ["preflight", "processing_integration", "validator"])
def test_acceptance_runner_returns_failure_and_keeps_redacted_diagnostic(tmp_path: Path, failure_at: str):
    secret_url = "postgresql://user:top-secret@runtime-inbox-postgres/postgres"

    def preflight(_environment, _required_free_slots):
        if failure_at == "preflight":
            raise RuntimeError(f"cannot connect: {secret_url}")

    def execute(command, environment):
        if command.name == "benchmark":
            Path(environment["RUNTIME_INBOX_BENCHMARK_EVIDENCE"]).write_text("{}", encoding="utf-8")
        if command.name == failure_at:
            raise RuntimeError(f"suite failed: {secret_url}")

    def validate(_path, _expected_commit):
        if failure_at == "validator":
            raise RuntimeError(f"invalid evidence: {secret_url}")

    with pytest.raises(AcceptanceFailure):
        run_acceptance(
            tmp_path,
            "b" * 40,
            environment={"GIT_COMMIT": "b" * 40, "INTEGRATION_DATABASE_URL": secret_url},
            preflight_check=preflight,
            executor=execute,
            evidence_validator=validate,
        )

    diagnostic = (tmp_path / "diagnostic.json").read_text(encoding="utf-8")
    assert "top-secret" not in diagnostic
    assert secret_url not in diagnostic
    assert "postgresql://***@runtime-inbox-postgres/postgres" in diagnostic
