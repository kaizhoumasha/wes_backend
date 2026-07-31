#!/bin/bash
# ============================================
# 环境配置初始化脚本
# ============================================
# 用途: 从指定环境复制配置到 .env，并生成应用层安全密钥
# 使用: ./scripts/init-env.sh [dev|test|prod]
# 示例: ./scripts/init-env.sh dev
#
# 功能:
# - 复制 .env.{dev|test|prod} 到 .env
# - 保留数据库和 Redis 密码（确保 Docker 容器和宿主机使用相同密码）
# - 生成应用层安全密钥: JWT_SECRET_KEY, API_SECRET_ENCRYPTION_KEY
# - 备份现有 .env 文件
# - 保存密钥对照信息到 .env.new_keys
# ============================================

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# 显示帮助
show_help() {
    cat << EOF
环境配置初始化脚本

用法: $0 [环境]

环境:
  dev         开发环境 (复制 .env.dev → .env)
  test        测试环境 (复制 .env.test → .env)
  prod        生产环境 (复制 .env.prod → .env)

说明:
  此脚本会将指定环境的配置文件复制到 .env，
  用于在宿主机上运行 Alembic 迁移、测试等操作。

  特性:
  - 保留数据库和 Redis 密码（确保容器和宿主机密码一致）
  - 自动生成应用层安全密钥（JWT_SECRET_KEY, API_SECRET_ENCRYPTION_KEY）
  - 备份现有 .env 文件
  - 跳过注释行，只保留配置项

示例:
  $0 dev
  $0 test
  $0 prod

EOF
}

# 检查参数
if [ $# -eq 0 ]; then
    show_help
    exit 1
fi

ENV=$1

# 支持的环境
if [[ ! "$ENV" =~ ^(dev|test|prod)$ ]]; then
    print_error "无效环境: $ENV"
    echo "支持的环境: dev, test, prod"
    exit 1
fi

ENV_FILE=".env.$ENV"

# 检查环境文件是否存在
if [ ! -f "$ENV_FILE" ]; then
    print_error "环境文件不存在: $ENV_FILE"
    exit 1
fi

print_info "从 $ENV_FILE 复制配置到 .env..."

# ============================================
# 密钥和密码生成策略
# ============================================
# dev/test 环境: 保留原密码，只生成应用层密钥
# prod 环境:   生成所有新密码（包括数据库和 Redis）
# ============================================

if [[ "$ENV" == "prod" ]]; then
    # 生产环境：生成所有新的强密码
    print_warning "生产环境检测到，将生成所有新的安全密钥和密码..."

    # 数据库和 Redis 密码
    DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

    # 应用层密钥
    JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    API_SECRET_ENCRYPTION_KEY=$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
else
    # dev/test 环境：保留原密码，只生成应用层密钥
    print_info "开发/测试环境，将保留原数据库和 Redis 密码..."

    # 应用层密钥
    JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    API_SECRET_ENCRYPTION_KEY=$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
fi

# ============================================
# 重要：统一使用 .env 文件
# ============================================
# - init-env.sh 将所有配置复制到 .env
# - 宿主机和 Docker 容器都使用 .env 文件
# - 无需再使用 --env-file 参数
# - 避免配置同步问题
# ============================================

# 备份现有 .env
if [ -f ".env" ]; then
    BACKUP_FILE=".env.backup.$(date +%Y%m%d_%H%M%S)"
    cp .env "$BACKUP_FILE"
    print_info "已备份现有 .env → $BACKUP_FILE"
fi

# 添加特殊说明头部
cat > .env << 'HEADER'
# ============================================
# 本地开发环境配置
# ============================================
# 来源: $ENV_FILE
# 更新时间: $(date '+%Y-%m-%d %H:%M:%S')
#
# 说明:
# - 此文件用于在宿主机上运行 Alembic 迁移、测试等操作
# - Docker 容器使用 $ENV_FILE，不受此文件影响
# - 密码已自动生成强密码，确保安全性
# - 如需更新配置，重新运行: ./scripts/init-env.sh $ENV
# ============================================

HEADER

# 复制环境配置，同时替换密码和密钥
while IFS= read -r line; do
    # 跳过空行和注释
    if [[ -z "$line" ]] || [[ "$line" =~ ^#.*$ ]]; then
        continue
    fi

    # 替换密钥和密码
    if [[ "$ENV" == "prod" ]]; then
        # 生产环境：替换所有密钥和密码
        if [[ "$line" =~ ^POSTGRES_PASSWORD= ]]; then
            echo "POSTGRES_PASSWORD=$DB_PASSWORD"
        elif [[ "$line" =~ ^REDIS_PASSWORD= ]]; then
            echo "REDIS_PASSWORD=$REDIS_PASSWORD"
        elif [[ "$line" =~ ^JWT_SECRET_KEY= ]]; then
            echo "JWT_SECRET_KEY=$JWT_SECRET_KEY"
        elif [[ "$line" =~ ^API_SECRET_ENCRYPTION_KEY= ]]; then
            echo "API_SECRET_ENCRYPTION_KEY=$API_SECRET_ENCRYPTION_KEY"
        else
            # 其他配置直接复制
            echo "$line"
        fi
    else
        # dev/test 环境：只替换应用层密钥，保留原数据库和 Redis 密码
        if [[ "$line" =~ ^JWT_SECRET_KEY= ]]; then
            echo "JWT_SECRET_KEY=$JWT_SECRET_KEY"
        elif [[ "$line" =~ ^API_SECRET_ENCRYPTION_KEY= ]]; then
            echo "API_SECRET_ENCRYPTION_KEY=$API_SECRET_ENCRYPTION_KEY"
        elif [[ "$line" =~ ^POSTGRES_HOST= ]]; then
            # 宿主机无法解析 Docker 容器主机名，需要替换为 localhost
            echo "POSTGRES_HOST=localhost"
        elif [[ "$line" =~ ^REDIS_HOST= ]]; then
            # 宿主机无法解析 Docker 容器主机名，需要替换为 localhost
            echo "REDIS_HOST=localhost"
        elif [[ "$line" =~ ^MOCK_ECS_HOST= ]]; then
            # 宿主机直跑 WES/Celery 时无法解析 Docker Compose 服务名
            echo "MOCK_ECS_HOST=localhost"
        elif [[ "$line" =~ ^MOCK_ECS_URL= ]]; then
            # 宿主机直跑联调脚本时应访问端口映射后的本地 ECS Mock
            echo "MOCK_ECS_URL=http://localhost:8010"
        elif [[ "$line" =~ ^WES_EVENT_CALLBACK_URL=http://api:8001/ ]]; then
            # 宿主机直跑 Mock ECS 时应访问端口映射后的本地 WES API
            echo "WES_EVENT_CALLBACK_URL=http://localhost:8001${line#WES_EVENT_CALLBACK_URL=http://api:8001}"
        elif [[ "$line" =~ ^WES_RESULT_CALLBACK_URL=http://api:8001/ ]]; then
            # 宿主机直跑 Mock ECS 时应访问端口映射后的本地 WES API
            echo "WES_RESULT_CALLBACK_URL=http://localhost:8001${line#WES_RESULT_CALLBACK_URL=http://api:8001}"
        elif [[ "$line" =~ ^WES_EXTERNAL_CALLBACK_URL=http://api:8001/ ]]; then
            # 宿主机直跑 Mock WMS 时应访问端口映射后的本地 WES API
            echo "WES_EXTERNAL_CALLBACK_URL=http://localhost:8001${line#WES_EXTERNAL_CALLBACK_URL=http://api:8001}"
        else
            # 其他配置直接复制（包括数据库和 Redis 密码）
            echo "$line"
        fi
    fi
done < "$ENV_FILE" >> .env

print_success "配置已从 $ENV_FILE 复制到 .env"

# 保存生成的密钥和密码到临时文件（用于参考）
if [[ "$ENV" == "prod" ]]; then
    # 生产环境：保存所有生成的密钥和密码
    cat > .env.new_keys << EOF
# ============================================
# 生产环境 - 新生成的安全密钥和密码
# ============================================
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# 来源: init-env.sh 自动生成
# 环境: prod (生产)
#
# ⚠️ 警告：
# - 以下所有密钥和密码都是新生成的强随机值
# - 所有配置已自动保存到 .env 文件
# - Docker 容器和宿主机都使用 .env（配置自动同步）
# - 请妥善保管此文件，不要提交到版本控制
# - 建议将此文件安全传输给运维团队
# ============================================

# 数据库和 Redis 密码
POSTGRES_PASSWORD=$DB_PASSWORD
REDIS_PASSWORD=$REDIS_PASSWORD

# JWT 和 API 加密密钥
JWT_SECRET_KEY=$JWT_SECRET_KEY
API_SECRET_ENCRYPTION_KEY=$API_SECRET_ENCRYPTION_KEY

# ============================================
# 重要提示
# ============================================
# ✅ 所有配置已自动保存到 .env 文件
# ✅ 宿主机和 Docker 容器都使用 .env（配置自动同步）
# ✅ 直接启动容器即可，无需手动更新任何文件
#
# 启动命令：
# docker-compose --profile prod up -d
# ============================================

EOF

    print_success "生产环境密钥和密码已保存到 .env.new_keys"
    print_success "所有配置已自动保存到 .env，可直接启动容器"

else
    # dev/test 环境：只保存应用层密钥
    cat > .env.new_keys << EOF
# ============================================
# 新生成的应用层安全密钥
# ============================================
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# 来源: init-env.sh 自动生成
# 环境: $ENV
#
# 说明:
# - 以下是应用层密钥，用于宿主机环境（.env 文件）
# - 数据库和 Redis 密码与环境配置文件保持一致
# - 请妥善保管此文件，不要提交到版本控制
# ============================================

# JWT 和 API 加密密钥
JWT_SECRET_KEY=$JWT_SECRET_KEY
API_SECRET_ENCRYPTION_KEY=$API_SECRET_ENCRYPTION_KEY

# ============================================
# Docker 容器使用的密钥（来自 .env.$ENV）
# ============================================

EOF

    grep "JWT_SECRET_KEY\|API_SECRET_ENCRYPTION_KEY" "$ENV_FILE" >> .env.new_keys

    print_success "应用层密钥已保存到 .env.new_keys"
fi

# 显示关键配置
echo ""
echo -e "${BLUE}当前配置:${NC}"
echo "  环境: $ENV"
echo "  数据库: $(grep POSTGRES_HOST .env | cut -d= -f2):$(grep POSTGRES_PORT .env | cut -d= -f2)/$(grep POSTGRES_DB .env | cut -d= -f2)"
echo "  Redis: $(grep REDIS_HOST .env | cut -d= -f2):$(grep REDIS_PORT .env | cut -d= -f2)"
echo ""

if [[ "$ENV" == "prod" ]]; then
    # 生产环境提示
    echo -e "${RED}⚠️  生产环境安全警告:${NC}"
    echo -e "${YELLOW}  - 所有密钥和密码已生成新的强随机值${NC}"
    echo -e "${YELLOW}  - 请查看 .env.new_keys 获取完整信息${NC}"
    echo -e "${YELLOW}  - 必须将新密码更新到 .env.prod 并重启 Docker 容器${NC}"
    echo ""

    echo -e "${YELLOW}生产环境部署步骤:${NC}"
    echo "  1. 查看新生成的密钥: cat .env.new_keys"
    echo "  2. 密钥已自动保存到 .env（无需手动更新）"
    echo "  3. 启动基础设施: docker-compose --profile prod up -d db redis"
    echo "  4. 等待数据库启动: sleep 5"
    echo "  5. 运行迁移: uv run alembic upgrade head"
    echo "  6. 启动所有服务: docker-compose --profile prod up -d"
    echo ""
    echo -e "${GREEN}✓ 宿主机和容器都使用 .env，配置自动同步${NC}"
else
    # dev/test 环境提示
    echo -e "${YELLOW}⚠️  应用层密钥已生成并保存到 .env.new_keys${NC}"
    echo -e "${YELLOW}⚠️  数据库和 Redis 密码与环境配置文件保持一致${NC}"
    echo ""

    echo -e "${YELLOW}下一步:${NC}"
    echo "  1. 启动基础设施: docker-compose --profile $ENV up -d db redis"
    echo "  2. 等待数据库启动: sleep 5"
    echo "  3. 运行迁移: uv run alembic upgrade head"
    echo "  4. 启动服务: docker-compose --profile $ENV up -d"
    echo ""
    echo -e "${GREEN}✓ 宿主机和容器都使用 .env，配置自动同步${NC}"
fi

echo ""
echo -e "${BLUE}提示: 如需切换环境，重新运行: ./scripts/init-env.sh [dev|test|prod]${NC}"
echo ""
