#!/bin/sh
set -eu

# Development-only auto-restart wrapper for Celery beat.
# It watches python source files and restarts the beat process on changes.

DEFAULT_WATCH_PATHS="/app/src /app/deployment /app/packages/wes_plugin_sdk/src /app/workline_plugins/rough_sorter/src"
WATCH_PATHS="${CELERY_WATCH_PATHS:-$DEFAULT_WATCH_PATHS}"
RELOAD_INTERVAL="${CELERY_RELOAD_INTERVAL:-2}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
. "$SCRIPT_DIR/dev_reload_fingerprint.sh"

CELERY_CMD="celery -A src.celery_app.app beat --loglevel=${CELERY_LOG_LEVEL:-INFO}"
SHUTDOWN_GRACE_SECONDS="${CELERY_RELOAD_SHUTDOWN_GRACE_SECONDS:-20}"

beat_pid=""
beat_stop_target=""

start_beat() {
  if command -v setsid >/dev/null 2>&1; then
    setsid sh -c "exec $CELERY_CMD" &
    beat_pid="$!"
    beat_stop_target="-$beat_pid"
  else
    sh -c "exec $CELERY_CMD" &
    beat_pid="$!"
    beat_stop_target="$beat_pid"
  fi
  echo "[celery-beat-dev-reload] beat started pid=${beat_pid}"
}

stop_beat() {
  if [ -n "$beat_pid" ] && kill -0 "$beat_pid" 2>/dev/null; then
    echo "[celery-beat-dev-reload] stopping beat pid=${beat_pid}"
    stop_target="${beat_stop_target:-$beat_pid}"
    kill -TERM "$stop_target" 2>/dev/null || kill -TERM "$beat_pid" 2>/dev/null || true
    (
      sleep "$SHUTDOWN_GRACE_SECONDS"
      if kill -0 "$beat_pid" 2>/dev/null; then
        echo "[celery-beat-dev-reload] beat pid=${beat_pid} did not stop, killing"
        kill -KILL "$stop_target" 2>/dev/null || kill -KILL "$beat_pid" 2>/dev/null || true
      fi
    ) &
    killer_pid="$!"
    wait "$beat_pid" || true
    kill "$killer_pid" 2>/dev/null || true
    wait "$killer_pid" 2>/dev/null || true
  fi
  beat_pid=""
  beat_stop_target=""
}

cleanup() {
  stop_beat
  exit 0
}

trap cleanup INT TERM

fingerprint="$(calculate_python_fingerprint $WATCH_PATHS || true)"
start_beat

while true; do
  sleep "$RELOAD_INTERVAL"

  if ! kill -0 "$beat_pid" 2>/dev/null; then
    echo "[celery-beat-dev-reload] beat exited unexpectedly, restarting"
    start_beat
    fingerprint="$(calculate_python_fingerprint $WATCH_PATHS || true)"
    continue
  fi

  new_fingerprint="$(calculate_python_fingerprint $WATCH_PATHS || true)"
  if [ "$new_fingerprint" != "$fingerprint" ]; then
    echo "[celery-beat-dev-reload] code change detected, restarting beat"
    fingerprint="$new_fingerprint"
    stop_beat
    start_beat
  fi
done
