#!/bin/bash
#
# 权限同步便捷脚本
#
# 直接扫描后端路由中的 RequirePermission / RequireAPIPermission，
# 同步到 permissions 表，并按内置规则补齐角色权限。
#
# 使用方式：
#   bash scripts/sync_permissions.sh
#   bash scripts/sync_permissions.sh --dry-run
#   bash scripts/sync_permissions.sh --preview
#   bash scripts/sync_permissions.sh --permissions-only
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--preview|--permissions-only)
            ARGS+=("$1")
            shift
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            echo "使用方式: $0 [--dry-run] [--preview] [--permissions-only]"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}🚀 权限同步工具${NC}"
echo "=================================================================="
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR"
echo ""

cd "$BACKEND_DIR"
uv run python scripts/sync_permissions.py "${ARGS[@]}"

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ 权限同步完成！${NC}"
