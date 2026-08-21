from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "scripts" / "validate_test_deploy_source_diff.sh"


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git executable not found")
    return executable


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    local_variables = subprocess.run(
        [_git_executable(), "rev-parse", "--local-env-vars"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for variable in local_variables:
        environment.pop(variable, None)
    return environment


def _commit(repository: Path, path: str, content: str) -> str:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git_executable = _git_executable()
    environment = _git_environment()
    subprocess.run([git_executable, "add", path], cwd=repository, env=environment, check=True)
    subprocess.run(
        [
            git_executable,
            "-c",
            "user.name=WES Test",
            "-c",
            "user.email=wes-test@example.com",
            "commit",
            "-qm",
            path,
        ],
        cwd=repository,
        env=environment,
        check=True,
    )
    return subprocess.run(
        [git_executable, "rev-parse", "HEAD"],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run([_git_executable(), "init", "-q"], cwd=repository, env=_git_environment(), check=True)
    base = _commit(repository, "src/app/runtime.py", "runtime-v1\n")
    return repository, base


def _validate(repository: Path, runtime_commit: str, deploy_commit: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(VALIDATOR), runtime_commit, deploy_commit],
        cwd=repository,
        env=_git_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_deploy_source_accepts_a_descendant_with_only_delivery_and_verification_changes(
    repository: tuple[Path, str],
) -> None:
    worktree, runtime_commit = repository
    _commit(worktree, "Jenkinsfile.backend-ci", "pipeline-v2\n")
    _commit(worktree, "Jenkinsfile.test-deploy", "pipeline-v2\n")
    _commit(worktree, "scripts/classify_runtime_inbox_acceptance.py", "classifier-v2\n")
    _commit(
        worktree,
        "scripts/run_runtime_inbox_postgresql_acceptance.py",
        "runner-v2\n",
    )
    _commit(
        worktree,
        "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh",
        "runner-ci-v2\n",
    )
    _commit(worktree, "tests/deployment/test_cutover.py", "test-v2\n")
    _commit(worktree, "docs/architecture/heavy-test-impact.toml", "mapping-v2\n")
    _commit(worktree, "tests/scripts/test_select_heavy_tests.py", "selector-test-v2\n")
    _commit(
        worktree,
        "tests/scripts/test_select_heavy_tests_regression_2.py",
        "mapping-test-v2\n",
    )
    deploy_commit = _commit(worktree, "docs/devops/test deploy.md", "runbook-v2\n")

    result = _validate(worktree, runtime_commit, deploy_commit)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "TEST_DEPLOY_SOURCE_DIFF=delivery-only"


def test_deploy_source_rejects_runtime_changes_even_when_the_deploy_commit_is_a_descendant(
    repository: tuple[Path, str],
) -> None:
    worktree, runtime_commit = repository
    deploy_commit = _commit(worktree, "src/app/runtime.py", "runtime-v2\n")

    result = _validate(worktree, runtime_commit, deploy_commit)

    assert result.returncode == 2
    assert "src/app/runtime.py" in result.stderr


def test_deploy_source_rejects_a_commit_that_is_not_a_descendant_of_the_runtime_commit(
    repository: tuple[Path, str],
) -> None:
    worktree, base_commit = repository
    runtime_commit = _commit(worktree, "src/app/runtime.py", "runtime-v2\n")
    subprocess.run(
        [_git_executable(), "checkout", "-qb", "other", base_commit],
        cwd=worktree,
        env=_git_environment(),
        check=True,
    )
    unrelated_commit = _commit(worktree, "Jenkinsfile.test-deploy", "unrelated\n")

    result = _validate(worktree, runtime_commit, unrelated_commit)

    assert result.returncode == 2
    assert "必须是后端运行提交的后继" in result.stderr
