#!/bin/bash
# ============================================
# SMT 粗分机 Mock 服务启动脚本（本地开发）
# ============================================
# 用途: 在本地开发环境启动 Mock 服务
# 使用: ./tests/mock/smt_classifier/start_local.sh
#
# 默认使用 localhost:8001 作为 WES 回调地址
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 获取脚本所在目录和项目根目录
# 脚本位置: tests/mock/smt_classifier/start_local.sh
# 需要往上 3 层到达项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# 本地开发环境变量（覆盖可能的 .env.test 配置）
export WES_EVENT_CALLBACK_URL="${WES_EVENT_CALLBACK_URL:-http://localhost:8001/api/v1/callback/event}"
export WES_RESULT_CALLBACK_URL="${WES_RESULT_CALLBACK_URL:-http://localhost:8001/api/v1/callback/result}"

# API 认证凭据
export API_APP_ID="${API_APP_ID:-app_Gqnvr3dpjGwlrjtO}"
export API_APP_SECRET="${API_APP_SECRET:-sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao}"

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}SMT 粗分机 Mock 服务启动（本地开发）${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "项目根目录: $PROJECT_ROOT"
echo "WES 事件回调: $WES_EVENT_CALLBACK_URL"
echo "WES 结果回调: $WES_RESULT_CALLBACK_URL"
echo ""

# 检查 WES 是否运行
echo -e "${YELLOW}检查 WES 服务...${NC}"
if curl -s http://localhost:8001/api/v1/admin/performance/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓ WES 服务运行正常 (localhost:8001)${NC}"
else
    echo -e "${RED}✗ WES 服务未运行，请先启动:${NC}"
    echo "  docker compose --env-file .env.dev --profile dev up -d"
    echo "  或"
    echo "  uv run uvicorn main:app --reload --port 8001"
    exit 1
fi

echo ""
echo -e "${YELLOW}启动 Mock 服务...${NC}"
echo ""

# 切换到项目根目录运行
cd "$PROJECT_ROOT"
uv run python tests/mock/smt_classifier/run_all.py