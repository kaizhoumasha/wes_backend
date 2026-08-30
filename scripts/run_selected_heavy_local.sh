#!/usr/bin/env bash

set -euo pipefail
set -m

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.test"
BASE_COMPOSE_FILE="$REPO_ROOT/docker-compose.ci-heavy.yml"
LOCAL_COMPOSE_FILE="$REPO_ROOT/docker-compose.ci-heavy.local.yml"
REPORT_DIR="$REPO_ROOT/reports"
PROJECT_NAME="wes-heavy-local-$$"
JUNIT_FILE="$REPORT_DIR/heavy-local-${PROJECT_NAME}.xml"
SERVICES_STARTED=0
ACTIVE_CHILD_PID=""
COMMAND_OUTPUT=""

if [[ $# -eq 0 ]]; then
    echo "用法: $0 --scope unstaged|staged，或 $0 --base <git-ref>" >&2
    exit 2
fi

TEMP_DIR="$(mktemp -d)"
MANIFEST_FILE="$TEMP_DIR/heavy-tests.txt"

COMPOSE=(
    docker compose
    --env-file "$ENV_FILE"
    -f "$BASE_COMPOSE_FILE"
    -f "$LOCAL_COMPOSE_FILE"
    --project-name "$PROJECT_NAME"
)

run_foreground() {
    local child_status=0
    "$@" </dev/null &
    ACTIVE_CHILD_PID=$!
    wait "$ACTIVE_CHILD_PID" || child_status=$?
    ACTIVE_CHILD_PID=""
    return "$child_status"
}

capture_output() {
    run_foreground "$@" > "$TEMP_DIR/command-output.txt"
    COMMAND_OUTPUT="$(<"$TEMP_DIR/command-output.txt")"
    COMMAND_OUTPUT="${COMMAND_OUTPUT//$'\r'/}"
}

cleanup() {
    local exit_code=$?
    trap - EXIT
    trap '' INT TERM
    if [[ "$SERVICES_STARTED" == "1" ]]; then
        if ! "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1; then
            echo "HEAVY 临时服务清理失败，请检查 Compose 项目: $PROJECT_NAME" >&2
            if [[ "$exit_code" == "0" ]]; then
                exit_code=1
            fi
        fi
    fi
    rm -rf "$TEMP_DIR"
    exit "$exit_code"
}

handle_signal() {
    local signal_name=$1
    local exit_code=$2
    trap '' INT TERM
    if [[ -n "$ACTIVE_CHILD_PID" ]] && kill -0 "$ACTIVE_CHILD_PID" 2>/dev/null; then
        kill "-$signal_name" -- "-$ACTIVE_CHILD_PID" 2>/dev/null || true
        wait "$ACTIVE_CHILD_PID" 2>/dev/null || true
    fi
    exit "$exit_code"
}

trap cleanup EXIT
trap 'handle_signal INT 130' INT
trap 'handle_signal TERM 143' TERM

cd "$REPO_ROOT"
mkdir -p "$REPORT_DIR"

run_foreground uv run scripts/select_heavy_tests.py "$@" > "$MANIFEST_FILE"
if [[ ! -s "$MANIFEST_FILE" ]]; then
    echo "当前差异未选择核心 HEAVY 测试。"
    exit 0
fi

SERVICES_STARTED=1
run_foreground "${COMPOSE[@]}" up -d --wait

capture_output "${COMPOSE[@]}" port db 5432
POSTGRES_PORT="${COMMAND_OUTPUT##*:}"
capture_output "${COMPOSE[@]}" port redis 6379
REDIS_PORT="${COMMAND_OUTPUT##*:}"
capture_output "${COMPOSE[@]}" exec -T db printenv POSTGRES_USER
POSTGRES_USER="$COMMAND_OUTPUT"
capture_output "${COMPOSE[@]}" exec -T db printenv POSTGRES_PASSWORD
POSTGRES_PASSWORD="$COMMAND_OUTPUT"
capture_output "${COMPOSE[@]}" exec -T db printenv POSTGRES_DB
POSTGRES_DB="$COMMAND_OUTPUT"
capture_output "${COMPOSE[@]}" exec -T redis printenv REDIS_PASSWORD
REDIS_PASSWORD="$COMMAND_OUTPUT"

export RUN_WORKLINE_INTEGRATION=1
export INTEGRATION_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
export INTEGRATION_REDIS_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:${REDIS_PORT}/15"
export ALEMBIC_DATABASE_URL="$INTEGRATION_DATABASE_URL"

run_foreground uv run alembic upgrade head
run_foreground uv run scripts/run_selected_heavy_tests.py "$MANIFEST_FILE" "$JUNIT_FILE"
