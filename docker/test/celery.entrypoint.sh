#!/bin/sh

set -eu

exec celery -A src.celery_app.app worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency="${CELERY_CONCURRENCY:-4}" \
  --max-tasks-per-child="${CELERY_MAX_TASKS:-1000}" \
  --queues=default,celery,device
