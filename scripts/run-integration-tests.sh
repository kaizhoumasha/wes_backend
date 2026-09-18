#!/usr/bin/env bash
# 本地开发环境的集成测试入口。
#
# 契约：
#   - RUN_WORKLINE_INTEGRATION=1 必须显式开启
#   - INTEGRATION_DATABASE_URL 必须指向本机 docker compose 起的 db 测试库
#   - 仅允许 localhost / 127.0.0.1 / db host，且数据库名带 test_ 前缀或 _test 后缀
#   - Redis 默认使用本机 redis（URL 可被 INTEGRATION_REDIS_URL 覆盖）
#   - CELERY_BROKER_URL 可显式覆盖 Celery broker
#
# 用法：
#   ./scripts/run-integration-tests.sh                                    # 跑全部 manual-picking 集成测试
#   ./scripts/run-integration-tests.sh tests/integration/wms_integration/outbound_picking/test_plan_activation_return_rack_postgresql.py
#   INTEGRATION_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_manual_picking \
#       RUN_WORKLINE_INTEGRATION=1 \
#       ./scripts/run-integration-tests.sh
#
# 前置条件：
#   docker compose up -d db redis
#   uv sync --dev --extra manual-picking
#   alembic upgrade head  # 用户需要手工执行（不要在脚本里自动执行，避免误连生产）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ "${RUN_WORKLINE_INTEGRATION:-0}" != "1" ]]; then
    echo "[run-integration-tests] RUN_WORKLINE_INTEGRATION must be set to 1 to enable integration tests" >&2
    echo "    export RUN_WORKLINE_INTEGRATION=1" >&2
    exit 1
fi

if [[ -z "${INTEGRATION_DATABASE_URL:-}" ]]; then
    echo "[run-integration-tests] INTEGRATION_DATABASE_URL is required; e.g." >&2
    echo "    export INTEGRATION_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_manual_picking" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "[run-integration-tests] uv is required" >&2
    exit 1
fi

# 显式确认目标数据库是 test 库，避免误用生产/共享 dev 库
db_host="$(python -c "from urllib.parse import urlparse; import os; print(urlparse(os.environ['INTEGRATION_DATABASE_URL']).hostname or '')")"
db_name="$(python -c "from urllib.parse import urlparse; import os; print(urlparse(os.environ['INTEGRATION_DATABASE_URL']).path.lstrip('/') or '')")"
case "${db_host}" in
    localhost|127.0.0.1|::1|db) ;;
    *) echo "[run-integration-tests] INTEGRATION_DATABASE_URL host '${db_host}' not in localhost/127.0.0.1/::1/db" >&2; exit 1 ;;
esac
case "${db_name}" in
    test_*|*_test|test) ;;
    *) echo "[run-integration-tests] INTEGRATION_DATABASE_URL database '${db_name}' is not a test database; rename to test_*" >&2; exit 1 ;;
esac

# 主动尝试连接 PG；若失败，给出明确指引，而不是让 pytest 后续报 authentication failed
probe_ok=0
if command -v pg_isready >/dev/null 2>&1; then
    pg_port="$(python -c "from urllib.parse import urlparse; import os; print(urlparse(os.environ['INTEGRATION_DATABASE_URL']).port or 5432)")"
    if pg_isready -h "${db_host}" -p "${pg_port}" -q; then
        probe_ok=1
    fi
else
    pg_port="$(python -c "from urllib.parse import urlparse; import os; print(urlparse(os.environ['INTEGRATION_DATABASE_URL']).port or 5432)")"
    if python -c "import socket, os; s=socket.socket(); s.settimeout(1.0); s.connect((os.environ.get('INTEGRATION_DATABASE_URL_DB_HOST', '${db_host}'), ${pg_port})); s.close()" 2>/dev/null; then
        probe_ok=1
    fi
fi
if [[ ${probe_ok} -eq 0 ]]; then
    echo "[run-integration-tests] PostgreSQL at ${db_host}:${pg_port} is not reachable." >&2
    echo "    Hint: docker compose up -d db redis" >&2
    exit 1
fi

# 主动认证并确认 schema 已应用到当前 head，避免后续 pytest 使用过期结构
if ! ALEMBIC_DATABASE_URL="${INTEGRATION_DATABASE_URL}" uv run alembic current --check-heads >/dev/null 2>&1; then
    echo "[run-integration-tests] alembic failed to authenticate against ${db_host}:${pg_port}/${db_name}" >&2
    echo "    Verify INTEGRATION_DATABASE_URL credentials and that the database exists." >&2
    exit 1
fi

# schema 不自动升级；用户需自行执行 alembic upgrade head

# 默认走 docker compose 起的 redis（如果用户没有显式覆盖）
if [[ -z "${INTEGRATION_REDIS_URL:-}" ]]; then
    redis_port="${REDIS_PORT:-6379}"
    if [[ -n "${REDIS_PASSWORD:-}" ]]; then
        export INTEGRATION_REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:${redis_port}/0"
    else
        export INTEGRATION_REDIS_URL="redis://localhost:${redis_port}/0"
    fi
fi
if [[ -z "${CELERY_BROKER_URL:-}" ]]; then
    if [[ -n "${REDIS_PASSWORD:-}" ]]; then
        export CELERY_BROKER_URL="redis://:${REDIS_PASSWORD}@localhost:${redis_port:-6379}/1"
    else
        export CELERY_BROKER_URL="redis://localhost:${redis_port:-6379}/1"
    fi
fi
if [[ -z "${CELERY_RESULT_BACKEND:-}" ]]; then
    if [[ -n "${REDIS_PASSWORD:-}" ]]; then
        export CELERY_RESULT_BACKEND="redis://:${REDIS_PASSWORD}@localhost:${redis_port:-6379}/2"
    else
        export CELERY_RESULT_BACKEND="redis://localhost:${redis_port:-6379}/2"
    fi
fi

pytest_args=("$@")
if [[ ${#pytest_args[@]} -eq 0 ]]; then
    pytest_args=(
        "workline_plugins/manual-picking/tests/"
        "tests/integration/wms_integration/outbound_picking/"
    )
fi

echo "[run-integration-tests] running: uv run --extra manual-picking pytest ${pytest_args[*]}" >&2
exec uv run --extra manual-picking pytest "${pytest_args[@]}" -o addopts=''
