#!/bin/bash
#
# 菜单同步便捷脚本
#
# 直接解析前端 src/router/index.ts，并同步到后端数据库。
#
# 使用方式：
#   bash scripts/sync_menus.sh
#   bash scripts/sync_menus.sh --dry-run
#   bash scripts/sync_menus.sh --preview
#   bash scripts/sync_menus.sh --frontend-path /path/to/wes_frontend
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$(dirname "$BACKEND_DIR")/wes_frontend"

ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --frontend-path)
            if [[ -z "${2:-}" ]]; then
                echo -e "${RED}❌ --frontend-path 缺少目录参数${NC}"
                exit 1
            fi
            FRONTEND_DIR="$2"
            ARGS+=("$1" "$2")
            shift 2
            ;;
        --dry-run|--preview)
            ARGS+=("$1")
            shift
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            echo "使用方式: $0 [--dry-run] [--preview] [--frontend-path /path/to/wes_frontend]"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}🚀 菜单同步工具${NC}"
echo "=================================================================="
echo -e "${BLUE}📦 前端目录:${NC} $FRONTEND_DIR"
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR"
echo ""

if [[ ! -d "$FRONTEND_DIR" ]]; then
    echo -e "${RED}❌ 前端目录不存在: $FRONTEND_DIR${NC}"
    exit 1
fi

if [[ ! -f "$FRONTEND_DIR/src/router/index.ts" ]]; then
    echo -e "${RED}❌ 前端路由文件不存在: $FRONTEND_DIR/src/router/index.ts${NC}"
    exit 1
fi

cd "$BACKEND_DIR"
uv run python scripts/sync_menus_from_frontend.py "${ARGS[@]}"

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ 菜单同步完成！${NC}"
