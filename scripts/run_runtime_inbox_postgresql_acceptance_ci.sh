#!/usr/bin/env bash
set -Eeuo pipefail

# RuntimeInbox 正式验收只使用本次 build 的隔离 PG17，不发布宿主机端口。
SUFFIX="${BUILD_NUMBER:-local}-${CI_SHORT_COMMIT:-unknown}"
POSTGRES_CONTAINER="wes-runtime-inbox-pg-${SUFFIX}"
ACCEPTANCE_CONTAINER="wes-runtime-inbox-acceptance-${SUFFIX}"
POSTGRES_NETWORK="wes-runtime-inbox-net-${SUFFIX}"
POSTGRES_VOLUME="wes-runtime-inbox-data-${SUFFIX}"
POSTGRES_HOST="runtime-inbox-postgres"
RUNTIME_INBOX_DATABASE_TEMPLATE="wes_tmp_runtime_inbox_template"
REPORT_DIR="${WORKSPACE:-$(pwd)}/reports/runtime-inbox-acceptance"
ENV_FILE="${REPORT_DIR}/.acceptance.env"

cleanup() {
    docker rm -f "${ACCEPTANCE_CONTAINER}" >/dev/null 2>&1 || true
    docker rm -f "${POSTGRES_CONTAINER}" >/dev/null 2>&1 || true
    docker network rm "${POSTGRES_NETWORK}" >/dev/null 2>&1 || true
    docker volume rm "${POSTGRES_VOLUME}" >/dev/null 2>&1 || true
    rm -f "${ENV_FILE}"
}

if [[ "${1:-run}" == "cleanup" ]]; then
    cleanup
    exit 0
fi

: "${CI_COMMIT_SHA:?CI_COMMIT_SHA is required}"
: "${CI_IMAGE:?CI_IMAGE is required}"
: "${WORKSPACE:?WORKSPACE is required}"

mkdir -p "${WORKSPACE}/logs" "${REPORT_DIR}/logs" "${REPORT_DIR}/junit"
trap cleanup EXIT

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
umask 077
{
    printf 'POSTGRES_USER=runtime_acceptance\n'
    printf 'POSTGRES_PASSWORD=%s\n' "${POSTGRES_PASSWORD}"
    printf 'POSTGRES_DB=postgres\n'
} >"${ENV_FILE}"

docker network create "${POSTGRES_NETWORK}" >/dev/null
docker volume create "${POSTGRES_VOLUME}" >/dev/null
docker run -d --name "${POSTGRES_CONTAINER}" \
    --network "${POSTGRES_NETWORK}" --network-alias "${POSTGRES_HOST}" \
    --env-file "${ENV_FILE}" \
    -v "${POSTGRES_VOLUME}:/var/lib/postgresql/data" \
    --health-cmd='test "$(cat /proc/1/comm)" = postgres && pg_isready -U runtime_acceptance -d postgres' \
    --health-interval=2s --health-timeout=3s --health-retries=30 \
    timescale/timescaledb:latest-pg17 \
    postgres -c max_connections=100 >/dev/null

ready=false
for _attempt in $(seq 1 60); do
    if [[ "$(docker inspect --format='{{.State.Health.Status}}' "${POSTGRES_CONTAINER}")" == "healthy" ]]; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "${ready}" != "true" ]]; then
    docker logs "${POSTGRES_CONTAINER}" >"${REPORT_DIR}/logs/postgresql.log" 2>&1 || true
    echo "PostgreSQL 17 health check timed out" >&2
    exit 1
fi

docker exec "${POSTGRES_CONTAINER}" \
    psql -v ON_ERROR_STOP=1 -U runtime_acceptance -d postgres \
    -c 'ALTER ROLE runtime_acceptance CREATEDB' >/dev/null

# 用临时 env-file 避免密码出现在 shell argv/log；SAFE_HOSTS 必须与网络 alias 精确一致。
{
    printf 'INTEGRATION_DATABASE_URL=postgresql://runtime_acceptance:%s@runtime-inbox-postgres:5432/postgres\n' \
        "${POSTGRES_PASSWORD}"
    printf 'INTEGRATION_DATABASE_SAFE_HOSTS=runtime-inbox-postgres\n'
    printf 'RUNTIME_INBOX_DATABASE_TEMPLATE=%s\n' "${RUNTIME_INBOX_DATABASE_TEMPLATE}"
    printf 'ALEMBIC_DATABASE_URL=postgresql://runtime_acceptance:%s@runtime-inbox-postgres:5432/%s\n' \
        "${POSTGRES_PASSWORD}" "${RUNTIME_INBOX_DATABASE_TEMPLATE}"
    printf 'POSTGRES_HOST=runtime-inbox-postgres\n'
    printf 'POSTGRES_PORT=5432\n'
    printf 'GIT_COMMIT=%s\n' "${CI_COMMIT_SHA}"
    printf 'GIT_CONFIG_COUNT=1\n'
    printf 'GIT_CONFIG_KEY_0=safe.directory\n'
    printf 'GIT_CONFIG_VALUE_0=/workspace\n'
    printf 'UV_PROJECT_ENVIRONMENT=/app/.venv\n'
    printf 'PYTHONPATH=/workspace\n'
} >>"${ENV_FILE}"

echo "Preparing migrated RuntimeInbox database template"
docker exec "${POSTGRES_CONTAINER}" \
    createdb -U runtime_acceptance -T template0 "${RUNTIME_INBOX_DATABASE_TEMPLATE}"
docker run --rm \
    --network "${POSTGRES_NETWORK}" \
    --env-file "${ENV_FILE}" \
    --entrypoint /opt/venv/bin/alembic \
    "${CI_IMAGE}" upgrade head

set +e
docker run --rm --name "${ACCEPTANCE_CONTAINER}" \
    --network "${POSTGRES_NETWORK}" \
    --env-file "${WORKSPACE}/.env.test" \
    --env-file "${ENV_FILE}" \
    -v "${WORKSPACE}:/workspace:ro" \
    -v "${REPORT_DIR}/logs:/workspace/logs:rw" \
    -v "${WORKSPACE}/reports:/artifacts/reports:rw" \
    --workdir /workspace \
    "${CI_IMAGE}" \
    sh -ec '
        if [ -n "$(git status --porcelain)" ]; then
            echo "RuntimeInbox acceptance requires a clean full checkout" >&2
            exit 1
        fi
        exec uv run --no-sync python scripts/run_runtime_inbox_postgresql_acceptance.py \
            --output-dir /artifacts/reports/runtime-inbox-acceptance \
            --expected-commit "${GIT_COMMIT}"
    '
acceptance_status=$?
set -e

if [[ ${acceptance_status} -ne 0 ]]; then
    docker logs "${POSTGRES_CONTAINER}" >"${REPORT_DIR}/logs/postgresql.log" 2>&1 || true
fi
exit "${acceptance_status}"
