#!/usr/bin/env bash

set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_ROOT="${WES_FRONTEND_ROOT:-$(dirname "$BACKEND_ROOT")/wes_frontend}"
ENV_FILE="$BACKEND_ROOT/.env.dev"
WAIT_TIMEOUT="${DEV_ENV_WAIT_TIMEOUT:-240}"
DEV_COMPOSE_PROJECT="${WES_DEV_COMPOSE_PROJECT:-wes_backend_dev}"
COMMAND="${1:-}"

REQUIRED_SERVICES=(
    db
    redis
    api
    celery
    celery-wms-fulfillment
    celery_beat
    mock_ecs
    mock_wms
    frontend
    nginx
)
SERVICES_WITHOUT_HEALTHCHECK=(nginx)

usage() {
    cat <<'EOF'
用法: ./scripts/dev-env.sh <up|check|logs|down> [service...]

  up     构建并启动完整前后端开发环境，执行迁移和幂等基础数据初始化
  check  只读检查容器、HTTP 链路、运行版本和基础调试数据
  logs   持续查看全部服务日志；可追加一个或多个 service 名称
  down   停止并移除开发容器和网络，保留数据库、Redis 和前端依赖卷

可通过 WES_FRONTEND_ROOT 指定前端仓库；默认使用 ../wes_frontend。
长期本机环境固定使用 wes_backend_dev；临时 worktree 必须通过
WES_DEV_COMPOSE_PROJECT 指定唯一项目名，并配置不冲突的宿主机端口。
EOF
}

compose() {
    FRONTEND_ROOT="$FRONTEND_ROOT" COMPOSE_PROJECT_NAME="$DEV_COMPOSE_PROJECT" docker compose \
        --project-name "$DEV_COMPOSE_PROJECT" \
        --env-file "$ENV_FILE" \
        -f "$BACKEND_ROOT/docker-compose.yml" \
        -f "$BACKEND_ROOT/docker-compose.frontend.yml" \
        --profile dev \
        "$@"
}

require_compose_prerequisites() {
    command -v docker >/dev/null 2>&1 || {
        echo "未找到 docker" >&2
        exit 1
    }
    docker info >/dev/null 2>&1 || {
        echo "Docker daemon 不可用" >&2
        exit 1
    }
    [ -f "$ENV_FILE" ] || {
        echo "缺少开发环境文件: $ENV_FILE" >&2
        exit 1
    }
}

require_frontend_prerequisites() {
    [ -f "$FRONTEND_ROOT/package.json" ] && [ -f "$FRONTEND_ROOT/pnpm-lock.yaml" ] || {
        echo "前端仓库路径无效: $FRONTEND_ROOT" >&2
        exit 1
    }
    FRONTEND_ROOT="$(cd "$FRONTEND_ROOT" && pwd -P)"
}

run_seed() {
    compose run --rm --no-deps \
        -e DEBUG=false \
        -e DEV_SEED_ALLOWED=true \
        api \
        python scripts/data/seed_initial_data.py \
        "$@"
}

print_versions() {
    local backend_branch frontend_branch
    backend_branch="$(git -C "$BACKEND_ROOT" symbolic-ref --short -q HEAD || printf detached)"
    frontend_branch="$(git -C "$FRONTEND_ROOT" symbolic-ref --short -q HEAD || printf detached)"
    echo "后端: $backend_branch $(git -C "$BACKEND_ROOT" rev-parse HEAD)"
    echo "前端: $frontend_branch $(git -C "$FRONTEND_ROOT" rev-parse HEAD) root=$FRONTEND_ROOT"
}

check_running_services() {
    local container_id running service_state
    running="$(compose ps --status running --services)"
    for service in "${REQUIRED_SERVICES[@]}"; do
        if ! grep -Fxq "$service" <<<"$running"; then
            echo "服务未运行: $service" >&2
            return 1
        fi
        container_id="$(compose ps -q "$service")"
        service_state="$(
            docker inspect \
                --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
                "$container_id"
        )"
        if [ "$service_state" = "running none" ]; then
            if ! printf '%s\n' "${SERVICES_WITHOUT_HEALTHCHECK[@]}" | grep -Fxq "$service"; then
                echo "服务缺少健康检查: $service" >&2
                return 1
            fi
        elif [ "$service_state" != "running healthy" ]; then
            echo "服务健康检查失败: $service ($service_state)" >&2
            return 1
        fi
    done
}

normalize_frontend_mount_path() {
    local raw_path="$1"
    case "$raw_path" in
        /host_mnt/*) raw_path="/${raw_path#/host_mnt/}" ;;
    esac
    [ -d "$raw_path" ] || return 1
    (cd "$raw_path" && pwd -P)
}

running_frontend_root() {
    local container_id raw_path
    container_id="$(compose ps -q frontend)"
    raw_path="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app"}}{{println .Source}}{{end}}{{end}}' "$container_id")"
    normalize_frontend_mount_path "$raw_path"
}

check_frontend_source_identity() {
    local actual_root
    actual_root="$(running_frontend_root)" || {
        echo "无法解析运行中 frontend 的 /app bind mount" >&2
        return 1
    }
    if [ "$actual_root" != "$FRONTEND_ROOT" ]; then
        echo "frontend 源码不一致: expected=$FRONTEND_ROOT actual=$actual_root" >&2
        return 1
    fi
}

check_http() {
    local urls=(
        "http://127.0.0.1:8001/health"
        "http://127.0.0.1:8001/ready"
        "http://127.0.0.1:8001/api/openapi.json"
        "http://127.0.0.1:5173/"
        "http://127.0.0.1:5173/api/openapi.json"
        "http://127.0.0.1/"
        "http://127.0.0.1:8010/"
        "http://127.0.0.1:8011/"
    )
    for url in "${urls[@]}"; do
        curl --fail --silent --show-error --connect-timeout 3 --max-time 10 --output /dev/null "$url"
        echo "OK $url"
    done
}

check_environment() {
    check_running_services
    check_frontend_source_identity
    check_http
    run_seed --check
    print_versions
    compose ps
    echo "开发环境检查通过"
}

up_environment() {
    echo "启动持久化基础设施..."
    compose up -d --wait --wait-timeout "$WAIT_TIMEOUT" db redis

    echo "构建开发镜像..."
    compose build api mock_ecs mock_wms

    echo "执行数据库迁移..."
    compose run --rm --no-deps api alembic upgrade head

    echo "收敛基础调试数据..."
    run_seed

    echo "启动前后端、异步进程和 Mock..."
    compose up -d --wait --wait-timeout "$WAIT_TIMEOUT" "${REQUIRED_SERVICES[@]}"
    check_environment
}

down_environment() {
    compose down --remove-orphans
    echo "开发容器已停止；docker_data、日志和命名卷均已保留"
}

case "$COMMAND" in
    up)
        require_compose_prerequisites
        require_frontend_prerequisites
        up_environment
        ;;
    check)
        require_compose_prerequisites
        require_frontend_prerequisites
        check_environment
        ;;
    logs)
        require_compose_prerequisites
        shift
        compose logs --tail=200 --follow "$@"
        ;;
    down)
        require_compose_prerequisites
        down_environment
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
