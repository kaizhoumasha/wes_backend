#!/bin/bash
# ================================================================
# WES Backend 数据库初始化脚本
# ================================================================
# 功能：
#   1. 自动读取环境变量配置
#   2. 检查数据库连接
#   3. 生成 Argon2 密码哈希
#   4. 执行 SQL 初始化脚本
#   5. 验证初始化结果
#
# 使用方法：
#   ./scripts/database/run_init_db.sh [options]
#
# 选项：
#   -f, --force    强制重新初始化（会清空现有数据）
#   -p, --password 指定管理员密码（默认：admin123）
#   -h, --help     显示帮助信息
# ================================================================

set -e  # 遇到错误立即退出

# ================================================================
# 颜色定义
# ================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ================================================================
# 默认配置
# ================================================================
DEFAULT_ADMIN_PASSWORD="admin123"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"
SQL_FILE="${PROJECT_ROOT}/scripts/database/init_db.sql"

# ================================================================
# 帮助信息
# ================================================================
show_help() {
    cat << EOF
${BLUE}WES Backend 数据库初始化脚本${NC}

${YELLOW}使用方法:${NC}
  $0 [options]

${YELLOW}选项:${NC}
  -f, --force       强制重新初始化（会清空现有数据）
  -h, --help        显示帮助信息

${YELLOW}示例:${NC}
  $0                                    # 使用默认配置初始化
  $0 --force                            # 强制重新初始化

${YELLOW}默认管理员账户:${NC}
  用户名: admin
  邮箱: admin@wes.local
  密码: admin123（请首次登录后修改）

${YELLOW}注意:${NC}
  SQL 文件中已预配置密码哈希值，所有用户使用统一密码: ${DEFAULT_ADMIN_PASSWORD}

EOF
}

# ================================================================
# 日志函数
# ================================================================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ================================================================
# 解析命令行参数
# ================================================================
FORCE_INIT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--force)
            FORCE_INIT=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log_error "未知选项: $1"
            show_help
            exit 1
            ;;
    esac
done

# ================================================================
# 加载环境变量
# ================================================================
load_env_vars() {
    local env_file="$1"
    if [[ -f "${env_file}" ]]; then
        log_info "加载环境变量: ${env_file}"
        # 使用 grep 安全地加载 .env 文件，避免空格和特殊字符问题
        while IFS='=' read -r key value; do
            # 跳过注释和空行
            [[ "$key" =~ ^#.*$ ]] && continue
            [[ -z "$key" ]] && continue
            # 移除值两端的空格和引号
            value=$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^['\"]//;s/['\"]$//")
            # 导出变量
            export "$key=$value"
        done < <(grep -E '^[A-Z_]+=.*' "${env_file}")
    else
        log_warning ".env 文件不存在，使用默认配置"
    fi
}

load_env_vars "${ENV_FILE}"

# 设置默认值（如果环境变量未设置）
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export POSTGRES_DB="${POSTGRES_DB:-wes_db}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"

# ================================================================
# 检查依赖
# ================================================================
log_info "检查依赖命令..."

if ! command -v docker &> /dev/null; then
    log_error "docker 命令未找到，请安装 Docker"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    log_error "python3 命令未找到，需要 Python 来生成密码哈希"
    exit 1
fi

# ================================================================
# 检查 Docker 容器状态
# ================================================================
log_info "检查 Docker 容器状态..."

DB_CONTAINER="${DB_CONTAINER:-wes_postgres}"

if ! docker ps | grep -q "${DB_CONTAINER}"; then
    log_error "数据库容器 ${DB_CONTAINER} 未运行"
    log_info "请先启动数据库：docker-compose up -d db"
    exit 1
fi

log_success "数据库容器 ${DB_CONTAINER} 正在运行"

# ================================================================
# 测试数据库连接
# ================================================================
log_info "测试数据库连接..."

if ! docker exec "${DB_CONTAINER}" pg_isready -U "${POSTGRES_USER}" &> /dev/null; then
    log_error "数据库连接失败"
    exit 1
fi

log_success "数据库连接成功"

# ================================================================
# 检查是否已初始化
# ================================================================
log_info "检查数据库状态..."

EXISTING_USERS=$(docker exec "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c "SELECT COUNT(*) FROM users WHERE username = 'admin';" 2>/dev/null || echo "0")

if [[ "${EXISTING_USERS}" -gt 0 ]]; then
    if [[ "${FORCE_INIT}" == false ]]; then
        log_warning "检测到数据库已初始化"
        log_info "如需重新初始化，请使用 -f 或 --force 选项"
        exit 0
    else
        log_warning "强制重新初始化，将清空现有数据..."
        read -p "确认继续？(y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "操作已取消"
            exit 0
        fi
    fi
fi

# ================================================================
# 准备 SQL 文件
# ================================================================
TEMP_SQL="${SQL_FILE}"

log_info "准备初始化脚本..."
log_info "所有用户使用统一密码: ${DEFAULT_ADMIN_PASSWORD}"
log_info "SQL 文件中已预配置密码哈希值"

# ================================================================
# 执行初始化脚本
# ================================================================
log_info "执行数据库初始化..."
log_info "这可能需要几分钟..."

if docker exec -i "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < "${TEMP_SQL}" > /tmp/init_db.log 2>&1; then
    log_success "数据库初始化成功"
else
    log_error "数据库初始化失败"
    cat /tmp/init_db.log
    exit 1
fi

# ================================================================
# 清理临时文件
# ================================================================
# 注意：TEMP_SQL 直接指向 SQL_FILE（不需要动态生成密码哈希），所以不清理
# rm -f "${TEMP_SQL}"

# ================================================================
# 验证初始化结果
# ================================================================
log_info "验证初始化结果..."

USER_COUNT=$(docker exec "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c "SELECT COUNT(*) FROM users;")
ROLE_COUNT=$(docker exec "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c "SELECT COUNT(*) FROM roles;")
PERM_COUNT=$(docker exec "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c "SELECT COUNT(*) FROM permissions;")

log_success "验证完成"
echo ""
printf "${GREEN}==================================================${NC}\n"
printf "${GREEN}数据库初始化完成！${NC}\n"
printf "${GREEN}==================================================${NC}\n"
printf "${BLUE}统计数据:${NC}\n"
printf "  用户数量: ${USER_COUNT}\n"
printf "  角色数量: ${ROLE_COUNT}\n"
printf "  权限数量: ${PERM_COUNT}\n"
echo ""
printf "${BLUE}初始化的账户:${NC}\n"
printf "  ${GREEN}admin${NC}      - 系统管理员 (超级管理员角色)\n"
printf "  ${GREEN}manager${NC}   - 系统管理员 (管理员角色)\n"
printf "  ${GREEN}operator${NC}   - 运营专员 (运营人员角色)\n"
printf "  ${GREEN}finance${NC}    - 财务专员 (财务人员角色)\n"
printf "  ${GREEN}user1${NC}      - 普通用户一 (普通用户角色)\n"
printf "  ${GREEN}user2${NC}      - 普通用户二 (普通用户角色)\n"
echo ""
printf "${BLUE}统一密码: ${GREEN}${ADMIN_PASSWORD}${NC}\n"
printf "  ${YELLOW}⚠️  请首次登录后立即修改默认密码！${NC}\n"
echo ""
printf "${GREEN}==================================================${NC}\n"
echo ""
