#!/bin/bash
# SMT 粗分机完整端到端测试启动脚本

set -e

echo "========================================"
echo "SMT 粗分机完整端到端测试"
echo "========================================"
echo ""

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检查端口是否被占用
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# 等待端口可用
wait_for_port() {
    local port=$1
    local service=$2
    local max_wait=${3:-30}

    echo -n "等待 $service (端口 $port)..."
    for i in $(seq 1 $max_wait); do
        if check_port $port; then
            echo -e "${GREEN}✓${NC}"
            return 0
        fi
        sleep 1
    done
    echo -e "${RED}✗ 超时${NC}"
    return 1
}

# 检查基础设施
echo "步骤 1: 检查基础设施"
echo "----------------------------------------"

if ! command_exists docker-compose; then
    echo -e "${RED}✗ docker-compose 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✓ docker-compose 已安装${NC}"

if ! docker-compose ps | grep -q "Up"; then
    echo -e "${YELLOW}启动 Docker 基础设施...${NC}"
    docker-compose up -d
    sleep 5
fi
echo -e "${GREEN}✓ Docker 基础设施运行中${NC}"

# 检查 WES Backend
echo ""
echo "步骤 2: 检查 WES Backend"
echo "----------------------------------------"

# 先尝试通过健康检查端点验证
if curl -sf http://localhost:8001/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓ WES Backend 运行中 (端口 8001)${NC}"
elif check_port 8001; then
    echo -e "${GREEN}✓ WES Backend 端口监听中 (端口 8001)${NC}"
else
    echo -e "${YELLOW}⚠ WES Backend 未运行${NC}"
    echo ""
    echo "请启动 WES Backend:"
    echo "  方式 1 (Docker): docker-compose up -d wes_api"
    echo "  方式 2 (本地):   uv run uvicorn main:app --reload --port 8001"
    echo ""
    echo "或者使用 --start-wes 参数自动启动"
    exit 1
fi

# 检查 Celery Worker
echo ""
echo "步骤 3: 检查 Celery Worker"
echo "----------------------------------------"

# 检查 Docker 容器中的 Celery Worker
if docker ps --format '{{.Names}}' | grep -q celery; then
    # 检查容器是否健康
    if docker ps --filter "name=celery" --filter "health=healthy" --format '{{.Names}}' | grep -q celery; then
        echo -e "${GREEN}✓ Celery Worker 运行中 (Docker 容器)${NC}"
    else
        echo -e "${YELLOW}⚠ Celery Worker 容器存在但未健康${NC}"
        echo "  检查容器状态: docker ps --filter 'name=celery'"
        exit 1
    fi
elif pgrep -f "celery.*worker" > /dev/null; then
    # 检查本地进程中的 Celery Worker
    echo -e "${GREEN}✓ Celery Worker 运行中 (本地进程)${NC}"
else
    echo -e "${YELLOW}⚠ Celery Worker 未运行${NC}"
    echo ""
    echo "请在另一个终端启动 Celery Worker:"
    echo "  方式 1 (Docker): docker-compose up -d celery_worker"
    echo "  方式 2 (本地):   uv run celery -A src.celery_app.app worker --loglevel=info"
    echo ""
    echo "或者使用 --start-celery 参数自动启动"
    exit 1
fi

# 检查 Mock 服务
echo ""
echo "步骤 4: 检查 Mock 服务"
echo "----------------------------------------"

MOCK_RUNNING=true
for port in 8005 8006 8007 8008 8009; do
    if ! check_port $port; then
        MOCK_RUNNING=false
        break
    fi
done

if $MOCK_RUNNING; then
    echo -e "${GREEN}✓ Mock 服务运行中${NC}"
else
    echo -e "${YELLOW}启动 Mock 服务...${NC}"
    python tests/mock/smt_classifier/run_all.py &
    MOCK_PID=$!
    sleep 5

    # 等待所有端口就绪
    wait_for_port 8005 "Pipeline Mock" 10
    wait_for_port 8006 "ARM01 Mock" 10
    wait_for_port 8007 "ARM02 Mock" 10
fi

# 运行测试
echo ""
echo "步骤 5: 运行完整端到端测试"
echo "========================================"
echo ""

uv run pytest tests/e2e/smt_classifier/test_full_e2e_chain.py -xvs

echo ""
echo "========================================"
echo -e "${GREEN}✓ 测试完成${NC}"
echo "========================================"
