#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

show_help() {
    cat <<'EOF'
Live / manual 测试入口

用法:
  ./scripts/test_live_suite.sh signature   # 运行 API 签名 live 用例
  ./scripts/test_live_suite.sh redis       # 运行 Redis 手工降级演练
  ./scripts/test_live_suite.sh all         # 顺序运行全部 opt-in 用例

说明:
  - signature: 需要本地 WES 服务 + 已存在的 API app seed 数据
  - redis: 需要交互式终端，过程中会提示你手动停/启 Redis
EOF
}

run_signature() {
    echo "==> Running live signature tests"
    RUN_LIVE_API_SIGNATURE_TESTS=1 PYTHONPATH=. uv run pytest -m live tests/api/test_signature.py -v --tb=short
}

run_redis() {
    if [ ! -t 0 ]; then
        echo "error: redis manual test requires an interactive TTY" >&2
        exit 1
    fi
    echo "==> Running manual Redis degradation drill"
    RUN_REDIS_DEGRADATION=1 PYTHONPATH=. uv run pytest -m "live and manual" tests/resilience/test_redis_degradation.py -v --tb=short
}

case "${1:-help}" in
    signature)
        run_signature
        ;;
    redis)
        run_redis
        ;;
    all)
        run_signature
        run_redis
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "error: unknown command '${1:-}'" >&2
        echo
        show_help
        exit 1
        ;;
esac
