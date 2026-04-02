#!/bin/sh

set -eu

: "${REGISTRY_URL:?REGISTRY_URL is required}"
: "${GITLAB_USER:?GITLAB_USER is required}"
: "${GITLAB_TOKEN:?GITLAB_TOKEN is required}"

printf '%s' "${GITLAB_TOKEN}" | docker login "${REGISTRY_URL}" -u "${GITLAB_USER}" --password-stdin
