#!/bin/bash
# ============================================
# Docker 部署管理脚本 (统一配置版本)
# ============================================
# 用途: 基于 profiles 和环境变量管理 Docker 服务
# 使用: ./scripts/docker-deploy-simple.sh [环境] [命令]
# 示例: ./scripts/docker-deploy-simple.sh dev up
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# 显示帮助
show_help() {
    cat << EOF
Docker 部署管理脚本 (统一配置版本)

用法: $0 [环境] [命令] [选项]

环境:
  dev         开发环境 (热重载、调试工具)
  test        测试环境 (自动化测试、性能测试)
  prod        生产环境 (优化配置、监控)
  infra       基础设施 (仅数据库 + Redis)
  celery      仅 Celery 服务 (Worker + Beat + Flower)
  api         仅 FastAPI 服务 (API + Nginx)

命令:
  up          启动服务 (后台运行)
  down        停止并删除服务
  restart     重启服务
  logs        查看日志
  ps          查看运行状态
  build       重新构建镜像
  scale       扩展服务实例

选项:
  --scale <service=<n>>    扩展指定服务实例数
  --no-deps               不启动依赖服务
  --force-recreate         强制重新创建容器

示例:
  $0 dev up                       # 启动开发环境
  $0 prod up                      # 按已核准的 1 API / 4 Celery 拓扑启动生产环境
  $0 prod scale --scale api=1 --scale celery=4 # 每次必须声明完整目标拓扑
  $0 dev logs api                 # 查看 API 日志
  $0 prod down                    # 停止生产环境
  $0 dev build --no-cache         # 无缓存重新构建

特殊命令:
  $0 dev up --profile testing      # 启动开发环境 + 性能测试工具
  $0 infra up                     # 仅启动基础设施
  $0 test up                      # 启动测试环境 (包含 pytest)

EOF
}

# 使用目标应用拓扑在 live PostgreSQL 上校验容量。CLI 短连接由 reserve 覆盖。
run_capacity_guard() {
    local env=$1
    shift
    local compose_cmd=$(get_compose_cmd)
    local env_file=""
    local guard_args=()
    local expect_scale_value=false

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    for argument in "$@"; do
        if [ "$expect_scale_value" = true ]; then
            guard_args+=(--scale "$argument")
            expect_scale_value=false
        elif [ "$argument" = "--scale" ]; then
            expect_scale_value=true
        elif [[ "$argument" == --scale=* ]]; then
            guard_args+=(--scale "${argument#--scale=}")
        elif [[ "$argument" == api=* || "$argument" == celery=* ]]; then
            guard_args+=(--scale "$argument")
        fi
    done

    $compose_cmd -f docker-compose.yml $env_file --profile "$env" run --rm --no-deps \
        -e DATABASE_RUNTIME_ROLE=cli \
        -e DATABASE_POOL_SIZE=1 \
        -e DATABASE_MAX_OVERFLOW=0 \
        api python scripts/capacity_guard.py --services api,celery,celery-wms-fulfillment "${guard_args[@]}"
}

# 扩缩容不能依赖上一次命令的离线默认值；每次必须同时声明两个服务的完整目标。
validate_complete_scale_targets() {
    local has_api=false
    local has_celery=false
    local expect_scale_value=false
    local scale_target=""

    for argument in "$@"; do
        scale_target=""
        if [ "$expect_scale_value" = true ]; then
            expect_scale_value=false
            scale_target="$argument"
        elif [ "$argument" = "--scale" ]; then
            expect_scale_value=true
            continue
        elif [[ "$argument" == --scale=* ]]; then
            scale_target="${argument#--scale=}"
        else
            scale_target="$argument"
        fi

        case "$scale_target" in
            api=*) has_api=true ;;
            celery=*) has_celery=true ;;
        esac
    done

    if [ "$has_api" != true ] || [ "$has_celery" != true ]; then
        print_error "scale 必须同时提供完整目标: api=<n> celery=<n>"
        return 1
    fi
}

# 检查环境配置文件
check_env_file() {
    local env=$1

    # 尝试的环境文件顺序
    local env_files=(".env.$env" ".env" "/dev/null")

    for env_file in "${env_files[@]}"; do
        if [ -f "$env_file" ]; then
            export $(cat "$env_file" | grep -v '^#' | xargs)
            print_success "加载配置: $env_file"
            return 0
        fi
    done

    print_error "未找到环境配置文件 (.env.$env 或 .env)"
    return 1
}

# 获取 Docker Compose 命令
get_compose_cmd() {
    # 检查 Docker Compose 版本
    if docker compose version &> /dev/null; then
        echo "docker compose"
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
    else
        print_error "Docker Compose 未安装"
        exit 1
    fi
}

# 启动服务
cmd_up() {
    local env=$1
    shift

    print_info "启动 $env 环境..."

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    # 确定环境文件
    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    # 生产环境分两阶段启动：基础设施健康和 live 容量门禁均通过后才允许应用启动。
    if [ "$env" = "prod" ]; then
        $compose_cmd -f docker-compose.yml $env_file --profile "$env" up -d --wait db redis
        run_capacity_guard "$env" "$@"
    fi

    # 执行命令
    $compose_cmd -f docker-compose.yml $env_file --profile $env up -d "$@"

    print_success "$env 环境已启动"
    print_info "查看状态: $0 $env ps"
    print_info "查看日志: $0 $env logs"
}

# 停止服务
cmd_down() {
    local env=$1
    shift

    print_info "停止 $env 环境..."

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    $compose_cmd -f docker-compose.yml $env_file --profile $env down "$@"

    print_success "$env 环境已停止"
}

# 重启服务
cmd_restart() {
    local env=$1
    shift

    print_info "重启 $env 环境..."

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    $compose_cmd -f docker-compose.yml $env_file --profile $env restart "$@"

    print_success "$env 环境已重启"
}

# 查看日志
cmd_logs() {
    local env=$1
    shift

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    $compose_cmd -f docker-compose.yml $env_file --profile $env logs -f --tail=100 "$@"
}

# 查看状态
cmd_ps() {
    local env=$1

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    $compose_cmd -f docker-compose.yml $env_file --profile $env ps
}

# 重新构建
cmd_build() {
    local env=$1
    shift

    print_info "重新构建 $env 镜像..."

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    $compose_cmd -f docker-compose.yml $env_file --profile $env build "$@"

    print_success "镜像构建完成"
}

# 扩展服务
cmd_scale() {
    local env=$1
    shift

    validate_complete_scale_targets "$@"
    print_info "扩展服务实例..."

    local compose_cmd=$(get_compose_cmd)
    local env_file=""

    if [ -f ".env.$env" ]; then
        env_file="--env-file .env.$env"
    elif [ -f ".env" ]; then
        env_file="--env-file .env"
    fi

    run_capacity_guard "$env" "$@"

    local compose_scale_args=()
    local expect_scale_value=false
    for argument in "$@"; do
        if [ "$expect_scale_value" = true ]; then
            compose_scale_args+=(--scale "$argument")
            expect_scale_value=false
        elif [ "$argument" = "--scale" ]; then
            expect_scale_value=true
        elif [[ "$argument" == --scale=* ]]; then
            compose_scale_args+=(--scale "${argument#--scale=}")
        elif [[ "$argument" == api=* || "$argument" == celery=* ]]; then
            compose_scale_args+=(--scale "$argument")
        fi
    done
    $compose_cmd -f docker-compose.yml $env_file --profile $env up -d "${compose_scale_args[@]}"

    print_success "服务已扩展"
}

# 运行数据库迁移
cmd_migrate() {
    local env=$1

    print_info "运行数据库迁移..."

    local container_name="wes_api_${env}"

    # 检查容器是否存在
    if ! docker ps | grep -q "$container_name"; then
        print_error "容器 $container_name 未运行"
        return 1
    fi

    docker exec -it \
        -e DATABASE_RUNTIME_ROLE=cli \
        -e DATABASE_POOL_SIZE=1 \
        -e DATABASE_MAX_OVERFLOW=0 \
        "$container_name" alembic upgrade head

    print_success "数据库迁移完成"
}

# 创建备份
cmd_backup() {
    local env=$1
    local backup_dir="./backups/$(date +%Y%m%d_%H%M%S)"

    print_info "创建备份到 $backup_dir..."

    mkdir -p "$backup_dir"

    # 备份 PostgreSQL
    print_info "备份数据库..."
    docker exec "wes_postgres_${env}" pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" > "$backup_dir/database.sql"

    # 备份 Redis
    print_info "备份 Redis..."
    docker exec "wes_redis_${env}" redis-cli --rdb /data/backup.rdb
    docker cp "wes_redis_${env}:/data/backup.rdb" "$backup_dir/redis.rdb"

    print_success "备份完成: $backup_dir"
}

# 主函数
main() {
    if [ $# -lt 2 ]; then
        show_help
        exit 1
    fi

    local env=$1
    local command=$2
    shift 2 || true

    # 支持的环境
    local valid_envs=(dev test prod infra celery api)
    if [[ ! " ${valid_envs[@]} " =~ " ${env} " ]]; then
        print_error "无效环境: $env"
        echo "支持的环境: ${valid_envs[@]}"
        exit 1
    fi

    # 执行命令
    case $command in
        up)
            cmd_up "$env" "$@"
            ;;
        down)
            cmd_down "$env" "$@"
            ;;
        restart)
            cmd_restart "$env" "$@"
            ;;
        logs)
            cmd_logs "$env" "$@"
            ;;
        ps)
            cmd_ps "$env"
            ;;
        build)
            cmd_build "$env" "$@"
            ;;
        scale)
            cmd_scale "$env" "$@"
            ;;
        migrate)
            cmd_migrate "$env"
            ;;
        backup)
            cmd_backup "$env"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "未知命令: $command"
            show_help
            exit 1
            ;;
    esac
}

# 直接执行时进入 CLI；被合同测试 source 时仅暴露可复用解析函数。
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
