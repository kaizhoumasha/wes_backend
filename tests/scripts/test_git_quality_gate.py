"""质量门禁脚本与 Jenkins 的强制测试所有权接线。"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_test_topology_check_runs_core_secondary_package_ownership_guardrails() -> None:
    quality_gate = (REPO_ROOT / "scripts/git-quality-gate.sh").read_text(encoding="utf-8")

    assert "tests/architecture/test_suite_topology_guardrail.py" in quality_gate
    assert "tests/architecture/test_core_plugin_test_ownership_guardrail.py" in quality_gate
    assert "test-topology)" in quality_gate


def test_jenkins_runs_canonical_quality_profile_without_partial_duplicates() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")

    assert (
        "./scripts/git-quality-gate.sh --profile quality --bandit-json /app/reports/bandit-report.json --ci"
    ) in jenkinsfile
    assert "./scripts/git-quality-gate.sh --check test-topology --ci" not in jenkinsfile
    assert "pytest tests/ -v --tb=short" not in jenkinsfile
    assert "pytest tests/api/test_signature.py" not in jenkinsfile
    assert "uv run --no-sync pytest tests/scripts -q" not in jenkinsfile


def test_jenkins_quality_gate_mounts_git_metadata_read_only() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")
    quality_body = jenkinsfile.split("stage('Quality Gate')", maxsplit=1)[1].split(
        "stage('RuntimeInbox PostgreSQL Acceptance')", maxsplit=1
    )[0]

    assert '-v "$WORKSPACE/.git:/app/.git:ro"' in quality_body
    assert "git config --global --add safe.directory /app" in quality_body


def test_ci_architecture_check_disables_uv_sync_for_nested_guardrail(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_no_sync_log = tmp_path / "uv-no-sync.log"
    fake_bash = fake_bin / "bash"
    fake_bash.write_text(
        '#!/bin/sh\nprintf "%s\\n" "${UV_NO_SYNC:-}" > "$UV_NO_SYNC_LOG"\n',
        encoding="utf-8",
    )
    fake_bash.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "UV_NO_SYNC_LOG": str(uv_no_sync_log),
        }
    )

    result = subprocess.run(
        ["/bin/bash", "scripts/git-quality-gate.sh", "--check", "architecture", "--ci"],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert uv_no_sync_log.read_text(encoding="utf-8").strip() == "1"


def test_retired_root_jenkinsfile_is_absent() -> None:
    assert not (REPO_ROOT / "Jenkinsfile").exists()
