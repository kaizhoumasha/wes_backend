#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_CONF="$ROOT_DIR/postgresql/base.conf"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

read_setting() {
  local key="$1"
  awk -v target="$key" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/#.*/, "", line)
      if (index(line, "=") == 0) {
        next
      }
      name = substr(line, 1, index(line, "=") - 1)
      value = substr(line, index(line, "=") + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (name == target) {
        print value
        exit
      }
    }
  ' "$BASE_CONF"
}

[[ -f "$BASE_CONF" ]] || fail "missing $BASE_CONF"

if ! grep -Eq "^[[:space:]]*shared_preload_libraries[[:space:]]*=.*timescaledb" "$BASE_CONF"; then
  fail "shared_preload_libraries must preload timescaledb"
fi

max_worker_processes="$(read_setting "max_worker_processes")"
timescale_workers="$(read_setting "timescaledb.max_background_workers")"

[[ "$max_worker_processes" =~ ^[0-9]+$ ]] || fail "max_worker_processes must be set to an integer"
[[ "$timescale_workers" =~ ^[0-9]+$ ]] || fail "timescaledb.max_background_workers must be set to an integer"

reserved_core_workers=8
minimum_required=$((timescale_workers + reserved_core_workers))

if (( max_worker_processes < minimum_required )); then
  fail "max_worker_processes=$max_worker_processes is too low; need >= $minimum_required for timescaledb.max_background_workers=$timescale_workers plus core worker reserve=$reserved_core_workers"
fi

echo "OK: max_worker_processes=$max_worker_processes, timescaledb.max_background_workers=$timescale_workers, reserve=$reserved_core_workers"
