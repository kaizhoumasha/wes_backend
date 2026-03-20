#!/bin/bash
#
# 系统初始化数据便捷脚本
#
# 初始化用户、角色、权限等系统基础数据。
#
# 使用方式：
#   bash scripts/data/seed_initial_data.sh
#

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo -e "${BLUE}🚀 系统初始化数据工具${NC}"
echo "=================================================================="
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR"
echo ""

cd "$BACKEND_DIR"
uv run python scripts/data/seed_initial_data.py "$@"

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ 系统初始化数据完成！${NC}"
