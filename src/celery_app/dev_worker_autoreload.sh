#!/bin/sh
set -eu

# Development-only auto-restart wrapper for Celery worker.
# It watches python source files and restarts the worker process on changes.

DEFAULT_WATCH_PATHS="/app/src /app/deployment /app/workline_plugins/rough_sorter/src"
WATCH_PATHS="${CELERY_WATCH_PATHS:-$DEFAULT_WATCH_PATHS}"
RELOAD_INTERVAL="${CELERY_RELOAD_INTERVAL:-2}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
. "$SCRIPT_DIR/dev_reload_fingerprint.sh"

CELERY_CMD="celery -A src.celery_app.app worker --loglevel=${CELERY_LOG_LEVEL:-INFO} --concurrency=${CELERY_WORKER_CONCURRENCY:-4} --max-tasks-per-child=${CELERY_MAX_TASKS:-1000} --queues=${CELERY_WORKER_QUEUES}"
SHUTDOWN_GRACE_SECONDS="${CELERY_RELOAD_SHUTDOWN_GRACE_SECONDS:-20}"

worker_pid=""
worker_stop_target=""

start_worker() {
  if command -v setsid >/dev/null 2>&1; then
    setsid sh -c "exec $CELERY_CMD" &
    worker_pid="$!"
    worker_stop_target="-$worker_pid"
  else
    sh -c "exec $CELERY_CMD" &
    worker_pid="$!"
    worker_stop_target="$worker_pid"
  fi
  echo "[celery-dev-reload] worker started pid=${worker_pid}"
}

stop_worker() {
  if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
    echo "[celery-dev-reload] stopping worker pid=${worker_pid}"
    stop_target="${worker_stop_target:-$worker_pid}"
    # 正常 TERM 只交给 Celery MainProcess，由 leader 协调 prefork children。
    kill -TERM "$worker_pid" 2>/dev/null || true
    (
      sleep "$SHUTDOWN_GRACE_SECONDS"
      if kill -0 "$worker_pid" 2>/dev/null; then
        echo "[celery-dev-reload] worker pid=${worker_pid} did not stop, killing"
        kill -KILL "$stop_target" 2>/dev/null || kill -KILL "$worker_pid" 2>/dev/null || true
      fi
    ) &
    killer_pid="$!"
    wait "$worker_pid" || true
    kill "$killer_pid" 2>/dev/null || true
    wait "$killer_pid" 2>/dev/null || true
  fi
  worker_pid=""
  worker_stop_target=""
}

cleanup() {
  stop_worker
  exit 0
}

trap cleanup INT TERM

fingerprint="$(calculate_python_fingerprint $WATCH_PATHS || true)"
start_worker

while true; do
  sleep "$RELOAD_INTERVAL"

  # Worker crashed unexpectedly: restart it.
  if ! kill -0 "$worker_pid" 2>/dev/null; then
    echo "[celery-dev-reload] worker exited unexpectedly, restarting"
    start_worker
    fingerprint="$(calculate_python_fingerprint $WATCH_PATHS || true)"
    continue
  fi

  new_fingerprint="$(calculate_python_fingerprint $WATCH_PATHS || true)"
  if [ "$new_fingerprint" != "$fingerprint" ]; then
    echo "[celery-dev-reload] code change detected, restarting worker"
    fingerprint="$new_fingerprint"
    stop_worker
    start_worker
  fi
done
