#!/bin/sh
set -eu

# Development-only auto-restart wrapper for Celery worker.
# It watches python source files and restarts the worker process on changes.

WATCH_PATH="${CELERY_WATCH_PATH:-/app/src}"
RELOAD_INTERVAL="${CELERY_RELOAD_INTERVAL:-2}"

CELERY_CMD="celery -A src.celery_app.app worker --loglevel=${CELERY_LOG_LEVEL:-INFO} --concurrency=${CELERY_CONCURRENCY:-4} --max-tasks-per-child=${CELERY_MAX_TASKS:-1000} --queues=default,celery"

worker_pid=""

calculate_fingerprint() {
  find "$WATCH_PATH" -type f -name "*.py" -print0 \
    | xargs -0 stat -c "%n:%Y" 2>/dev/null \
    | sort \
    | sha256sum \
    | awk "{print \$1}"
}

start_worker() {
  sh -c "$CELERY_CMD" &
  worker_pid="$!"
  echo "[celery-dev-reload] worker started pid=${worker_pid}"
}

stop_worker() {
  if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
    echo "[celery-dev-reload] stopping worker pid=${worker_pid}"
    kill -TERM "$worker_pid" || true
    wait "$worker_pid" || true
  fi
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
