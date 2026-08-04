"""质量门禁脚本与 Jenkins 的强制测试所有权接线。"""

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


def test_retired_root_jenkinsfile_is_absent() -> None:
    assert not (REPO_ROOT / "Jenkinsfile").exists()
