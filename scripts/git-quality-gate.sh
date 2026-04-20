#!/usr/bin/env bash

set -euo pipefail

PROFILE="quality"
CHECK=""
BANDIT_JSON=""
CI_MODE="false"

usage() {
    cat <<'EOF'
Usage: scripts/git-quality-gate.sh [--profile PROFILE] [--check CHECK] [--bandit-json PATH] [--ci]

Profiles:
  quality   Run Ruff format check, Ruff lint, and Bandit security scan.
  ci-smoke  Run the quality profile plus API signature smoke tests.
  full      Run the quality profile plus the full pytest suite.

Checks:
  format    Run only Ruff format check.
  lint      Run only Ruff lint.
  security  Run only Bandit security scan.

Examples:
  ./scripts/git-quality-gate.sh
  ./scripts/git-quality-gate.sh --profile ci-smoke
  ./scripts/git-quality-gate.sh --check lint
  ./scripts/git-quality-gate.sh --profile quality --bandit-json reports/bandit-report.json --ci
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            PROFILE="${2:-}"
            shift 2
            ;;
        --check)
            CHECK="${2:-}"
            shift 2
            ;;
        --bandit-json)
            BANDIT_JSON="${2:-}"
            shift 2
            ;;
        --ci)
            CI_MODE="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

log_step() {
    printf '\n[%s] %s\n' "$1" "$2"
}

run_tool() {
    if command -v uv >/dev/null 2>&1; then
        uv run "$@"
        return
    fi
    "$@"
}

run_format_check() {
    log_step "quality" "ruff format --check ."
    run_tool ruff format --check .
}

run_lint_check() {
    log_step "quality" "ruff check ."
    run_tool ruff check .
}

run_security_check() {
    if [[ -n "$BANDIT_JSON" ]]; then
        mkdir -p "$(dirname "$BANDIT_JSON")"
        log_step "quality" "bandit -r src/ -f json -o $BANDIT_JSON"
        run_tool bandit -r src/ -f json -o "$BANDIT_JSON"
    fi

    log_step "quality" "bandit -r src/ -f screen"
    run_tool bandit -r src/ -f screen
}

run_quality_profile() {
    run_format_check
    run_lint_check
    run_security_check
}

run_ci_smoke_profile() {
    run_quality_profile
    log_step "ci-smoke" "pytest tests/api/test_signature.py --capture=fd -v --tb=short"
    run_tool pytest tests/api/test_signature.py --capture=fd -v --tb=short
}

run_full_profile() {
    run_quality_profile
    log_step "full" "pytest tests/ --capture=fd -v --tb=short"
    run_tool pytest tests/ --capture=fd -v --tb=short
}

if [[ -n "$CHECK" ]]; then
    case "$CHECK" in
        format)
            run_format_check
            ;;
        lint)
            run_lint_check
            ;;
        security)
            run_security_check
            ;;
        *)
            echo "Unsupported check: $CHECK" >&2
            usage >&2
            exit 2
            ;;
    esac
else
    case "$PROFILE" in
        quality)
            run_quality_profile
            ;;
        ci-smoke)
            run_ci_smoke_profile
            ;;
        full)
            run_full_profile
            ;;
        *)
            echo "Unsupported profile: $PROFILE" >&2
            usage >&2
            exit 2
            ;;
    esac
fi

if [[ "$CI_MODE" != "true" && -z "$CHECK" ]]; then
    printf '\n[quality] Profile "%s" passed.\n' "$PROFILE"
fi
