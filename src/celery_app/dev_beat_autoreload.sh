#!/bin/sh
set -eu

# Development-only auto-restart wrapper for Celery beat.
# It watches python source files and restarts the beat process on changes.

WATCH_PATH="${CELERY_WATCH_PATH:-/app/src}"
RELOAD_INTERVAL="${CELERY_RELOAD_INTERVAL:-2}"

CELERY_CMD="celery -A src.celery_app.app beat --loglevel=${CELERY_LOG_LEVEL:-INFO}"

beat_pid=""

calculate_fingerprint() {
  find "$WATCH_PATH" -type f -name "*.py" -print0 \
    | xargs -0 stat -c "%n:%Y" 2>/dev/null \
    | sort \
    | sha256sum \
    | awk "{print \$1}"
}

start_beat() {
  sh -c "$CELERY_CMD" &
  beat_pid="$!"
  echo "[celery-beat-dev-reload] beat started pid=${beat_pid}"
}

stop_beat() {
  if [ -n "$beat_pid" ] && kill -0 "$beat_pid" 2>/dev/null; then
    echo "[celery-beat-dev-reload] stopping beat pid=${beat_pid}"
    kill -TERM "$beat_pid" || true
    wait "$beat_pid" || true
  fi
}

cleanup() {
  stop_beat
  exit 0
}

trap cleanup INT TERM

fingerprint="$(calculate_fingerprint || true)"
start_beat

while true; do
  sleep "$RELOAD_INTERVAL"

  if ! kill -0 "$beat_pid" 2>/dev/null; then
    echo "[celery-beat-dev-reload] beat exited unexpectedly, restarting"
    start_beat
    fingerprint="$(calculate_fingerprint || true)"
    continue
  fi

  new_fingerprint="$(calculate_fingerprint || true)"
  if [ "$new_fingerprint" != "$fingerprint" ]; then
    echo "[celery-beat-dev-reload] code change detected, restarting beat"
    fingerprint="$new_fingerprint"
    stop_beat
    start_beat
  fi
done
