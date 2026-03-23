#!/bin/bash

# ==============================================================================
# WES Backend 启动前初始化脚本
# ==============================================================================
# 功能：
#   1. Docker 环境检查
#   2. .env 配置文件检查
#   3. 必要目录创建
#   4. 端口占用检查
#   5. 数据库初始化检查
#   6. 基础数据初始化
# ==============================================================================

set -e  # 遇到错误立即退出
set -u  # 使用未定义变量时报错

# ==============================================================================
# 颜色和格式定义
# ==============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ==============================================================================
# 项目配置
# ==============================================================================
SCRIPT_NAME="P9 WES"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
ENV_NAME="prod"
API_PORT="8001"
DB_PORT="5432"
REDIS_PORT_VALUE="6379"
INTERACTIVE_MODE=false
RUN_PORT_CHECK=true
RUN_DOCKER_DOWN=false
RUN_MIGRATIONS=true
RUN_SEED_DATA=true
SELECTED_PROFILES=()
SELECTED_SERVICES=("db" "redis" "api")
AVAILABLE_ENV_FILES=()
AVAILABLE_PROFILES=()
AVAILABLE_SERVICES=()
COMPOSE_PROFILE_ARGS=()

# 需要检查的端口
REQUIRED_PORTS=(
    "5432"  # PostgreSQL
    "6379"  # Redis
    "8001"  # FastAPI App
)

# 需要创建的目录
REQUIRED_DIRS=(
    "logs"
    "docker_data/postgres"
    "docker_data/redis"
)

# 必须配置的环境变量（不能为默认值或占位符）
REQUIRED_ENV_VARS=(
    "POSTGRES_PASSWORD"
    "REDIS_PASSWORD"
    "JWT_SECRET_KEY"
)

# ==============================================================================
# 工具函数
# ==============================================================================

print_header() {
    echo -e "${PURPLE}============================================${NC}"
    echo -e "${PURPLE}${SCRIPT_NAME} $1${NC}"
    echo -e "${PURPLE}============================================${NC}"
}

print_section() {
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

configure_runtime_settings() {
    ENV_NAME="${ENV:-prod}"
    API_PORT="${APP_PORT:-8001}"
    DB_PORT="${POSTGRES_PORT:-5432}"
    REDIS_PORT_VALUE="${REDIS_PORT:-6379}"

    REQUIRED_PORTS=(
        "${DB_PORT}"
        "${REDIS_PORT_VALUE}"
        "${API_PORT}"
    )

    REQUIRED_DIRS=(
        "logs"
        "docker_data/postgres_${ENV_NAME}"
        "docker_data/redis_${ENV_NAME}"
    )
}

refresh_required_ports() {
    REQUIRED_PORTS=()

    if selection_includes "db" "${SELECTED_SERVICES[@]}"; then
        REQUIRED_PORTS+=("${DB_PORT}")
    fi

    if selection_includes "redis" "${SELECTED_SERVICES[@]}"; then
        REQUIRED_PORTS+=("${REDIS_PORT_VALUE}")
    fi

    if selection_includes "api" "${SELECTED_SERVICES[@]}"; then
        REQUIRED_PORTS+=("${API_PORT}")
    fi
}

selection_includes() {
    local target="$1"
    shift

    local item=""
    for item in "$@"; do
        if [[ "${item}" == "${target}" ]]; then
            return 0
        fi
    done

    return 1
}

compose_service_running() {
    local service_name="$1"
    compose_cmd ps --status running -q "${service_name}" 2>/dev/null | grep -q .
}

compose_exec() {
    local service_name="$1"
    shift
    compose_cmd exec -T "${service_name}" "$@"
}

compose_cmd() {
    docker compose --env-file "${ENV_FILE}" "${COMPOSE_PROFILE_ARGS[@]}" "$@"
}

compose_down_cmd() {
    docker compose --env-file "${ENV_FILE}" down --remove-orphans
}

discover_env_files() {
    AVAILABLE_ENV_FILES=()

    while IFS= read -r env_path; do
        [[ -n "${env_path}" ]] && AVAILABLE_ENV_FILES+=("${env_path}")
    done < <(find "${SCRIPT_DIR}" -maxdepth 1 -type f -name '.env*' ! -name '.env.example' | sort)
}

select_env_file() {
    discover_env_files

    if [[ "${INTERACTIVE_MODE}" != "true" ]]; then
        return 0
    fi

    print_section "环境文件选择"

    if [[ ${#AVAILABLE_ENV_FILES[@]} -eq 0 ]]; then
        print_warning "当前目录未发现可用 env 文件，将使用默认 .env"
        return 0
    fi

    local index=1
    local default_choice="1"
    local env_path=""
    for env_path in "${AVAILABLE_ENV_FILES[@]}"; do
        local display_path="${env_path#"${SCRIPT_DIR}/"}"
        echo "  [${index}] ${display_path}"
        if [[ "${env_path}" == "${SCRIPT_DIR}/.env" ]]; then
            default_choice="${index}"
        fi
        ((index++))
    done

    while true; do
        local input=""
        read -p "选择环境文件 [${default_choice}]: " input
        input="${input:-${default_choice}}"

        if [[ "${input}" =~ ^[0-9]+$ ]] && (( input >= 1 && input <= ${#AVAILABLE_ENV_FILES[@]} )); then
            ENV_FILE="${AVAILABLE_ENV_FILES[$((input - 1))]}"
            print_success "已选择环境文件: ${ENV_FILE#"${SCRIPT_DIR}/"}"
            return 0
        fi

        print_warning "输入无效，请输入编号"
    done
}

discover_compose_options() {
    AVAILABLE_PROFILES=()
    AVAILABLE_SERVICES=()

    while IFS= read -r profile_name; do
        [[ -n "${profile_name}" ]] && AVAILABLE_PROFILES+=("${profile_name}")
    done < <(compose_cmd config --profiles 2>/dev/null | sort -u)

    while IFS= read -r service_name; do
        [[ -n "${service_name}" ]] && AVAILABLE_SERVICES+=("${service_name}")
    done < <(compose_cmd config --services 2>/dev/null)
}

parse_multi_select() {
    local input="$1"
    shift
    local options=("$@")
    local tokens=()
    local parsed=()
    local token=""
    local resolved=""

    if [[ -z "${input}" ]]; then
        PARSED_MULTI_SELECT=()
        return 0
    fi

    if [[ "${input}" == "all" || "${input}" == "ALL" ]]; then
        PARSED_MULTI_SELECT=("${options[@]}")
        return 0
    fi

    local normalized="${input//,/ }"
    IFS=' ' read -r -a tokens <<< "${normalized}"

    for token in "${tokens[@]}"; do
        [[ -z "${token}" ]] && continue
        resolved=""

        if [[ "${token}" =~ ^[0-9]+$ ]] && (( token >= 1 && token <= ${#options[@]} )); then
            resolved="${options[$((token - 1))]}"
        else
            local option=""
            for option in "${options[@]}"; do
                if [[ "${option}" == "${token}" ]]; then
                    resolved="${option}"
                    break
                fi
            done
        fi

        if [[ -z "${resolved}" ]]; then
            return 1
        fi

        if ! selection_includes "${resolved}" "${parsed[@]}"; then
            parsed+=("${resolved}")
        fi
    done

    PARSED_MULTI_SELECT=("${parsed[@]}")
    return 0
}

prompt_yes_no() {
    local prompt_text="$1"
    local default_value="$2"

    if [[ "${INTERACTIVE_MODE}" != "true" ]]; then
        [[ "${default_value}" == "y" ]]
        return $?
    fi

    local prompt_suffix="y/N"
    if [[ "${default_value}" == "y" ]]; then
        prompt_suffix="Y/n"
    fi

    while true; do
        local input=""
        read -p "${prompt_text} (${prompt_suffix}): " input
        input="${input:-${default_value}}"

        if [[ "${input}" =~ ^[Yy]$ ]]; then
            return 0
        fi

        if [[ "${input}" =~ ^[Nn]$ ]]; then
            return 1
        fi

        print_warning "请输入 y 或 n"
    done
}

configure_interactive_options() {
    discover_compose_options

    if [[ "${INTERACTIVE_MODE}" != "true" ]]; then
        COMPOSE_PROFILE_ARGS=()
        refresh_required_ports
        return 0
    fi

    print_section "启动选项配置"

    if [[ ${#AVAILABLE_PROFILES[@]} -gt 0 ]]; then
        echo "可用 profiles:"
        local index=1
        local profile_name=""
        for profile_name in "${AVAILABLE_PROFILES[@]}"; do
            echo "  [${index}] ${profile_name}"
            ((index++))
        done
        echo "  [Enter] 跳过 profile 选择"

        while true; do
            local profile_input=""
            read -p "选择 profiles（可多选，逗号分隔，或留空跳过）: " profile_input
            if [[ -z "${profile_input}" ]]; then
                SELECTED_PROFILES=()
                break
            fi

            if parse_multi_select "${profile_input}" "${AVAILABLE_PROFILES[@]}"; then
                SELECTED_PROFILES=("${PARSED_MULTI_SELECT[@]}")
                break
            fi

            print_warning "profile 输入无效，请重新输入"
        done
    fi

    if [[ ${#SELECTED_PROFILES[@]} -gt 0 ]]; then
        COMPOSE_PROFILE_ARGS=()
        local selected_profile=""
        for selected_profile in "${SELECTED_PROFILES[@]}"; do
            COMPOSE_PROFILE_ARGS+=("--profile" "${selected_profile}")
        done
        print_success "已选择 profiles: ${SELECTED_PROFILES[*]}"
    else
        COMPOSE_PROFILE_ARGS=()
        print_info "未显式选择 profile，将按服务名启动"
    fi

    echo ""
    echo "可用 services:"
    local service_index=1
    local service_name=""
    for service_name in "${AVAILABLE_SERVICES[@]}"; do
        echo "  [${service_index}] ${service_name}"
        ((service_index++))
    done
    echo "  默认: db, redis, api"

    while true; do
        local service_input=""
        read -p "选择要启动的 services（可多选，逗号分隔，all 表示全部） [db,redis,api]: " service_input
        service_input="${service_input:-db,redis,api}"

        if parse_multi_select "${service_input}" "${AVAILABLE_SERVICES[@]}"; then
            SELECTED_SERVICES=("${PARSED_MULTI_SELECT[@]}")
            break
        fi

        print_warning "service 输入无效，请重新输入"
    done

    print_success "已选择 services: ${SELECTED_SERVICES[*]}"

    if prompt_yes_no "启动前先执行 docker compose down 吗？" "n"; then
        RUN_DOCKER_DOWN=true
    else
        RUN_DOCKER_DOWN=false
    fi

    if prompt_yes_no "启动前检查相关端口占用吗？" "y"; then
        RUN_PORT_CHECK=true
    else
        RUN_PORT_CHECK=false
    fi

    if selection_includes "api" "${SELECTED_SERVICES[@]}"; then
        if prompt_yes_no "启动后执行数据库迁移吗？" "y"; then
            RUN_MIGRATIONS=true
        else
            RUN_MIGRATIONS=false
        fi

        if [[ "${RUN_MIGRATIONS}" == "true" ]]; then
            if prompt_yes_no "迁移完成后执行基础数据初始化吗？" "y"; then
                RUN_SEED_DATA=true
            else
                RUN_SEED_DATA=false
            fi
        else
            RUN_SEED_DATA=false
        fi
    else
        RUN_MIGRATIONS=false
        RUN_SEED_DATA=false
        print_info "未选择 api 服务，已跳过迁移和种子初始化"
    fi

    refresh_required_ports
}

ensure_required_services() {
    if [[ "${RUN_MIGRATIONS}" == "true" || "${RUN_SEED_DATA}" == "true" ]]; then
        if ! selection_includes "api" "${SELECTED_SERVICES[@]}"; then
            SELECTED_SERVICES+=("api")
        fi
        if ! selection_includes "db" "${SELECTED_SERVICES[@]}"; then
            SELECTED_SERVICES+=("db")
        fi
        if ! selection_includes "redis" "${SELECTED_SERVICES[@]}"; then
            SELECTED_SERVICES+=("redis")
        fi
    fi

    refresh_required_ports
}

stop_services_first() {
    print_section "停止现有服务"

    print_info "执行 docker compose down --remove-orphans ..."
    if compose_down_cmd; then
        print_success "现有服务已停止"
        return 0
    fi

    print_error "停止现有服务失败"
    return 1
}

# ==============================================================================
# 检查函数
# ==============================================================================

check_docker() {
    print_section "Docker 环境检查"

    # 检查 Docker 命令
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装"
        print_info "请访问 https://docs.docker.com/get-docker/ 安装 Docker"
        return 1
    fi
    print_success "Docker 命令可用: $(docker --version | head -n1)"

    # 检查 Docker Compose
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        print_error "Docker Compose 未安装"
        print_info "请安装 Docker Compose 插件或独立版本"
        return 1
    fi

    # 检查 Docker 服务状态
    if ! docker info &> /dev/null; then
        print_error "Docker 服务未运行"
        print_info "请启动 Docker 服务"
        return 1
    fi
    print_success "Docker 服务运行中"

    return 0
}

# 安全加载 .env 文件
load_env_file() {
    local env_file="$1"

    # 逐行读取 .env 文件并导出环境变量
    while IFS='=' read -r key value; do
        # 跳过空行和注释
        [[ -z "${key}" ]] && continue
        [[ "${key}" == \#* ]] && continue

        # 移除值两边的引号（支持单引号和双引号）
        # 检查是否以双引号开头
        if [[ "${value}" == \"* ]]; then
            value="${value#\"}"      # 移除开头的双引号
            value="${value%\"}"       # 移除结尾的双引号
        # 检查是否以单引号开头
        elif [[ "${value}" == \'* ]]; then
            value="${value#\'}"       # 移除开头的单引号
            value="${value%\'}"       # 移除结尾的单引号
        fi

        # 移除值前后的空格
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"

        # 导出环境变量
        export "${key}=${value}"
    done < "${env_file}"
}

check_env_file() {
    print_section "环境配置文件检查"

    # 检查 .env 文件是否存在
    if [[ ! -f "${ENV_FILE}" ]]; then
        print_warning ".env 文件不存在"

        # 检查是否有 .env.example
        if [[ ! -f "${ENV_EXAMPLE}" ]]; then
            print_error ".env.example 文件也不存在，无法创建配置文件"
            return 1
        fi

        print_info "从 .env.example 创建 .env 文件"
        if ! cp "${ENV_EXAMPLE}" "${ENV_FILE}"; then
            print_error "创建 .env 文件失败"
            return 1
        fi
        print_success ".env 文件已创建"
    else
        print_success ".env 文件存在"
    fi

    # 安全加载环境变量
    load_env_file "${ENV_FILE}"
    configure_runtime_settings

    # 检查必需的环境变量
    local missing_vars=0
    local insecure_vars=0

    for var in "${REQUIRED_ENV_VARS[@]}"; do
        local var_value="${!var:-}"

        # 检查变量是否设置
        if [[ -z "${var_value}" ]]; then
            print_error "环境变量 ${var} 未设置"
            ((missing_vars++))
        # 检查是否为占位符
        elif [[ "${var_value}" == *"CHANGE_THIS"* ]]; then
            print_error "环境变量 ${var} 使用了默认占位符值，必须修改"
            print_info "  生成方法: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            ((insecure_vars++))
        # 检查长度是否足够
        elif [[ "${var}" == *"SECRET"* ]] && [[ ${#var_value} -lt 32 ]]; then
            print_warning "环境变量 ${var} 长度不足 32 字符"
            ((insecure_vars++))
        else
            print_success "环境变量 ${var} 已配置"
        fi
    done

    if [[ ${missing_vars} -gt 0 ]] || [[ ${insecure_vars} -gt 0 ]]; then
        print_error "发现 ${missing_vars} 个未设置变量和 ${insecure_vars} 个不安全配置"
        print_info "请编辑 .env 文件并设置正确的值"
        return 1
    fi

    print_success "环境配置检查通过"
    print_info "当前环境: ${ENV_NAME}"

    return 0
}

check_directories() {
    print_section "目录结构检查"

    local created=0

    for dir in "${REQUIRED_DIRS[@]}"; do
        local full_path="${SCRIPT_DIR}/${dir}"

        if [[ ! -d "${full_path}" ]]; then
            print_info "创建目录: ${dir}"
            if ! mkdir -p "${full_path}"; then
                print_error "创建目录 ${dir} 失败"
                return 1
            fi
            ((created++))
        fi

        # 检查目录权限
        if [[ ! -w "${full_path}" ]]; then
            print_warning "目录 ${dir} 不可写"
            if ! chmod +w "${full_path}"; then
                print_error "修改目录权限失败"
                return 1
            fi
        fi

        print_success "目录就绪: ${dir}"
    done

    if [[ ${created} -gt 0 ]]; then
        print_info "已创建 ${created} 个必要目录"
    fi

    return 0
}

check_ports() {
    print_section "端口占用检查"

    local occupied=0

    for port in "${REQUIRED_PORTS[@]}"; do
        # 检查端口是否被占用
        if lsof -Pi :${port} -sTCP:LISTEN -t >/dev/null 2>&1 || \
           netstat -an 2>/dev/null | grep "LISTEN" | grep -q ":${port} "; then
            print_warning "端口 ${port} 已被占用"
            ((occupied++))

            # 尝试识别占用进程
            local pid=$(lsof -ti:${port} 2>/dev/null || echo "")
            if [[ -n "${pid}" ]]; then
                local process=$(ps -p ${pid} -o comm= 2>/dev/null || echo "unknown")
                print_info "  占用进程: ${process} (PID: ${pid})"
            fi
        else
            print_success "端口 ${port} 可用"
        fi
    done

    if [[ ${occupied} -gt 0 ]]; then
        print_warning "发现 ${occupied} 个端口被占用"
        read -p "是否继续启动？(y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi

    return 0
}

check_database_connection() {
    print_section "数据库连接检查"

    if ! compose_service_running "db"; then
        print_error "数据库服务未运行"
        return 1
    fi

    print_success "数据库服务运行中"

    # 等待数据库就绪
    print_info "等待数据库就绪..."
    local max_attempts=30
    local attempt=0

    while [[ ${attempt} -lt ${max_attempts} ]]; do
        if compose_exec "db" pg_isready -U "${POSTGRES_USER:-wes_user}" -d "${POSTGRES_DB:-wes_db}" &>/dev/null; then
            print_success "数据库连接就绪"
            return 0
        fi

        ((attempt++))
        if [[ ${attempt} -lt ${max_attempts} ]]; then
            echo -n "."
            sleep 1
        fi
    done

    print_error "数据库连接超时"
    return 1
}

check_redis_connection() {
    print_section "Redis 连接检查"

    if ! compose_service_running "redis"; then
        print_error "Redis 服务未运行"
        return 1
    fi

    print_success "Redis 服务运行中"

    # 等待 Redis 就绪
    print_info "等待 Redis 就绪..."
    local max_attempts=30
    local attempt=0

    while [[ ${attempt} -lt ${max_attempts} ]]; do
        if compose_exec "redis" redis-cli -a "${REDIS_PASSWORD}" ping &>/dev/null; then
            print_success "Redis 连接就绪"
            return 0
        fi

        ((attempt++))
        if [[ ${attempt} -lt ${max_attempts} ]]; then
            echo -n "."
            sleep 1
        fi
    done

    print_error "Redis 连接超时"
    return 1
}

init_database_data() {
    print_section "数据库初始化"

    if ! compose_service_running "api"; then
        print_error "API 服务未运行，无法执行初始化"
        return 1
    fi

    # 检查初始化脚本
    local init_script="${SCRIPT_DIR}/scripts/data/seed_initial_data.py"
    if [[ ! -f "${init_script}" ]]; then
        print_warning "初始化脚本不存在: ${init_script}"
        return 0
    fi

    print_info "检查是否需要初始化数据库..."

    # 重新加载环境变量确保可用
    load_env_file "${ENV_FILE}"
    local check_query="SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'wes_sys' AND table_name = 'users');"

    if compose_exec "db" psql -U "${POSTGRES_USER:-wes_user}" -d "${POSTGRES_DB:-wes_db}" -tAc "${check_query}" 2>/dev/null | grep -q "t"; then
        print_info "检查用户数据..."

        local user_count=$(compose_exec "db" psql -U "${POSTGRES_USER:-wes_user}" -d "${POSTGRES_DB:-wes_db}" -tAc "SELECT COUNT(*) FROM wes_sys.users;" 2>/dev/null || echo "0")

        if [[ "${user_count}" -gt "0" ]]; then
            print_success "数据库已包含 ${user_count} 个用户，跳过初始化"
            return 0
        fi
    fi

    print_info "开始初始化数据库基础数据..."

    # 运行初始化脚本
    if compose_exec "api" python scripts/data/seed_initial_data.py; then
        print_success "数据库初始化完成"
        return 0
    else
        print_error "数据库初始化失败"
        return 1
    fi
}

run_database_migrations() {
    print_section "数据库迁移"

    if ! compose_service_running "api"; then
        print_error "API 服务未运行，无法执行迁移"
        return 1
    fi

    print_info "执行 Alembic 迁移..."
    if compose_exec "api" alembic upgrade head; then
        print_success "数据库迁移完成"
        return 0
    fi

    print_error "数据库迁移失败"
    return 1
}

start_services() {
    print_section "启动服务"

    print_info "使用 Docker Compose 启动服务: ${SELECTED_SERVICES[*]}"
    if compose_cmd up -d "${SELECTED_SERVICES[@]}"; then
        print_success "服务启动成功"
        return 0
    else
        print_error "服务启动失败"
        return 1
    fi
}

show_summary() {
    print_header "初始化完成"

    echo ""
    print_success "所有检查通过，系统已准备就绪！"
    echo ""
    print_info "已启动服务: ${SELECTED_SERVICES[*]}"
    if [[ ${#SELECTED_PROFILES[@]} -gt 0 ]]; then
        print_info "已选择 profiles: ${SELECTED_PROFILES[*]}"
    fi
    echo ""
    print_info "服务信息:"
    if selection_includes "api" "${SELECTED_SERVICES[@]}"; then
        echo -e "  • API 地址: ${GREEN}http://localhost:${API_PORT}${NC}"
        echo -e "  • API 文档: ${GREEN}http://localhost:${API_PORT}/docs${NC}"
    fi
    if selection_includes "db" "${SELECTED_SERVICES[@]}"; then
        echo -e "  • 数据库: ${GREEN}localhost:${DB_PORT}${NC}"
    fi
    if selection_includes "redis" "${SELECTED_SERVICES[@]}"; then
        echo -e "  • Redis: ${GREEN}localhost:${REDIS_PORT_VALUE}${NC}"
    fi
    echo ""
    if [[ "${RUN_SEED_DATA}" == "true" ]]; then
        print_info "默认登录账号:"
        echo -e "  ${CYAN}admin${NC}     / ${YELLOW}admin123${NC}"
        echo -e "  ${CYAN}manager${NC}   / ${YELLOW}admin123${NC}"
        echo -e "  ${CYAN}operator${NC}  / ${YELLOW}admin123${NC}"
        echo -e "  ${CYAN}finance${NC}   / ${YELLOW}admin123${NC}"
        echo -e "  ${CYAN}user1${NC}     / ${YELLOW}admin123${NC}"
        echo -e "  ${CYAN}user2${NC}     / ${YELLOW}admin123${NC}"
        echo ""
        print_warning "⚠️  生产环境请立即修改默认密码！"
        echo ""
    fi
    echo ""
    print_info "常用命令:"
    echo -e "  • 查看日志: ${CYAN}docker compose logs -f${NC}"
    echo -e "  • 停止服务: ${CYAN}docker compose down${NC}"
    echo -e "  • 重启服务: ${CYAN}docker compose restart${NC}"
    echo -e "  • 查看状态: ${CYAN}docker compose ps${NC}"
    echo ""
}

# ==============================================================================
# 主函数
# ==============================================================================

main() {
    print_header "启动初始化"
    echo ""

    if [[ -t 0 && -t 1 ]]; then
        INTERACTIVE_MODE=true
    fi

    # 1. Docker 环境检查
    if ! check_docker; then
        print_error "Docker 环境检查失败，退出"
        exit 1
    fi
    echo ""

    # 2. 选择环境文件
    select_env_file
    echo ""

    # 3. 环境配置文件检查
    if ! check_env_file; then
        print_error "环境配置检查失败，退出"
        exit 1
    fi
    echo ""

    # 4. 配置交互选项
    configure_interactive_options
    ensure_required_services
    echo ""

    # 5. 目录结构检查
    if ! check_directories; then
        print_error "目录检查失败，退出"
        exit 1
    fi
    echo ""

    # 6. 按需停止现有服务
    if [[ "${RUN_DOCKER_DOWN}" == "true" ]]; then
        if ! stop_services_first; then
            print_error "停止现有服务失败，退出"
            exit 1
        fi
        echo ""
    fi

    # 7. 端口占用检查
    if [[ "${RUN_PORT_CHECK}" == "true" && ${#REQUIRED_PORTS[@]} -gt 0 ]]; then
        if ! check_ports; then
            print_error "端口检查失败，退出"
            exit 1
        fi
        echo ""
    fi

    # 8. 启动 Docker 服务
    if ! start_services; then
        print_error "服务启动失败，退出"
        exit 1
    fi
    echo ""

    # 9. 数据库连接检查
    if selection_includes "db" "${SELECTED_SERVICES[@]}" || [[ "${RUN_MIGRATIONS}" == "true" || "${RUN_SEED_DATA}" == "true" ]]; then
        if ! check_database_connection; then
            print_error "数据库连接失败，退出"
            exit 1
        fi
        echo ""
    fi

    # 10. Redis 连接检查
    if selection_includes "redis" "${SELECTED_SERVICES[@]}" || selection_includes "api" "${SELECTED_SERVICES[@]}"; then
        if ! check_redis_connection; then
            print_error "Redis 连接失败，退出"
            exit 1
        fi
        echo ""
    fi

    # 11. 数据库迁移
    if [[ "${RUN_MIGRATIONS}" == "true" ]]; then
        if ! run_database_migrations; then
            print_error "数据库迁移失败，退出"
            exit 1
        fi
        echo ""
    fi

    # 12. 数据库初始化
    if [[ "${RUN_SEED_DATA}" == "true" ]]; then
        if ! init_database_data; then
            print_warning "数据库初始化失败，但服务已启动"
            print_info "可以稍后手动运行: docker compose exec api python scripts/data/seed_initial_data.py"
        fi
        echo ""
    fi

    # 显示摘要信息
    show_summary
}

# ==============================================================================
# 脚本入口
# ==============================================================================

# 捕获 Ctrl+C 信号
trap 'print_error "初始化被用户中断"; exit 130' INT

# 运行主函数
main "$@"
