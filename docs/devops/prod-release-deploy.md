# 生产环境部署 Runbook

> 状态：`implementation_baseline`
> 系统尚未发布；本文只描述当前前后端固定版本对的直接替换，不提供旧镜像、旧 schema、旧数据或旧 Runtime 的兼容/滚动升级路径。

## 1. 发布边界

- 前后端必须作为同一批准合同发布，禁止只部署其中一端。
- 后端和前端都必须使用 Jenkins 产出的 immutable tag，并在切换前固定为 registry digest。
- fresh DB 使用 `bootstrap_foundation`；已有部署数据库使用权限 `--apply`。两者之后都必须执行独立的 `--check`。
- 维护态就是停止 Nginx 并确认外部端口关闭。任何后续门禁失败都保持 Nginx 关闭并报告失败阶段。
- 不允许生产 seed、旧同步入口、静态权限 SQL、兼容 API、双 schema、双读写或旧镜像回退。

本文命令不会自行部署。执行生产命令必须取得单独部署授权，并先记录发布 SHA、镜像 digest、OpenAPI SHA 和权限契约 SHA。

## 2. 前置条件

部署机必须具备 Docker、Docker Compose 2.24.4 或更高版本，以及与目标后端提交一致的 `docker-compose.yml`、`docker-compose.deploy.yml` 和 `.env.prod`。还必须确认：

- `USE_SNOWFLAKE_ID=true` 且 datacenter/worker ID 唯一；
- `WMS_PROVIDER_PROFILE_HOST_FILE` 指向当前工厂唯一、只读且可读的 Provider profile；
- API、Celery 和 CLI 数据库连接预算已批准；
- 生产 Compose 不挂载宿主机 `/app/src`；
- `BOOTSTRAP_ADMIN_*` 通过受控部署环境注入，不写入仓库或现场记录；
- 已明确目标是 fresh DB 还是已有部署数据库，不在运行中猜测。

以下示例统一使用：

```bash
set -euo pipefail
cd /srv/wes/app

export BACKEND_IMAGE=192.168.0.220:5050/wes/wes_backend:<approved-tag>
export FRONTEND_IMAGE=192.168.0.220:5050/wes/wes_frontend:<approved-tag>
export EXPECTED_BACKEND_COMMIT_SHA=<approved-backend-commit>
export DEPLOY_SOURCE_COMMIT_SHA=<approved-backend-commit>
export EXPECTED_FRONTEND_COMMIT_SHA=<approved-frontend-commit>
export EXPECTED_OPENAPI_SHA256=<approved-openapi-sha256>
export EXPECTED_PERMISSIONS_SHA256=<approved-permissions-sha256>
[ "$DEPLOY_SOURCE_COMMIT_SHA" = "$EXPECTED_BACKEND_COMMIT_SHA" ] || exit 1
DEPLOY_ACTUAL_COMMIT=$(git rev-parse HEAD)
[ "$DEPLOY_ACTUAL_COMMIT" = "$DEPLOY_SOURCE_COMMIT_SHA" ] || exit 1

compose() {
  docker compose --env-file .env.prod \
    -f docker-compose.yml \
    -f docker-compose.deploy.yml "$@"
}

MAINTENANCE_MODE=false
CUTOVER_STAGE=pre-maintenance
keep_external_entrypoint_closed() {
  if [ "$MAINTENANCE_MODE" = true ]; then
    echo "发布在阶段 ${CUTOVER_STAGE} 失败；外部入口保持关闭" >&2
    compose stop nginx >/dev/null 2>&1 || true
  fi
}
fail_cutover() {
  CUTOVER_STAGE="$1"
  keep_external_entrypoint_closed
  exit 1
}
trap keep_external_entrypoint_closed EXIT
```

## 3. 拉取并固定前后端镜像

这一步在进入维护态前完成：

```bash
docker login 192.168.0.220:5050
compose pull api celery celery-wms-fulfillment celery_beat flower frontend nginx

BACKEND_IMAGE=$(docker image inspect --format '{{ index .RepoDigests 0 }}' "$BACKEND_IMAGE")
FRONTEND_IMAGE=$(docker image inspect --format '{{ index .RepoDigests 0 }}' "$FRONTEND_IMAGE")
case "$BACKEND_IMAGE" in *@sha256:*) ;; *) echo '后端镜像没有固定 digest' >&2; exit 1 ;; esac
case "$FRONTEND_IMAGE" in *@sha256:*) ;; *) echo '前端镜像没有固定 digest' >&2; exit 1 ;; esac
export BACKEND_IMAGE FRONTEND_IMAGE

BACKEND_REVISION=$(docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$BACKEND_IMAGE")
FRONTEND_REVISION=$(docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$FRONTEND_IMAGE")
FRONTEND_BACKEND_REVISION=$(docker image inspect --format \
  '{{ index .Config.Labels "com.zontec.wes.backend-contract-revision" }}' "$FRONTEND_IMAGE")
FRONTEND_OPENAPI_SHA256=$(docker image inspect --format \
  '{{ index .Config.Labels "com.zontec.wes.openapi-sha256" }}' "$FRONTEND_IMAGE")
FRONTEND_PERMISSIONS_SHA256=$(docker image inspect --format \
  '{{ index .Config.Labels "com.zontec.wes.permissions-sha256" }}' "$FRONTEND_IMAGE")
[ "$BACKEND_REVISION" = "$EXPECTED_BACKEND_COMMIT_SHA" ] || exit 1
[ "$FRONTEND_REVISION" = "$EXPECTED_FRONTEND_COMMIT_SHA" ] || exit 1
[ "$FRONTEND_BACKEND_REVISION" = "$EXPECTED_BACKEND_COMMIT_SHA" ] || exit 1
[ "$FRONTEND_OPENAPI_SHA256" = "$EXPECTED_OPENAPI_SHA256" ] || exit 1
[ "$FRONTEND_PERMISSIONS_SHA256" = "$EXPECTED_PERMISSIONS_SHA256" ] || exit 1

compose config >/dev/null
```

固定后不得重新解析 channel tag。部署仓库、后端镜像、前端绑定的后端契约必须是同一个批准 revision；任一 digest、revision 或 SHA 与批准发布记录不一致，在进入维护态前停止发布。

## 4. 进入维护态并清空旧应用进程

先停止 Nginx，再确认外部端口已经关闭：

```bash
NGINX_HTTP_PORT=$(sed -n 's/^NGINX_HTTP_PORT=//p' .env.prod | tail -n 1)
NGINX_HTTP_PORT=${NGINX_HTTP_PORT:-80}
CUTOVER_STAGE=maintenance-stop
MAINTENANCE_MODE=true
compose stop nginx || fail_cutover maintenance-stop
CLOSE_RETRY=0
while curl -sS --connect-timeout 1 --max-time 2 \
  "http://127.0.0.1:${NGINX_HTTP_PORT}/" >/dev/null 2>&1; do
  CLOSE_RETRY=$((CLOSE_RETRY + 1))
  [ "$CLOSE_RETRY" -lt 10 ] || fail_cutover listener-closure
  sleep 1
done
```

只按 Compose project/service 标签识别容器，禁止依赖 `container_name`：

```bash
compose_project_name=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env.prod | tail -n 1)
compose_project_name=${compose_project_name:-$(basename "$PWD")}

PROJECT_CONTAINER_IDS=$(docker ps -q \
  --filter "label=com.docker.compose.project=${compose_project_name}") || fail_cutover service-discovery
for container_id in $PROJECT_CONTAINER_IDS; do
  compose_service=$(docker inspect --format \
    '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id") || fail_cutover service-discovery
  case "$compose_service" in
    db|redis) ;;
    api|celery|celery-wms-fulfillment|celery_beat|flower|frontend|nginx)
      docker stop "$container_id" >/dev/null || fail_cutover application-stop
      ;;
    *)
      echo "未知 Compose service，保持维护态: ${compose_service:-<empty>}" >&2
      fail_cutover unknown-service
      ;;
  esac
done

compose up -d --wait db redis || fail_cutover infrastructure-readiness

REMAINING_CONTAINER_IDS=$(docker ps -q \
  --filter "label=com.docker.compose.project=${compose_project_name}") \
  || fail_cutover remaining-service-discovery
for container_id in $REMAINING_CONTAINER_IDS; do
  compose_service=$(docker inspect --format \
    '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id") \
    || fail_cutover remaining-service-discovery
  case "$compose_service" in
    db|redis) ;;
    *) echo "迁移前仍有应用运行: ${compose_service:-<empty>}" >&2; fail_cutover remaining-application-service ;;
  esac
done
```

进入本节后所有错误都必须保留维护态。不得在授权零漂移前重建任何对外应用服务。

## 5. 使用新后端镜像执行迁移

```bash
compose run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  --entrypoint /opt/venv/bin/alembic \
  api upgrade head
```

这是一条新后端镜像的一次性命令。失败时保持 Nginx 与全部应用停止；不得执行 downgrade、启动旧镜像或引入兼容 revision。

## 6. 在维护态收敛基础授权

二选一，不能同时执行。

### 6.1 Fresh DB

```bash
if BOOTSTRAP_OUTPUT=$(compose run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  --entrypoint /bin/bash \
  api scripts/data/bootstrap_foundation.sh 2>&1); then
  printf '%s\n' "$BOOTSTRAP_OUTPUT"
else
  BOOTSTRAP_STATUS=$?
  printf '%s\n' "$BOOTSTRAP_OUTPUT"
  echo "bootstrap exit status: ${BOOTSTRAP_STATUS}" >&2
  printf '%s\n' "$BOOTSTRAP_OUTPUT" \
    | grep -Fxq 'DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED' \
    || fail_cutover authorization-bootstrap
  compose run --rm --no-deps \
    --entrypoint /opt/venv/bin/python \
    api scripts/data/sync_permissions.py --repair-cache \
    || fail_cutover authorization-cache-repair
fi
```

### 6.2 已有部署数据库

```bash
compose run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  --entrypoint /opt/venv/bin/python \
  api scripts/data/sync_permissions.py --apply
```

无论选择哪条路径，随后都必须运行新的只读检查：

```bash
compose run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  --entrypoint /opt/venv/bin/python \
  api scripts/data/sync_permissions.py --check
```

6.1 的失败分支只接受独占整行的 `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED`，并已在同一分支执行一次 `--repair-cache`；不得重跑 bootstrap 或再次 repair。`--apply` 的任何非零退出都 fail closed。`--repair-cache` 只清理两个权限缓存命名空间；repair 或随后独立的 `--check` 失败时，EXIT trap 会再次停止 Nginx。

## 7. 启动固定版本并执行内部门禁

授权零漂移后才允许启动成对版本：

```bash
compose up -d --force-recreate --wait \
  api celery celery-wms-fulfillment celery_beat flower frontend \
  || fail_cutover application-start

compose exec -T api curl -fsS http://127.0.0.1:8001/ready >/dev/null \
  || fail_cutover backend-readiness
compose exec -T frontend wget --no-verbose --tries=1 --spider \
  http://127.0.0.1:5173/ >/dev/null || fail_cutover frontend-asset

BACKEND_REVISION=$(docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$BACKEND_IMAGE") \
  || fail_cutover backend-image-revision
FRONTEND_REVISION=$(docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$FRONTEND_IMAGE") \
  || fail_cutover frontend-image-revision
FRONTEND_BACKEND_REVISION=$(docker image inspect --format \
  '{{ index .Config.Labels "com.zontec.wes.backend-contract-revision" }}' "$FRONTEND_IMAGE") \
  || fail_cutover frontend-backend-provenance
FRONTEND_OPENAPI_SHA256=$(docker image inspect --format \
  '{{ index .Config.Labels "com.zontec.wes.openapi-sha256" }}' "$FRONTEND_IMAGE") \
  || fail_cutover openapi-provenance
FRONTEND_PERMISSIONS_SHA256=$(docker image inspect --format \
  '{{ index .Config.Labels "com.zontec.wes.permissions-sha256" }}' "$FRONTEND_IMAGE") \
  || fail_cutover permission-provenance
[ "$BACKEND_REVISION" = "$EXPECTED_BACKEND_COMMIT_SHA" ] || fail_cutover backend-image-revision
[ "$FRONTEND_REVISION" = "$EXPECTED_FRONTEND_COMMIT_SHA" ] || fail_cutover frontend-image-revision
[ "$FRONTEND_BACKEND_REVISION" = "$EXPECTED_BACKEND_COMMIT_SHA" ] \
  || fail_cutover frontend-backend-provenance
[ "$FRONTEND_OPENAPI_SHA256" = "$EXPECTED_OPENAPI_SHA256" ] || fail_cutover openapi-provenance
[ "$FRONTEND_PERMISSIONS_SHA256" = "$EXPECTED_PERMISSIONS_SHA256" ] \
  || fail_cutover permission-provenance
```

上述可执行来源门禁通过后，在恢复 Nginx 前还必须完成并记录：

- 后端和前端实际容器镜像 digest 与第 3 节固定值一致；
- 镜像 OCI revision 与批准的前后端提交一致；
- 发布记录中的 frozen OpenAPI SHA、权限契约 SHA 与该镜像对的评审产物一致；
- 当前保留的菜单同步按 `docs/auth/menu-sync-guide.md` 完成；
- `api`、`celery`、`celery-wms-fulfillment`、`celery_beat`、`flower`、`frontend` 状态符合当前拓扑。

任一来源、readiness、前端资源或菜单门禁失败，都停止在本节并保持 Nginx 关闭。

## 8. 最后恢复外部入口

只有前述全部门禁通过后执行：

```bash
compose up -d --no-deps nginx || fail_cutover external-entrypoint
curl --fail --show-error --connect-timeout 1 --max-time 10 \
  "http://127.0.0.1:${NGINX_HTTP_PORT}/health" || fail_cutover external-health
curl --fail --show-error --connect-timeout 1 --max-time 10 --output /dev/null \
  "http://127.0.0.1:${NGINX_HTTP_PORT}/" || fail_cutover external-frontend
compose ps || fail_cutover final-compose-status
MAINTENANCE_MODE=false
trap - EXIT
```

外部检查失败时立即再次执行 `compose stop nginx`，记录失败阶段，不把内部健康或首页单项成功提升为部署完成。

## 9. 失败处理与证据边界

失败处理只有一条路径：保持/恢复维护态，保留 PostgreSQL 与 Redis 供取证，修复当前版本并重新走批准流程。禁止临时启用旧应用、兼容端点、双 schema 或不成对前后端。

本 Runbook 的命令结果最多证明指定镜像对、schema、授权目录和服务入口的部署技术门禁。它不证明 WMS/ECS 供应商一致性、现场联调或业务验收；这些证据必须单独取得。

## 10. 检查清单

- 已记录批准的前后端提交、digest、OpenAPI SHA 和权限 SHA；
- Nginx 在迁移/授权阶段关闭，外部端口实测不可达；
- Compose 标签发现无未知 service，迁移前只有 PostgreSQL/Redis 运行；
- 新后端镜像一次性 Alembic 成功；
- fresh DB bootstrap 或已有 DB `--apply` 成功，随后 `--check` 零漂移；
- post-commit cache failure 如发生，只执行 repair 后新的 check；
- 六个固定版本应用服务完成内部门禁；
- Nginx 最后恢复，外部健康与首页通过；
- 状态仍明确区分“已部署”与供应商/现场/业务验收。
