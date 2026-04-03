#!/bin/bash
#
# 生产首个超级管理员 bootstrap 便捷脚本
#
# 仅在系统中尚无超级管理员时创建首个超级管理员；
# 若已存在超级管理员，则安全跳过，不覆盖现有账号。
#
# 使用方式：
#   export BOOTSTRAP_ADMIN_USERNAME=admin
#   export BOOTSTRAP_ADMIN_PASSWORD='StrongPassw0rd!'
#   export BOOTSTRAP_ADMIN_FULL_NAME='系统管理员'
#   export BOOTSTRAP_ADMIN_EMAIL='admin@example.com'
#   bash scripts/data/bootstrap_admin.sh
#

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo -e "${BLUE}🚀 超级管理员 bootstrap 工具${NC}"
echo "=================================================================="
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR"
echo ""

cd "$BACKEND_DIR"
uv run python scripts/data/bootstrap_admin.py "$@"

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ 超级管理员 bootstrap 执行完成！${NC}"
