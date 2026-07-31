#!/bin/bash
# 从四个实际 Compose 一次性容器生成并验证 WMS deployment attestation。

set -euo pipefail

compose_args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --compose-file)
            compose_args+=(-f "$2")
            shift 2
            ;;
        --env-file)
            compose_args+=(--env-file "$2")
            shift 2
            ;;
        --profile)
            compose_args+=(--profile "$2")
            shift 2
            ;;
        *)
            echo "unknown attestation runner argument: $1" >&2
            exit 2
            ;;
    esac
done

compose=(docker compose "${compose_args[@]}")
services=(api celery celery-wms-fulfillment celery_beat)
roles=(api wes-worker fulfillment-worker beat)
container_ids=()
artifacts=()

cleanup() {
    exit_code=$?
    for container_id in "${container_ids[@]}"; do
        docker rm -f "$container_id" >/dev/null 2>&1 || true
    done
    return "$exit_code"
}
trap cleanup EXIT

"${compose[@]}" pull "${services[@]}"

for index in "${!services[@]}"; do
    service="${services[$index]}"
    expected_role="${roles[$index]}"
    container_id=$("${compose[@]}" run -d --no-deps --entrypoint sleep "$service" 600)
    if [[ ! "$container_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
        echo "invalid temporary container id for $service" >&2
        exit 1
    fi
    container_ids+=("$container_id")

    image_identity=$(docker inspect --format '{{.Image}}' "$container_id")
    if [[ ! "$image_identity" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        echo "invalid actual image identity for $service" >&2
        exit 1
    fi
    actual_role=$(
        docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" |
            sed -n 's/^WMS_DEPLOYMENT_ROLE=//p'
    )
    if [ "$actual_role" != "$expected_role" ]; then
        echo "WMS deployment role mismatch for $service: expected $expected_role" >&2
        exit 1
    fi

    artifact=$(
        docker exec \
            -e "WMS_DEPLOYMENT_IMAGE_ID=$image_identity" \
            "$container_id" \
            python scripts/check_wms_deployment_attestation.py emit
    )
    if [[ -z "$artifact" || "$artifact" == *$'\n'* ]]; then
        echo "WMS deployment emit must return one compact JSON line for $service" >&2
        exit 1
    fi
    artifacts+=("$artifact")
done

api_image_identity=$(docker inspect --format '{{.Image}}' "${container_ids[0]}")
printf '%s\n' "${artifacts[@]}" |
    docker exec -i \
        -e "WMS_DEPLOYMENT_IMAGE_ID=$api_image_identity" \
        "${container_ids[0]}" \
        python scripts/check_wms_deployment_attestation.py verify-stdin
