#!/bin/bash
#
# 清理 WES 运行时/调试数据便捷脚本(保留主数据)。
#
# 用途:开发/联调环境把历次跑流程残留的运行时数据清空,回到"只有主数据"的
# 干净状态,方便重新触发 START → SCAN_COMPLETED 观察落库。
#
# 使用方式:
#   bash scripts/data/reset_runtime_data.sh                  # dry-run 预览(不写库)
#   bash scripts/data/reset_runtime_data.sh --yes            # 真正清空运行时数据
#   bash scripts/data/reset_runtime_data.sh --yes --json     # 输出 JSON 摘要
#   bash scripts/data/reset_runtime_data.sh --yes --include-audit-logs   # 连审计日志一起清
#   bash scripts/data/reset_runtime_data.sh --yes --no-reset-mocks       # 不重置 Mock WMS
#   bash scripts/data/reset_runtime_data.sh --yes --force    # 非 APP_DEBUG 环境强制(慎用)
#
# 安全闸:
#   - 默认 dry-run,必须显式 --yes 才写库
#   - 仅 APP_DEBUG=True 时允许,生产环境需要 --force
#   - 保留 work_lines/devices/resource_*/workline_rack_positions 等主数据
#   - 默认连 Mock WMS 一起重置(否则连续重跑会撞 TARGET_POSITION_OCCUPIED)
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

# 透传所有已知参数,未知参数交给 Python 端 argparse 报错。
ALLOWED=(
    --yes
    --include-audit-logs
    --reset-mocks
    --no-reset-mocks
    --force
    --json
    --help
    -h
)
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        "${ALLOWED[@]}")
            ARGS+=("$1")
            shift
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            echo "使用方式: $0 [--yes] [--include-audit-logs] [--no-reset-mocks] [--force] [--json]"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}🧹 WES 运行时数据清理工具${NC}"
echo "=================================================================="
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR"
if [[ " ${ARGS[*]} " != *" --yes "* ]]; then
    echo -e "${YELLOW}⚠️  Dry-run 模式:仅预览,不写库。加 --yes 真正执行。${NC}"
else
    echo -e "${RED}⚠️  将真正清空运行时数据(保留主数据)。${NC}"
fi
echo ""

cd "$BACKEND_DIR"
uv run python scripts/data/reset_runtime_data.py "${ARGS[@]}"

echo ""
echo "=================================================================="
echo -e "${GREEN}✅ 完成${NC}"
