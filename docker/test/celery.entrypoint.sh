#!/bin/sh

set -eu

: "${CELERY_WORKER_QUEUES:?CELERY_WORKER_QUEUES is required}"
: "${CELERY_WORKER_CONCURRENCY:?CELERY_WORKER_CONCURRENCY is required}"

exec celery -A src.celery_app.app worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY}" \
  --max-tasks-per-child="${CELERY_MAX_TASKS:-1000}" \
  --queues="${CELERY_WORKER_QUEUES}"
