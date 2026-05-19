# Pre-Commit Quality Gate Design

**Date:** 2026-04-20

## Goal

Add a repo-managed pre-commit quality gate that catches the same code-quality failures Jenkins enforces, before code reaches CI.

## Scope

- Add one shared quality-gate script for local and CI usage
- Add one repo-managed `pre-commit` hook that calls the shared script
- Add one install script that sets `core.hooksPath` to the tracked hooks directory
- Update Jenkins quality stages to call the shared script instead of duplicating raw commands

## Non-Goals

- Fix the current unit test failures in `wes_backend-ci` job `36`
- Add slow tests to every local commit by default
- Introduce a third-party hook manager such as `pre-commit`

## Baseline

Jenkins job `36` shows that the quality gate is now green:

- `ruff format --check .`
- `ruff check .`
- `bandit -r src/`

The current pipeline failure moved to the unit-test stage. That means the local gate should focus on preventing quality regressions, while keeping heavier test profiles opt-in.

## Design

### Shared script

Create `scripts/git-quality-gate.sh` as the single entrypoint for local and CI quality checks.

Supported profiles:

- `quality`: `ruff format --check .` + `ruff check .` + `bandit -r src/`
- `ci-smoke`: `quality` + `pytest tests/api/test_signature.py --capture=fd -v --tb=short`
- `full`: `quality` + `pytest tests/ --capture=fd -v --tb=short`

Supported single-check modes for CI reuse:

- `format`: `ruff format --check .`
- `lint`: `ruff check .`
- `security`: `bandit -r src/`

The script should prefer `uv run ...` when `uv` is available, and fall back to direct executables inside CI containers where the tools are already on `PATH`.

### Repo-managed hook

Track `.githooks/pre-commit` in the repository. The hook should call the shared script with profile `quality` by default, while allowing developers to override the profile with `WES_GIT_QUALITY_PROFILE`.

### Hook installation

Create `scripts/install-git-hooks.sh` to set:

```bash
git config core.hooksPath .githooks
```

This keeps the repository portable across worktrees without requiring a global Git configuration change.

### Jenkins integration

Update `Jenkinsfile` and `Jenkinsfile.backend-ci` so that quality checks call the shared script instead of duplicating inline `ruff` and `bandit` commands. Jenkins should keep the existing parallel stage structure by invoking the script in single-check mode, and should still archive the Bandit JSON report through the script's `--bandit-json` option.

## Expected outcome

- Developers get a fast, automatic `pre-commit` gate for the exact quality failures that previously broke Jenkins
- Jenkins and local development share the same quality command contract
- Heavier verification remains available without forcing it into every commit
