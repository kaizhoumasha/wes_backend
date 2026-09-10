#!/usr/bin/env bash

set -euo pipefail

ACTION="${1:-}"
DEPLOY_ROOT="${2:-}"
STATE_DIR="$DEPLOY_ROOT/.integration-hot"
SOURCE_DIR="$STATE_DIR/source"
BASELINE_FILE="$STATE_DIR/baseline.env"
DEPLOY_LAYOUT=""
API_CONTAINER=""
CELERY_CONTAINER=""
FULFILLMENT_CONTAINER=""
BEAT_CONTAINER=""
FRONTEND_CONTAINER=""
NGINX_CONTAINER=""
HOT_SERVICES=()

fail() {
    echo "$1" >&2
    exit 1
}

require_deploy_root() {
    [[ -n "$DEPLOY_ROOT" && "$DEPLOY_ROOT" == /* && "$DEPLOY_ROOT" != / ]] || fail "联调部署目录无效"
    if [[ -f "$DEPLOY_ROOT/.env.integration" && -x "$DEPLOY_ROOT/compose.sh" ]]; then
        DEPLOY_LAYOUT=integration
        API_CONTAINER=wes_api_int
        CELERY_CONTAINER=wes_integration-celery-1
        FULFILLMENT_CONTAINER=wes_integration-celery-wms-fulfillment-1
        BEAT_CONTAINER=wes_celery_beat_int
        FRONTEND_CONTAINER=wes_integration-frontend-1
        NGINX_CONTAINER=wes_nginx_int
        HOT_SERVICES=(api celery celery-wms-fulfillment celery_beat frontend nginx)
    elif [[ -f "$DEPLOY_ROOT/.env" && -f "$DEPLOY_ROOT/docker-compose.test-deploy.yml" ]]; then
        DEPLOY_LAYOUT=test
        API_CONTAINER=wes_api_test
        CELERY_CONTAINER=wes_backend_test-celery-1
        FULFILLMENT_CONTAINER=wes_backend_test-celery-wms-fulfillment-1
        BEAT_CONTAINER=wes_celery_beat_test
        FRONTEND_CONTAINER=wes_frontend_test
        NGINX_CONTAINER=wes_nginx_test
        HOT_SERVICES=(api celery celery-wms-fulfillment celery_beat frontend nginx)
    else
        fail "联调部署目录缺少 compose.sh/.env.integration 或 TEST Compose: $DEPLOY_ROOT"
    fi
}

compose() {
    local backend_image
    if [[ "$DEPLOY_LAYOUT" == integration ]]; then
        HOT_BACKEND_ROOT="$SOURCE_DIR/backend" \
        HOT_FRONTEND_ROOT="$SOURCE_DIR/frontend" \
            "$DEPLOY_ROOT/compose.sh" -f "$SOURCE_DIR/backend/docker-compose.integration-hot.yml" "$@"
    else
        backend_image="$(docker inspect "$API_CONTAINER" --format '{{.Config.Image}}')"
        [[ -n "$backend_image" ]] || fail "无法识别当前后端基础镜像"
        BACKEND_IMAGE="$backend_image" \
        HOT_BACKEND_ROOT="$SOURCE_DIR/backend" \
        HOT_FRONTEND_ROOT="$SOURCE_DIR/frontend" \
            docker compose --project-directory "$DEPLOY_ROOT" --env-file "$DEPLOY_ROOT/.env" \
            -f "$DEPLOY_ROOT/docker-compose.test-deploy.yml" \
            -f "$SOURCE_DIR/backend/docker-compose.integration-hot.yml" "$@"
    fi
}

immutable_compose() {
    if [[ "$DEPLOY_LAYOUT" == integration ]]; then
        "$DEPLOY_ROOT/compose.sh" "$@"
    else
        BACKEND_IMAGE="$BASE_BACKEND_IMAGE" \
        FRONTEND_IMAGE="$BASE_FRONTEND_IMAGE" \
            docker compose --project-directory "$DEPLOY_ROOT" --env-file "$DEPLOY_ROOT/.env" \
            -f "$DEPLOY_ROOT/docker-compose.test-deploy.yml" "$@"
    fi
}

validate_image_reference() {
    [[ "$1" =~ ^[A-Za-z0-9._/@:+-]+$ ]] || fail "保存的基础镜像引用无效"
}

validate_archive() {
    local archive="$1" entry
    [[ -s "$archive" ]] || fail "热更新上传包不存在: $archive"
    while IFS= read -r entry; do
        case "$entry" in
            /*|../*|*/../*) fail "热更新上传包包含越界路径: $entry" ;;
        esac
    done < <(tar -tzf "$archive")
}

container_protected_fingerprint() {
    docker exec -i "$1" python - /app pyproject.toml uv.lock migrations <<'PY'
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

sync_tree() {
    local source="$1" destination="$2" preserve="${3:-}"
    python3 - "$source" "$destination" "$preserve" <<'PY'
import os
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
preserve = sys.argv[3]
destination.mkdir(parents=True, exist_ok=True)
source_paths = {path.relative_to(source) for path in source.rglob("*")}

for path in sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True):
    relative = path.relative_to(destination)
    if preserve and relative.parts[0] == preserve:
        continue
    if relative not in source_paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()

for path in sorted(source.rglob("*"), key=lambda item: len(item.parts)):
    relative = path.relative_to(source)
    target = destination / relative
    if path.is_symlink():
        raise SystemExit(f"热更新源码不允许符号链接: {relative}")
    if path.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not target.is_symlink() and target.read_bytes() == path.read_bytes():
        continue
    temporary = target.with_name(f".{target.name}.wes-hot-{os.getpid()}")
    shutil.copy2(path, temporary)
    os.replace(temporary, target)
PY
}

activate() {
    local release_id="$3" mode="$4" backend_sha="$5" frontend_sha="$6" control_sha="$7"
    local bootstrap_timeout="${8:-${HOT_BOOTSTRAP_TIMEOUT:-300}}"
    local upload_dir="$STATE_DIR/uploads/$release_id" base_backend_image base_frontend_image
    local extract_dir="$upload_dir/extracted"
    require_deploy_root
    case "$mode" in
        bootstrap) ;;
        sync)
            [[ -f "$BASELINE_FILE" ]] || fail "联调热更新尚未 bootstrap"
            # 文件只包含本工具写入的十六进制摘要。
            # shellcheck disable=SC1090
            source "$BASELINE_FILE"
            if [[ "$backend_sha" != "$BACKEND_PROTECTED_SHA" \
                || "$frontend_sha" != "$FRONTEND_PROTECTED_SHA" \
                || "$control_sha" != "$CONTROL_SHA" ]]; then
                fail "受保护输入已变化，请重新执行 bootstrap"
            fi
            ;;
        *) fail "未知激活模式: $mode" ;;
    esac

    validate_archive "$upload_dir/backend.tar.gz"
    validate_archive "$upload_dir/frontend.tar.gz"
    mkdir -p "$extract_dir/backend" "$extract_dir/frontend" "$SOURCE_DIR/backend" "$SOURCE_DIR/frontend"
    tar -xzf "$upload_dir/backend.tar.gz" -C "$extract_dir/backend"
    tar -xzf "$upload_dir/frontend.tar.gz" -C "$extract_dir/frontend"
    [[ -f "$extract_dir/backend/main.py" && -f "$extract_dir/backend/docker-compose.integration-hot.yml" ]] \
        || fail "后端热更新包不完整"
    [[ -f "$extract_dir/frontend/package.json" && -f "$extract_dir/frontend/src/main.ts" ]] \
        || fail "前端热更新包不完整"

    sync_tree "$extract_dir/backend" "$SOURCE_DIR/backend"
    sync_tree "$extract_dir/frontend" "$SOURCE_DIR/frontend" node_modules
    mkdir -p "$SOURCE_DIR/frontend/node_modules"
    if [[ "$mode" == bootstrap ]]; then
        if [[ -f "$BASELINE_FILE" ]]; then
            # shellcheck disable=SC1090
            source "$BASELINE_FILE"
            base_backend_image="$BASE_BACKEND_IMAGE"
            base_frontend_image="$BASE_FRONTEND_IMAGE"
        else
            base_backend_image="$(docker inspect "$API_CONTAINER" --format '{{.Config.Image}}')"
            base_frontend_image="$(docker inspect "$FRONTEND_CONTAINER" --format '{{.Config.Image}}')"
        fi
        validate_image_reference "$base_backend_image"
        validate_image_reference "$base_frontend_image"
        printf 'BACKEND_PROTECTED_SHA=%s\nFRONTEND_PROTECTED_SHA=%s\nCONTROL_SHA=%s\n' \
            "$backend_sha" "$frontend_sha" "$control_sha" >"$BASELINE_FILE"
        printf 'BASE_BACKEND_IMAGE=%q\nBASE_FRONTEND_IMAGE=%q\n' \
            "$base_backend_image" "$base_frontend_image" >>"$BASELINE_FILE"
    fi
    if [[ "$mode" == bootstrap ]]; then
        [[ "$bootstrap_timeout" =~ ^[1-9][0-9]*$ ]] || fail "HOT_BOOTSTRAP_TIMEOUT 必须是正整数"
        compose up -d --no-build --force-recreate --wait --wait-timeout "$bootstrap_timeout" \
            "${HOT_SERVICES[@]}"
    else
        # 先终止旧进程，再由完整同步后的源码启动；后续探针不能命中旧版本。
        compose restart --timeout 30 api celery celery-wms-fulfillment celery_beat frontend
    fi
    rm -rf -- "$upload_dir"
}

disable_hot_mode() {
    local disable_timeout="${3:-${HOT_DISABLE_TIMEOUT:-300}}"
    require_deploy_root
    [[ -f "$BASELINE_FILE" ]] || fail "联调热更新尚未 bootstrap"
    # shellcheck disable=SC1090
    source "$BASELINE_FILE"
    validate_image_reference "$BASE_BACKEND_IMAGE"
    validate_image_reference "$BASE_FRONTEND_IMAGE"
    [[ "$disable_timeout" =~ ^[1-9][0-9]*$ ]] || fail "HOT_DISABLE_TIMEOUT 必须是正整数"
    immutable_compose up -d --no-build --force-recreate --wait --wait-timeout "$disable_timeout" \
        "${HOT_SERVICES[@]}"
    rm -f -- "$BASELINE_FILE"
    echo "联调服务器已恢复不可变镜像模式"
}

runtime_ready() {
    local deadline="$1" remaining states state probe_failed=0 pid
    local probe_pids=()
    remaining=$((deadline - SECONDS))
    ((remaining > 0)) || return 1
    states="$(timeout "$remaining" docker inspect \
        "$API_CONTAINER" \
        "$CELERY_CONTAINER" \
        "$FULFILLMENT_CONTAINER" \
        "$BEAT_CONTAINER" \
        "$FRONTEND_CONTAINER" \
        "$NGINX_CONTAINER" \
        --format '{{.State.Status}}' 2>/dev/null)" || return 1
    while IFS= read -r state; do
        [[ "$state" == running ]] || return 1
    done <<<"$states"

    remaining=$((deadline - SECONDS))
    ((remaining > 0)) || return 1
    timeout "$remaining" docker exec "$API_CONTAINER" \
        curl -fsS http://127.0.0.1:8001/ready >/dev/null 2>&1 &
    probe_pids+=("$!")
    timeout "$remaining" docker exec "$CELERY_CONTAINER" \
        python /app/src/celery_app/worker_healthcheck.py >/dev/null 2>&1 &
    probe_pids+=("$!")
    timeout "$remaining" docker exec "$FULFILLMENT_CONTAINER" \
        python /app/src/celery_app/worker_healthcheck.py >/dev/null 2>&1 &
    probe_pids+=("$!")
    timeout "$remaining" docker exec "$BEAT_CONTAINER" \
        python /app/src/celery_app/beat_healthcheck.py >/dev/null 2>&1 &
    probe_pids+=("$!")
    timeout "$remaining" docker exec "$FRONTEND_CONTAINER" node -e \
        "fetch('http://127.0.0.1:5173/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" \
        >/dev/null 2>&1 &
    probe_pids+=("$!")
    timeout "$remaining" docker exec "$NGINX_CONTAINER" \
        wget -q --spider http://127.0.0.1/health >/dev/null 2>&1 &
    probe_pids+=("$!")
    for pid in "${probe_pids[@]}"; do
        wait "$pid" || probe_failed=1
    done
    ((probe_failed == 0))
}

check_hot_mode() {
    local mounts deadline timeout interval
    require_deploy_root
    [[ -f "$BASELINE_FILE" ]] || fail "联调热更新尚未 bootstrap"
    mounts="$(docker inspect "$API_CONTAINER" --format '{{range .Mounts}}{{println .Destination}}{{end}}')"
    grep -Fxq /app/src <<<"$mounts" || fail "当前 API 未挂载热更新源码，请重新执行 bootstrap"
    timeout="${3:-${HOT_CHECK_TIMEOUT:-60}}"
    interval="${4:-${HOT_CHECK_INTERVAL:-1}}"
    [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || fail "HOT_CHECK_TIMEOUT 必须是正整数"
    [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "HOT_CHECK_INTERVAL 必须是非负数"
    command -v timeout >/dev/null 2>&1 || fail "联调服务器缺少 timeout 命令"
    deadline=$((SECONDS + timeout))
    while true; do
        if runtime_ready "$deadline"; then
            echo "联调服务器热更新模式检查通过"
            return 0
        fi
        ((SECONDS >= deadline)) && break
        sleep "$interval"
    done
    fail "联调容器在 ${timeout} 秒总超时内仍未全部就绪"
}

case "$ACTION" in
    probe)
        require_deploy_root
        printf 'DEPLOY_LAYOUT=%s\n' "$DEPLOY_LAYOUT"
        for container in "$API_CONTAINER" "$CELERY_CONTAINER" "$FULFILLMENT_CONTAINER" "$BEAT_CONTAINER"; do
            printf 'BACKEND_REVISION=%s\n' \
                "$(docker inspect "$container" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
            if [[ "$DEPLOY_LAYOUT" == integration ]]; then
                printf 'BACKEND_PROTECTED_SHA=%s\n' "$(container_protected_fingerprint "$container")"
            fi
        done
        if docker inspect "$API_CONTAINER" --format '{{range .Mounts}}{{println .Destination}}{{end}}' | grep -Fxq /app/src; then
            echo "HOT_MODE=true"
        else
            echo "HOT_MODE=false"
        fi
        ;;
    prepare)
        release_id="$3"
        require_deploy_root
        [[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ || "$release_id" == test-release ]] \
            || fail "热更新 release id 无效"
        upload_dir="$STATE_DIR/uploads/$release_id"
        mkdir -p "$upload_dir"
        printf '%s\n' "$upload_dir"
        ;;
    activate) activate "$@" ;;
    check) check_hot_mode "$@" ;;
    disable) disable_hot_mode "$@" ;;
    *) fail "未知远端操作: $ACTION" ;;
esac
