#!/bin/bash
# ============================================
# E2E 测试环境启动脚本
# ============================================
# 用途: 启动完整的 E2E 测试环境（API + Mock 设备服务）
# 使用: ./scripts/start_e2e_env.sh [up|down|logs]
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 配置
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env.test"
PROFILE="e2e"

# 帮助信息
show_help() {
    echo "E2E 测试环境管理脚本"
    echo ""
    echo "使用方法:"
    echo "  $0 up      - 启动 E2E 测试环境"
    echo "  $0 down    - 停止并清理 E2E 测试环境"
    echo "  $0 logs    - 查看服务日志"
    echo "  $0 status  - 查看服务状态"
    echo "  $0 test    - 运行 E2E 测试"
    echo ""
    echo "包含的服务:"
    echo "  - API 服务 (端口 8001)"
    echo "  - PostgreSQL (端口 5433)"
    echo "  - Redis (端口 6380)"
    echo "  - 摄像头 Mock (端口 8003)"
    echo "  - 机械臂 Mock (端口 8004)"
    echo "  - SMT 流水线 Mock (端口 8005)"
    echo "  - SMT 进料臂 Mock (端口 8006)"
    echo "  - SMT 出料臂 Mock (端口 8007)"
    echo ""
}

# 启动环境
start_env() {
    echo -e "${GREEN}===========================================${NC}"
    echo -e "${GREEN}启动 E2E 测试环境${NC}"
    echo -e "${GREEN}===========================================${NC}"
    echo ""

    # 检查 .env.test 文件
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${RED}错误: $ENV_FILE 文件不存在${NC}"
        exit 1
    fi

    # 启动基础设施和 Mock 服务
    echo -e "${YELLOW}步骤 1/3: 启动基础设施 (PostgreSQL + Redis)${NC}"
    docker-compose --env-file "$ENV_FILE" --profile infra up -d

    # 等待基础设施就绪
    echo -e "${YELLOW}等待基础设施就绪...${NC}"
    sleep 5

    # 运行数据库迁移
    echo -e "${YELLOW}步骤 2/3: 运行数据库迁移${NC}"
    docker-compose --env-file "$ENV_FILE" run --rm api alembic upgrade head

    # 启动 API 和 Mock 服务
    echo -e "${YELLOW}步骤 3/3: 启动 API 和 Mock 服务${NC}"
    docker-compose --env-file "$ENV_FILE" --profile e2e up -d

    echo ""
    echo -e "${GREEN}===========================================${NC}"
    echo -e "${GREEN}E2E 测试环境启动完成!${NC}"
    echo -e "${GREEN}===========================================${NC}"
    echo ""
    echo "服务地址:"
    echo "  - API:        http://localhost:8001"
    echo "  - PostgreSQL: localhost:5433"
    echo "  - Redis:      localhost:6380"
    echo "  - 摄像头:     http://localhost:8003"
    echo "  - 机械臂:     http://localhost:8004"
    echo "  - SMT 流水线: http://localhost:8005"
    echo "  - SMT 进料臂: http://localhost:8006"
    echo "  - SMT 出料臂: http://localhost:8007"
    echo ""
    echo "健康检查:"
    echo "  - API:        curl http://localhost:8001/api/health"
    echo "  - 摄像头:     curl http://localhost:8003/api/v1/device/status"
    echo "  - 机械臂:     curl http://localhost:8004/api/v1/device/status"
    echo "  - SMT 流水线: curl http://localhost:8005/api/v1/device/status"
    echo "  - SMT 进料臂: curl http://localhost:8006/api/v1/device/status"
    echo "  - SMT 出料臂: curl http://localhost:8007/api/v1/device/status"
    echo ""
    echo "查看日志: $0 logs"
    echo "停止环境: $0 down"
    echo ""
}

# 停止环境
stop_env() {
    echo -e "${YELLOW}停止 E2E 测试环境...${NC}"
    docker-compose --env-file "$ENV_FILE" --profile "$PROFILE" down
    docker-compose --env-file "$ENV_FILE" --profile infra down
    echo -e "${GREEN}环境已停止${NC}"
}

# 查看日志
show_logs() {
    docker-compose --env-file "$ENV_FILE" --profile "$PROFILE" logs -f --tail=100
}

# 查看状态
show_status() {
    echo -e "${GREEN}E2E 测试环境服务状态:${NC}"
    echo ""
    docker-compose --env-file "$ENV_FILE" --profile "$PROFILE" ps
}

# 运行测试
run_tests() {
    echo -e "${GREEN}===========================================${NC}"
    echo -e "${GREEN}运行 E2E 测试${NC}"
    echo -e "${GREEN}===========================================${NC}"
    echo ""

    # 运行 E2E 测试
    docker-compose --env-file "$ENV_FILE" --profile "$PROFILE" exec -T api pytest tests/e2e/ -v --tb=short

    echo ""
    echo -e "${GREEN}测试完成!${NC}"
}

# 主逻辑
case "${1:-up}" in
    up)
        start_env
        ;;
    down)
        stop_env
        ;;
    logs)
        show_logs
        ;;
    status)
        show_status
        ;;
    test)
        run_tests
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}错误: 未知命令 '$1'${NC}"
        echo ""
        show_help
        exit 1
        ;;
esac
