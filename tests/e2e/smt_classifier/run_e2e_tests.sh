#!/bin/bash
#
# SMT 粗分机 E2E 测试运行脚本
#
# 用法:
#   ./run_e2e_tests.sh              # 运行所有 E2E 测试
#   ./run_e2e_tests.sh --setup      # 仅设置环境（生成 .env.e2e）
#   ./run_e2e_tests.sh --seed       # 仅初始化数据库
#   ./run_e2e_tests.sh --full       # 完整流程：设置 + 种子 + 测试
#   ./run_e2e_tests.sh -v           # 详细输出
#   ./run_e2e_tests.sh -k <pattern> # 运行特定测试
#

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_DIR="tests/e2e/smt_classifier"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  SMT 粗分机 E2E 测试运行器${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 解析参数
SETUP_ONLY=false
SEED_ONLY=false
FULL_FLOW=false
PYTEST_ARGS="-m e2e"

while [[ $# -gt 0 ]]; do
    case $1 in
        --setup)
            SETUP_ONLY=true
            shift
            ;;
        --seed)
            SEED_ONLY=true
            shift
            ;;
        --full)
            FULL_FLOW=true
            shift
            ;;
        -v|--verbose)
            PYTEST_ARGS="-v $PYTEST_ARGS"
            shift
            ;;
        -k)
            PYTEST_ARGS="$PYTEST_ARGS -k $2"
            shift 2
            ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $1"
            shift
            ;;
    esac
done

# 检查 WES 服务是否运行
check_wes_running() {
    echo -e "${BLUE}检查 WES 服务状态...${NC}"
    if curl -s http://localhost:8001/api/v1/admin/performance/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ WES 服务已启动${NC}"
        return 0
    else
        echo -e "${YELLOW}⚠ WES 服务未启动，请先运行: uvicorn main:app --reload${NC}"
        return 1
    fi
}

# 设置环境
setup_env() {
    echo -e "${BLUE}步骤 1: 设置 E2E 测试环境...${NC}"
    uv run python "$E2E_DIR/setup_e2e_app.py"
    echo ""
}

# 初始化数据库
seed_database() {
    echo -e "${BLUE}步骤 2: 初始化 E2E 测试数据...${NC}"
    uv run python scripts/data/seed_e2e_test_data.py
    echo ""
}

# 运行 E2E 测试
run_tests() {
    echo -e "${BLUE}步骤 3: 运行 E2E 测试...${NC}"
    echo -e "${BLUE}pytest 参数: $PYTEST_ARGS${NC}"
    echo ""

    # 加载环境变量并运行测试
    if [ -f "$E2E_DIR/.env.e2e" ]; then
        export $(grep -v '^#' "$E2E_DIR/.env.e2e" | xargs)
        echo -e "${GREEN}✓ 已加载环境变量: $E2E_DIR/.env.e2e${NC}"
    fi

    uv run pytest "$E2E_DIR" $PYTEST_ARGS
}

# 主流程
main() {
    cd "$SCRIPT_DIR"

    if [ "$SETUP_ONLY" = true ]; then
        setup_env
        exit 0
    fi

    if [ "$SEED_ONLY" = true ]; then
        seed_database
        exit 0
    fi

    # 完整流程或仅运行测试
    if [ "$FULL_FLOW" = true ]; then
        setup_env
        seed_database
    fi

    # 检查 WES 服务
    check_wes_running || exit 1

    # 运行测试
    run_tests

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  E2E 测试完成!${NC}"
    echo -e "${GREEN}========================================${NC}"
}

main
