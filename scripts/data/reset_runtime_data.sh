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
#   bash scripts/data/reset_runtime_data.sh --transport-task-id transport-... --yes
#
# 安全闸:
#   - 默认 dry-run,必须显式 --yes 才写库
#   - 全量模式仅 APP_DEBUG=True 时允许；生产型配置需要 --force
#   - 定向模式不重置 Mock WMS，只删除命中安全条件的单个 TransportTask
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
ARGS=()
JSON_MODE=false
TRANSPORT_TASK_ID=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --transport-task-id)
            if [[ $# -lt 2 || -z "${2//[[:space:]]/}" ]]; then
                echo -e "${RED}--transport-task-id 缺少任务 ID${NC}" >&2
                exit 1
            fi
            TRANSPORT_TASK_ID="$2"
            ARGS+=("$1" "$2")
            shift 2
            ;;
        --yes|--include-audit-logs|--reset-mocks|--no-reset-mocks|--force|--json|--help|-h)
            ARGS+=("$1")
            if [[ "$1" == "--json" ]]; then
                JSON_MODE=true
            fi
            shift
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}" >&2
            echo "使用方式: $0 [--yes] [--transport-task-id ID] [--include-audit-logs] [--no-reset-mocks] [--force] [--json]" >&2
            exit 1
            ;;
    esac
done

# JSON 模式保留 Python stdout 为单一机器可读文档；wrapper 装饰信息改走 stderr。
if [[ "$JSON_MODE" == true ]]; then
    exec 3>&2
else
    exec 3>&1
fi

echo -e "${BLUE}🧹 WES 运行时数据清理工具${NC}" >&3
echo "==================================================================" >&3
echo -e "${BLUE}📦 后端目录:${NC} $BACKEND_DIR" >&3
if [[ -n "$TRANSPORT_TASK_ID" ]]; then
    echo -e "${BLUE}🎯 定向 TransportTask:${NC} $TRANSPORT_TASK_ID" >&3
fi
if [[ " ${ARGS[*]} " != *" --yes "* ]]; then
    echo -e "${YELLOW}⚠️  Dry-run 模式:仅预览,不写库。加 --yes 真正执行。${NC}" >&3
elif [[ -n "$TRANSPORT_TASK_ID" ]]; then
    echo -e "${RED}⚠️  将定向清理一个符合安全条件的 TransportTask。${NC}" >&3
else
    echo -e "${RED}⚠️  将真正清空运行时数据(保留主数据)。${NC}" >&3
fi
echo "" >&3

cd "$BACKEND_DIR"
uv run python scripts/data/reset_runtime_data.py "${ARGS[@]}"

echo "" >&3
echo "==================================================================" >&3
echo -e "${GREEN}✅ 完成${NC}" >&3
