"""质量门禁脚本与 Jenkins 的强制测试所有权接线。"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _isolated_git_environment(git_executable: str) -> dict[str, str]:
    environment = os.environ.copy()
    local_variables = subprocess.run(
        [git_executable, "rev-parse", "--local-env-vars"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for variable in local_variables:
        environment.pop(variable, None)
    return environment


def _run_pre_commit_hook(
    tmp_path: Path,
    staged_files: str | dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable not found")

    environment = _isolated_git_environment(git_executable)
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    subprocess.run([git_executable, "init", "-q"], cwd=repository, env=environment, check=True)

    files = {staged_files: "content\n"} if isinstance(staged_files, str) else staged_files
    for staged_path, content in files.items():
        target = repository / staged_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run([git_executable, "add", *files], cwd=repository, env=environment, check=True)

    gate_log = repository / "gate.log"
    quality_gate = scripts / "git-quality-gate.sh"
    quality_gate.write_text('#!/bin/sh\nprintf "quality %s\\n" "$*" >> "$GATE_LOG"\n', encoding="utf-8")
    quality_gate.chmod(0o755)

    environment["GATE_LOG"] = str(gate_log)
    result = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / ".githooks" / "pre-commit")],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    lines = gate_log.read_text(encoding="utf-8").splitlines() if gate_log.exists() else []
    return result, lines


def _run_release_metadata_gate(
    tmp_path: Path,
    *,
    version: str,
    readme_version: str,
    changelog_version: str,
) -> subprocess.CompletedProcess[str]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable not found")

    environment = _isolated_git_environment(git_executable)
    repository = tmp_path / "release-repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    subprocess.run([git_executable, "init", "-q"], cwd=repository, env=environment, check=True)
    shutil.copy(REPO_ROOT / "scripts" / "git-quality-gate.sh", scripts / "git-quality-gate.sh")

    release_files = {
        "VERSION": f"{version}\n",
        "README.md": f"**Version**: {readme_version}\n",
        "CHANGELOG.md": f"## [{changelog_version}] - 2026-08-16\n",
    }
    for path, content in release_files.items():
        (repository / path).write_text(content, encoding="utf-8")
    subprocess.run([git_executable, "add", *release_files], cwd=repository, env=environment, check=True)

    return subprocess.run(
        ["/bin/bash", "scripts/git-quality-gate.sh", "--check", "release-metadata"],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


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
        "stage('Compose Contracts')", maxsplit=1
    )[0]

    assert '-v "$WORKSPACE/.git:/app/.git:ro"' in quality_body
    assert "git config --global --add safe.directory /app" in quality_body


def test_jenkins_parallelizes_independent_quality_and_compose_gates() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")
    verification = jenkinsfile.split("stage('Build CI Image')", maxsplit=1)[1].split(
        "stage('Mock Image Contracts')", maxsplit=1
    )[0]

    assert "stage('Verification')" in verification
    parallel_position = verification.index("parallel {")
    for stage_name in ("Quality Gate", "Compose Contracts"):
        assert verification.index(f"stage('{stage_name}')") > parallel_position
    assert "RuntimeInbox PostgreSQL Acceptance" not in verification


def test_jenkins_passes_selector_manifest_to_general_heavy_runner_without_legacy_filter() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")
    heavy_stage = jenkinsfile.split("stage('HEAVY Required')", maxsplit=1)[1].split(
        "stage('Build Runtime Image')", maxsplit=1
    )[0]
    assert "cp reports/heavy-tests.selected.txt reports/heavy-tests.txt" in heavy_stage
    assert "scripts/run_selected_heavy_tests.py /artifacts/heavy-tests.txt /reports/heavy-required.xml" in heavy_stage
    assert "runtime-inbox-acceptance-owned.txt" not in heavy_stage
    assert "grep -Fvx -f" not in heavy_stage
    assert "runtime_inbox" not in heavy_stage.lower()


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


def test_pre_commit_uses_docs_gate_for_human_readable_document(tmp_path: Path) -> None:
    result, gate_log = _run_pre_commit_hook(tmp_path, "docs/plan.md")

    assert result.returncode == 0, result.stderr
    assert "Running documentation-only gate" in result.stdout
    assert gate_log == []


@pytest.mark.parametrize(
    ("staged_path", "uses_docs_gate"),
    [
        ("docs/note.txt", True),
        ("src/runtime/contract.txt", False),
        ("scripts/input.txt", False),
        ("tests/integration/fixture.txt", False),
        ("requirements.txt", False),
    ],
)
def test_pre_commit_routes_txt_by_path(tmp_path: Path, staged_path: str, *, uses_docs_gate: bool) -> None:
    result, gate_log = _run_pre_commit_hook(tmp_path, staged_path)

    assert result.returncode == 0, result.stderr
    if uses_docs_gate:
        assert "Running documentation-only gate" in result.stdout
        assert gate_log == []
    else:
        assert gate_log == ["quality --profile quality"]


def test_pre_commit_keeps_quality_gate_for_machine_readable_contract(tmp_path: Path) -> None:
    result, gate_log = _run_pre_commit_hook(tmp_path, "docs/architecture/contract.toml")

    assert result.returncode == 0, result.stderr
    assert gate_log == ["quality --profile quality"]


def test_pre_commit_uses_release_metadata_gate_for_version_and_human_documents(tmp_path: Path) -> None:
    result, gate_log = _run_pre_commit_hook(
        tmp_path,
        {
            "VERSION": "1.2.3.4\n",
            "README.md": "**Version**: 1.2.3.4\n",
            "CHANGELOG.md": "## [1.2.3.4] - 2026-08-16\n",
            "TODOS.md": "# TODO\n",
            "docs/note.txt": "release note\n",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "Running release-metadata gate" in result.stdout
    assert gate_log == ["quality --check release-metadata"]


def test_pre_commit_keeps_quality_gate_when_release_includes_machine_readable_config(tmp_path: Path) -> None:
    result, gate_log = _run_pre_commit_hook(
        tmp_path,
        {
            "VERSION": "1.2.3.4\n",
            "README.md": "**Version**: 1.2.3.4\n",
            "CHANGELOG.md": "## [1.2.3.4] - 2026-08-16\n",
            "pyproject.toml": '[project]\nname = "example"\n',
        },
    )

    assert result.returncode == 0, result.stderr
    assert gate_log == ["quality --profile quality"]


def test_release_metadata_gate_accepts_consistent_four_part_version(tmp_path: Path) -> None:
    result = _run_release_metadata_gate(
        tmp_path,
        version="1.2.3.4",
        readme_version="1.2.3.4",
        changelog_version="1.2.3.4",
    )

    assert result.returncode == 0, result.stderr


def test_release_metadata_gate_rejects_inconsistent_readme_version(tmp_path: Path) -> None:
    result = _run_release_metadata_gate(
        tmp_path,
        version="1.2.3.4",
        readme_version="1.2.3.3",
        changelog_version="1.2.3.4",
    )

    assert result.returncode == 1
    assert "README.md does not declare VERSION 1.2.3.4" in result.stderr


def test_retired_root_jenkinsfile_is_absent() -> None:
    assert not (REPO_ROOT / "Jenkinsfile").exists()
