#!/bin/sh

set -eu

APP_DIR="${FRONTEND_APP_DIR:-/app}"
PORT="${FRONTEND_PORT:-5173}"
STORE_DIR="${PNPM_STORE_DIR:-/pnpm/store}"
LOCKFILE="$APP_DIR/pnpm-lock.yaml"
PACKAGE_JSON="$APP_DIR/package.json"
STAMP_FILE="$APP_DIR/node_modules/.pnpm-lock.sha256"

if [ ! -f "$PACKAGE_JSON" ] || [ ! -f "$LOCKFILE" ]; then
  echo "frontend source is not mounted correctly: expected $PACKAGE_JSON and $LOCKFILE" >&2
  exit 1
fi

cd "$APP_DIR"

export PNPM_HOME="${PNPM_HOME:-/pnpm}"
export PATH="$PNPM_HOME:$PATH"

corepack enable >/dev/null 2>&1 || true

mkdir -p "$STORE_DIR" "$APP_DIR/node_modules"
pnpm config set store-dir "$STORE_DIR" >/dev/null

LOCK_HASH="$(sha256sum "$LOCKFILE" | awk '{print $1}')"
NEEDS_INSTALL=0

if [ ! -f "$STAMP_FILE" ]; then
  NEEDS_INSTALL=1
elif [ "$(cat "$STAMP_FILE")" != "$LOCK_HASH" ]; then
  NEEDS_INSTALL=1
elif [ ! -f "$APP_DIR/node_modules/.modules.yaml" ]; then
  NEEDS_INSTALL=1
fi

if [ "$NEEDS_INSTALL" -eq 1 ]; then
  echo "Installing frontend dependencies with pnpm..."
  if pnpm install --frozen-lockfile; then
    printf '%s' "$LOCK_HASH" >"$STAMP_FILE"
    echo "Frontend dependencies installed successfully."
  else
    echo "WARNING: Frozen lockfile failed, trying without --frozen-lockfile..." >&2
    if pnpm install; then
      printf '%s' "$LOCK_HASH" >"$STAMP_FILE"
      echo "Frontend dependencies installed (lockfile was out of sync)."
    else
      echo "ERROR: Failed to install frontend dependencies" >&2
      exit 1
    fi
  fi
else
  echo "Frontend dependencies are up to date."
fi

exec pnpm dev --host 0.0.0.0 --port "$PORT"
