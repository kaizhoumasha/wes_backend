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
  runtime-toggle-release
            Run only runtime toggle release gate.
  runtime-evidence-readiness
            Run only runtime evidence readiness gate.
  workline-restructuring-readiness
            Run only WorkLine restructuring technical-scope readiness gate.
  business-legacy-absence
            Run only business legacy absence final gate.
  process-naming
            Run only active process naming guardrail.
  architecture  Run only architecture guardrails.
  import-linter  Run only import-linter capability-isolation contract check.

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

if command -v git >/dev/null 2>&1; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
else
    REPO_ROOT=""
fi

if [[ -z "$REPO_ROOT" && "$CI_MODE" == "true" ]]; then
    # CI 测试镜像默认不携带 .git 元数据，回退到脚本所在仓库目录。
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [[ -z "$REPO_ROOT" ]]; then
    # CI 镜像和源码包场景可能没有 .git 元数据；脚本仍应能从自身位置定位仓库。
    script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [[ -f "$script_root/pyproject.toml" ]]; then
        REPO_ROOT="$script_root"
    fi
fi

if [[ -z "$REPO_ROOT" ]]; then
    echo "Unable to determine repository root. Run inside a git checkout or pass --ci in containerized CI." >&2
    exit 2
fi

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

run_runtime_toggle_release_gate() {
    log_step "runtime-toggle" "check_runtime_toggle_release_gate.py"
    run_tool python scripts/check_runtime_toggle_release_gate.py
}

run_runtime_evidence_readiness_gate() {
    log_step "runtime-evidence-readiness" "check_runtime_evidence_readiness_gate.py"
    run_tool python scripts/check_runtime_evidence_readiness_gate.py
}

run_workline_restructuring_readiness_gate() {
    log_step "workline-restructuring-readiness" "check_workline_restructuring_readiness_gate.py --scope technical"
    run_tool python scripts/check_workline_restructuring_readiness_gate.py --scope technical
}

run_business_legacy_absence_gate() {
    log_step "business-legacy-absence" "check_business_legacy_absence_gate.py --mode final"
    run_tool python scripts/check_business_legacy_absence_gate.py --mode final
}

run_process_naming_guardrail() {
    log_step "process-naming" "pytest tests/architecture/test_process_naming_guardrail.py -q"
    run_tool pytest tests/architecture/test_process_naming_guardrail.py -q
}

run_architecture_check() {
    # 默认 enforced，确保每次 commit 触发 stable architecture guardrails。
    # 允许 ARCHITECTURE_GUARDRAIL_MODE 环境变量覆盖 (测试/回滚场景)。
    local mode="${ARCHITECTURE_GUARDRAIL_MODE:-enforced}"
    log_step "architecture" "architecture-guardrails.sh --mode $mode"
    bash "$REPO_ROOT/scripts/architecture-guardrails.sh" --mode "$mode"
}

run_import_linter_check() {
    log_step "import-linter" "import-linter-check.sh (capability-isolation contract)"
    bash "$REPO_ROOT/scripts/import-linter-check.sh"
}

run_test_topology_check() {
    log_step "tests" "pytest tests/architecture/test_test_suite_topology_guardrail.py -q"
    run_tool pytest tests/architecture/test_test_suite_topology_guardrail.py -q
}

run_quality_profile() {
    run_format_check
    run_lint_check
    run_security_check
    run_runtime_toggle_release_gate
    run_runtime_evidence_readiness_gate
    run_workline_restructuring_readiness_gate
    run_business_legacy_absence_gate
    run_process_naming_guardrail
    run_import_linter_check
    run_architecture_check
    run_test_topology_check
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
        runtime-toggle-release)
            run_runtime_toggle_release_gate
            ;;
        runtime-evidence-readiness)
            run_runtime_evidence_readiness_gate
            ;;
        workline-restructuring-readiness)
            run_workline_restructuring_readiness_gate
            ;;
        business-legacy-absence)
            run_business_legacy_absence_gate
            ;;
        process-naming)
            run_process_naming_guardrail
            ;;
        architecture)
            run_architecture_check
            ;;
        import-linter)
            run_import_linter_check
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
