#!/bin/bash
#
# E2E 测试数据便捷脚本
#
# 初始化 E2E 测试所需的作业线、设备和 API 应用数据。
#
# 使用方式：
#   bash scripts/data/seed_e2e_test_data.sh
#

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo -e "${BLUE}🚀 E2E 测试数据工具${NC}"
echo "=================================================================="
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR"
echo ""

cd "$BACKEND_DIR"
uv run python scripts/data/seed_e2e_test_data.py "$@"

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ E2E 测试数据初始化完成！${NC}"
