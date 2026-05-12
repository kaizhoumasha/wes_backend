#!/bin/sh
set -eu

# Development-only auto-restart wrapper for Celery worker.
# It watches python source files and restarts the worker process on changes.

WATCH_PATH="${CELERY_WATCH_PATH:-/app/src}"
RELOAD_INTERVAL="${CELERY_RELOAD_INTERVAL:-2}"

CELERY_CMD="celery -A src.celery_app.app worker --loglevel=${CELERY_LOG_LEVEL:-INFO} --concurrency=${CELERY_CONCURRENCY:-4} --max-tasks-per-child=${CELERY_MAX_TASKS:-1000} --queues=default,celery"
SHUTDOWN_GRACE_SECONDS="${CELERY_RELOAD_SHUTDOWN_GRACE_SECONDS:-20}"

worker_pid=""
worker_stop_target=""

calculate_fingerprint() {
  find "$WATCH_PATH" -type f -name "*.py" -print0 \
    | xargs -0 stat -c "%n:%Y" 2>/dev/null \
    | sort \
    | sha256sum \
    | awk "{print \$1}"
}

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
    kill -TERM "$stop_target" 2>/dev/null || kill -TERM "$worker_pid" 2>/dev/null || true
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

fingerprint="$(calculate_fingerprint || true)"
start_worker

while true; do
  sleep "$RELOAD_INTERVAL"

  # Worker crashed unexpectedly: restart it.
  if ! kill -0 "$worker_pid" 2>/dev/null; then
    echo "[celery-dev-reload] worker exited unexpectedly, restarting"
    start_worker
    fingerprint="$(calculate_fingerprint || true)"
    continue
  fi

  new_fingerprint="$(calculate_fingerprint || true)"
  if [ "$new_fingerprint" != "$fingerprint" ]; then
    echo "[celery-dev-reload] code change detected, restarting worker"
    fingerprint="$new_fingerprint"
    stop_worker
    start_worker
  fi
done
