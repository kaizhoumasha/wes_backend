#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="${WES_BACKEND_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FRONTEND_ROOT="${WES_FRONTEND_ROOT:-$(dirname "$BACKEND_ROOT")/wes_frontend}"
REMOTE_HOST="${WES_INTEGRATION_HOST:-100.94.216.118}"
REMOTE_USER="${WES_INTEGRATION_USER:-CANTAISYS}"
REMOTE_ROOT="${WES_INTEGRATION_ROOT:-/srv/wes/app/current-single}"
SSH_KEY="${WES_INTEGRATION_SSH_KEY:-}"
HOT_BOOTSTRAP_TIMEOUT="${HOT_BOOTSTRAP_TIMEOUT:-300}"
HOT_DISABLE_TIMEOUT="${HOT_DISABLE_TIMEOUT:-300}"
HOT_CHECK_TIMEOUT="${HOT_CHECK_TIMEOUT:-60}"
HOT_CHECK_INTERVAL="${HOT_CHECK_INTERVAL:-1}"
COMMAND="${1:-}"
REMOTE_SCRIPT="$SCRIPT_DIR/integration-hot-remote.sh"
SSH_OPTIONS=(-o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2)
HOT_TEMP_DIR=""

if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTIONS+=(-i "$SSH_KEY")
fi

usage() {
    cat <<'EOF'
用法: ./scripts/integration-hot-sync.sh <bootstrap|sync|check|disable>

  bootstrap  首次启用或重建联调热更新模式，前端依赖变化后也使用此命令
  sync       增量发布当前前后端源码，依赖、migration 或热更新控制文件变化时拒绝
  check      检查联调服务器热更新模式和前后端运行状态
  disable    恢复启用热更新前的不可变前后端镜像，供正式 Jenkins 发布前使用

环境变量:
  WES_FRONTEND_ROOT          前端仓库路径，默认 ../wes_frontend
  WES_INTEGRATION_HOST       联调服务器，默认 100.94.216.118
  WES_INTEGRATION_USER       SSH 用户，默认 CANTAISYS
  WES_INTEGRATION_SSH_KEY    SSH 私钥路径
  WES_INTEGRATION_ROOT       服务器部署目录，默认 /srv/wes/app/current-single
EOF
}

remote_call() {
    local action="$1"
    shift
    ssh "${SSH_OPTIONS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
        bash -s -- "$action" "$REMOTE_ROOT" "$@" <"$REMOTE_SCRIPT"
}

require_sources() {
    [[ -f "$BACKEND_ROOT/pyproject.toml" && -f "$BACKEND_ROOT/uv.lock" && -d "$BACKEND_ROOT/src" ]] || {
        echo "后端仓库路径无效: $BACKEND_ROOT" >&2
        exit 1
    }
    [[ -f "$FRONTEND_ROOT/package.json" && -f "$FRONTEND_ROOT/pnpm-lock.yaml" && -d "$FRONTEND_ROOT/src" ]] || {
        echo "前端仓库路径无效: $FRONTEND_ROOT" >&2
        exit 1
    }
    [[ -f "$BACKEND_ROOT/docker-compose.integration-hot.yml" ]] || {
        echo "缺少联调热更新 Compose: $BACKEND_ROOT/docker-compose.integration-hot.yml" >&2
        exit 1
    }
}

fingerprint_files() {
    local repository="$1"
    shift
    python3 - "$repository" "$@" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
files: set[Path] = set()
for item in sys.argv[2:]:
    path = root / item
    if path.is_dir():
        files.update(
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix not in {".pyc", ".pyo"}
        )
    elif path.is_file():
        files.add(path)

digest = hashlib.sha256()
for path in sorted(files):
    relative = path.relative_to(root).as_posix().encode()
    content = path.read_bytes()
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
print(digest.hexdigest())
PY
}

verify_backend_image_compatibility() {
    local image_revisions="$1" image_revision
    [[ -n "$image_revisions" ]] || {
        echo "联调后端镜像缺少 revision 标签" >&2
        return 1
    }
    while IFS= read -r image_revision; do
        if ! git -C "$BACKEND_ROOT" cat-file -e "${image_revision}^{commit}" 2>/dev/null; then
            echo "本地仓库缺少联调后端镜像 revision: $image_revision，请先 fetch 对应提交" >&2
            return 1
        fi
        if ! git -C "$BACKEND_ROOT" diff --quiet "$image_revision" -- pyproject.toml uv.lock migrations; then
            echo "后端依赖或 migration 与联调服务器基础镜像不一致；请先完成一次正式镜像/数据库发布" >&2
            return 1
        fi
    done <<<"$image_revisions"
    if [[ -n "$(git -C "$BACKEND_ROOT" ls-files --others --exclude-standard -- migrations)" ]]; then
        echo "存在未跟踪 migration；源码热更新已拒绝" >&2
        return 1
    fi
}

create_archives() {
    local temp_dir="$1"
    local frontend_paths=(src index.html vite.config.ts tsconfig.json package.json pnpm-lock.yaml .npmrc)
    [[ -d "$FRONTEND_ROOT/public" ]] && frontend_paths+=(public)
    [[ -f "$FRONTEND_ROOT/tailwind.config.js" ]] && frontend_paths+=(tailwind.config.js)
    tar -czf "$temp_dir/backend.tar.gz" -C "$BACKEND_ROOT" \
        src deployment workline_plugins/rough_sorter/src main.py \
        scripts/frontend-dev-entrypoint.sh docker-compose.integration-hot.yml
    tar -czf "$temp_dir/frontend.tar.gz" -C "$FRONTEND_ROOT" "${frontend_paths[@]}"
}

sync_sources() {
    local mode="$1" probe image_revision image_protected_sha deploy_layout hot_mode
    local backend_sha frontend_sha control_sha release_id remote_upload
    probe="$(remote_call probe)"
    image_revision="$(awk -F= '$1 == "BACKEND_REVISION" {print $2}' <<<"$probe")"
    image_protected_sha="$(awk -F= '$1 == "BACKEND_PROTECTED_SHA" {print $2}' <<<"$probe")"
    deploy_layout="$(awk -F= '$1 == "DEPLOY_LAYOUT" {print $2}' <<<"$probe")"
    hot_mode="$(awk -F= '$1 == "HOT_MODE" {print $2}' <<<"$probe")"
    if [[ "$mode" == sync && "$hot_mode" != true ]]; then
        echo "当前服务器未启用热更新模式，请执行 bootstrap" >&2
        return 1
    fi
    if [[ "$deploy_layout" != integration ]]; then
        verify_backend_image_compatibility "$image_revision"
    fi

    backend_sha="$(fingerprint_files "$BACKEND_ROOT" pyproject.toml uv.lock migrations)"
    frontend_sha="$(fingerprint_files "$FRONTEND_ROOT" package.json pnpm-lock.yaml .npmrc)"
    control_sha="$(fingerprint_files \
        "$BACKEND_ROOT" main.py docker-compose.integration-hot.yml scripts/frontend-dev-entrypoint.sh \
        src/celery_app/dev_worker_autoreload.sh src/celery_app/dev_beat_autoreload.sh \
        src/celery_app/dev_reload_fingerprint.sh)"
    if [[ "$deploy_layout" == integration ]]; then
        [[ -n "$image_protected_sha" ]] || {
            echo "联调后端镜像未返回依赖与 migration 指纹" >&2
            return 1
        }
        while IFS= read -r image_sha; do
            [[ "$image_sha" == "$backend_sha" ]] || {
                echo "本机依赖或 migration 与联调后端运行镜像内容不一致；请先完成一次正式发布" >&2
                return 1
            }
        done <<<"$image_protected_sha"
    fi
    release_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    HOT_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wes-integration-hot.XXXXXX")"
    cleanup() {
        [[ -n "$HOT_TEMP_DIR" && "$HOT_TEMP_DIR" == *wes-integration-hot.* ]] && rm -rf -- "$HOT_TEMP_DIR"
    }
    trap cleanup EXIT

    create_archives "$HOT_TEMP_DIR"
    remote_upload="$(remote_call prepare "$release_id")"
    scp "${SSH_OPTIONS[@]}" "$HOT_TEMP_DIR/backend.tar.gz" "${REMOTE_USER}@${REMOTE_HOST}:${remote_upload}/backend.tar.gz"
    scp "${SSH_OPTIONS[@]}" "$HOT_TEMP_DIR/frontend.tar.gz" "${REMOTE_USER}@${REMOTE_HOST}:${remote_upload}/frontend.tar.gz"
    remote_call activate "$release_id" "$mode" "$backend_sha" "$frontend_sha" "$control_sha" \
        "$HOT_BOOTSTRAP_TIMEOUT"
    remote_call check "$HOT_CHECK_TIMEOUT" "$HOT_CHECK_INTERVAL"
    echo "联调服务器前后端源码已更新: $release_id"
}

case "$COMMAND" in
    bootstrap)
        require_sources
        sync_sources bootstrap
        ;;
    sync)
        require_sources
        sync_sources sync
        ;;
    check)
        remote_call check "$HOT_CHECK_TIMEOUT" "$HOT_CHECK_INTERVAL"
        ;;
    disable)
        remote_call disable "$HOT_DISABLE_TIMEOUT"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
