#!/bin/sh

set -eu

: "${CELERY_WORKER_QUEUES:?CELERY_WORKER_QUEUES is required}"
: "${WMS_PROVIDER_PROCESS_ROLE:?WMS_PROVIDER_PROCESS_ROLE is required}"

exec celery -A src.celery_app.app worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency="${CELERY_CONCURRENCY:-4}" \
  --max-tasks-per-child="${CELERY_MAX_TASKS:-1000}" \
  --queues="${CELERY_WORKER_QUEUES}"
