#!/bin/bash
# ============================================
# 联调服务器 NGINX 日志权限修复脚本
# ============================================
# 用途: 修正联调服务器 NGINX 容器日志目录与文件权限，
#       让 /api/v1/callback/{result,event} 的 access_log 能正确写入。
# 背景: 容器 bind-mount 的 logs/nginx 目录在某些部署步骤里
#       被创建为非 nginx 用户可写，导致 worker 进程对
#       ecs_callback_body.log / wes_backend_access.log 报
#       "Permission denied"，callback 原始 Body 日志静默丢失。
# 使用: ./scripts/fix-integration-nginx-logs.sh [选项]
# 选项:
#   --container <name>   指定 NGINX 容器名（默认 wes_nginx_int）
#   --host <user@ip>     通过 SSH 在远程主机上修复；不传则在本地 docker 执行
#   --probe              修复后用一次本地 POST 验证日志可写
#   --help               显示本帮助
# 示例:
#   ./scripts/fix-integration-nginx-logs.sh
#   ./scripts/fix-integration-nginx-logs.sh --probe
#   ./scripts/fix-integration-nginx-logs.sh --host CANTAISYS@100.94.216.118 --probe
# ============================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

CONTAINER="wes_nginx_int"
REMOTE_HOST=""
RUN_PROBE=0

show_help() {
  sed -n '2,21p' "$0"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --host) REMOTE_HOST="$2"; shift 2 ;;
    --probe) RUN_PROBE=1; shift ;;
    --help|-h) show_help; exit 0 ;;
    *) print_error "未知选项: $1"; show_help; exit 2 ;;
  esac
done

run_remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" "$1"
}

run_local() {
  eval "$1"
}

run_in_target() {
  local cmd="$1"
  if [ -n "$REMOTE_HOST" ]; then
    run_remote "$cmd"
  else
    run_local "$cmd"
  fi
}

ensure_container_running() {
  print_info "确认容器 ${CONTAINER} 处于运行状态 ..."
  run_in_target "docker inspect -f '{{.State.Running}}' ${CONTAINER} 2>/dev/null | grep -q true" \
    || { print_error "容器 ${CONTAINER} 未运行，请先 docker compose up -d"; exit 1; }
}

fix_log_dir_and_files() {
  print_info "修正 ${CONTAINER} 内的 /var/log/nginx 目录与文件属主 ..."
  run_in_target "docker exec ${CONTAINER} sh -lc 'chown -R nginx:adm /var/log/nginx; chmod 750 /var/log/nginx; find /var/log/nginx -maxdepth 1 -type f -exec chmod 640 {} +'"
  print_success "/var/log/nginx 目录与文件权限已修正为 nginx:adm (750/640)"
}

reload_nginx() {
  print_info "向 ${CONTAINER} 发送 nginx -s reload 让 master 重新 open 日志文件 ..."
  run_in_target "docker exec ${CONTAINER} nginx -s reload"
  sleep 1
  print_success "NGINX 已重新加载"
}

probe_callback_log() {
  if [ "${RUN_PROBE}" -ne 1 ]; then
    return 0
  fi
  print_info "触发一次 callback POST 验证 ecs_callback_body.log 可写 ..."
  local probe_body
  probe_body="{\"_verify\":\"fix-integration-nginx-logs-$(date -u +%Y%m%dT%H%MZ)\"}"
  local code
  code=$(run_in_target "docker exec ${CONTAINER} sh -lc \"curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data '${probe_body}' http://127.0.0.1/api/v1/callback/result\"")
  if [ "${code}" != "200" ] && [ "${code}" != "400" ] && [ "${code}" != "401" ]; then
    print_warning "callback 探测返回 ${code}，日志可能仍写不进，请人工检查 error.log"
    return 1
  fi
  sleep 1
  local tail_line
  tail_line=$(run_in_target "docker exec ${CONTAINER} tail -n 1 /var/log/nginx/ecs_callback_body.log" 2>/dev/null || true)
  if [ -z "${tail_line}" ]; then
    print_warning "ecs_callback_body.log 仍为空，请检查 NGINX error.log"
    return 1
  fi
  print_success "ecs_callback_body.log 末尾: ${tail_line}"
}

main() {
  ensure_container_running
  fix_log_dir_and_files
  reload_nginx
  probe_callback_log
  print_success "完成。"
}

main "$@"
