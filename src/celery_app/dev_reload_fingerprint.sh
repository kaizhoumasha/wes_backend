#!/bin/sh
set -eu

calculate_python_fingerprint() {
  watch_paths="$*"
  for watch_path in $watch_paths; do
    if [ -d "$watch_path" ]; then
      find "$watch_path" -type f -name "*.py" -exec sha256sum {} + 2>/dev/null
    fi
  done | sort | sha256sum | awk '{print $1}'
}

if [ "${0##*/}" = "dev_reload_fingerprint.sh" ]; then
  calculate_python_fingerprint "$@"
fi
