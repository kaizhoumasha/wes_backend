# 生产环境部署 Runbook

> 状态：`implementation_baseline`
> 系统尚未发布；本文只描述当前前后端固定版本对的直接替换，不提供旧镜像、旧 schema、旧数据或旧 Runtime 的兼容/滚动升级路径。

## 1. 发布边界

- 前后端必须作为同一批准合同发布，禁止只部署其中一端。
- 后端和前端都必须使用 Jenkins 产出的 immutable tag，并在切换前固定为 registry digest。
- fresh DB 使用 `bootstrap_foundation`；已有部署数据库使用受控的权限 `--apply`。任一 mutation 如精确报告 post-commit cache marker，只 repair 一次且不重跑 mutation；两者之后都必须执行独立的 `--check`。
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
  docker compose --profile prod --env-file .env.prod \
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
# EXISTING_DATABASE_AUTHORIZATION_BEGIN
if AUTHORIZATION_APPLY_OUTPUT=$(compose run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  --entrypoint /opt/venv/bin/python \
  api scripts/data/sync_permissions.py --apply 2>&1); then
  printf '%s\n' "$AUTHORIZATION_APPLY_OUTPUT"
else
  AUTHORIZATION_APPLY_STATUS=$?
  printf '%s\n' "$AUTHORIZATION_APPLY_OUTPUT"
  echo "authorization apply exit status: ${AUTHORIZATION_APPLY_STATUS}" >&2
  printf '%s\n' "$AUTHORIZATION_APPLY_OUTPUT" \
    | grep -Fxq 'DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED' \
    || fail_cutover authorization-apply
  compose run --rm --no-deps \
    --entrypoint /opt/venv/bin/python \
    api scripts/data/sync_permissions.py --repair-cache \
    || fail_cutover authorization-cache-repair
fi
# EXISTING_DATABASE_AUTHORIZATION_END
```

无论选择哪条路径，随后都必须运行新的只读检查：

```bash
# AUTHORIZATION_FRESH_CHECK_BEGIN
compose run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  --entrypoint /opt/venv/bin/python \
  api scripts/data/sync_permissions.py --check \
  || fail_cutover authorization-check
# AUTHORIZATION_FRESH_CHECK_END
```

两个 mutation 的失败分支都只接受独占整行、且与详情行分离的 `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED`，并在同一分支执行一次 `--repair-cache`；不得重跑 bootstrap、`--apply` 或再次 repair。没有该精确整行的普通失败立即 fail closed。`--repair-cache` 只清理当前数据库前缀下的两个权限缓存命名空间；repair 或随后独立的 fresh `--check` 失败时，EXIT trap 会再次停止 Nginx。

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

echo "验证超级管理员真实登录"
CUTOVER_STAGE=admin-login-gate
if ! compose exec -T \
  -e BOOTSTRAP_ADMIN_USERNAME \
  -e BOOTSTRAP_ADMIN_PASSWORD \
  api /opt/venv/bin/python scripts/check_bootstrap_admin_login.py \
  --base-url http://127.0.0.1:8001; then
  fail_cutover admin-login-gate
fi

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
echo "校验入口恢复前服务拓扑"
CUTOVER_STAGE=pre-entrypoint-topology
EXPECTED_PRE_ENTRY_SERVICES="$(compose config --services | grep -v '^nginx$' | LC_ALL=C sort)"
RUNNING_PRE_ENTRY_SERVICES="$(compose ps --status running --services | LC_ALL=C sort)"
if [ "$RUNNING_PRE_ENTRY_SERVICES" != "$EXPECTED_PRE_ENTRY_SERVICES" ]; then
  printf '期望服务:\n%s\n' "$EXPECTED_PRE_ENTRY_SERVICES"
  printf '运行服务:\n%s\n' "$RUNNING_PRE_ENTRY_SERVICES"
  fail_cutover pre-entrypoint-topology
fi

CUTOVER_STAGE=external-entrypoint
compose up -d --no-deps --wait --wait-timeout 60 nginx || fail_cutover external-entrypoint
CUTOVER_STAGE=external-health
python3 scripts/wait_for_http.py \
  --url "http://127.0.0.1:${NGINX_HTTP_PORT}/health" \
  --attempts 10 --timeout-seconds 2 --interval-seconds 1 \
  || fail_cutover external-health
CUTOVER_STAGE=external-frontend
python3 scripts/wait_for_http.py \
  --url "http://127.0.0.1:${NGINX_HTTP_PORT}/" \
  --attempts 10 --timeout-seconds 2 --interval-seconds 1 \
  || fail_cutover external-frontend

echo "校验最终服务拓扑"
CUTOVER_STAGE=final-topology
EXPECTED_FINAL_SERVICES="$(compose config --services | LC_ALL=C sort)"
RUNNING_FINAL_SERVICES="$(compose ps --status running --services | LC_ALL=C sort)"
if [ "$RUNNING_FINAL_SERVICES" != "$EXPECTED_FINAL_SERVICES" ]; then
  printf '期望服务:\n%s\n' "$EXPECTED_FINAL_SERVICES"
  printf '运行服务:\n%s\n' "$RUNNING_FINAL_SERVICES"
  fail_cutover final-topology
fi
MAINTENANCE_MODE=false
trap - EXIT
```

任何登录、拓扑或外部检查失败都由现有 EXIT trap 再次停止 Nginx；记录失败阶段，不把内部健康或首页单项成功提升为部署完成。

## 9. 失败处理与证据边界

失败处理只有一条路径：保持/恢复维护态，保留 PostgreSQL 与 Redis 供取证，修复当前版本并重新走批准流程。禁止临时启用旧应用、兼容端点、双 schema 或不成对前后端。

本 Runbook 的命令结果最多证明指定镜像对、schema、授权目录和服务入口的部署技术门禁。它不证明 WMS/ECS 供应商一致性、现场联调或业务验收；这些证据必须单独取得。

## 10. 非秘密发布记录合同

每次发布只生成一份非秘密 release record；它是本 Runbook 的记录合同，不是第二份 Compose manifest，也不替代镜像 OCI labels、Compose 输出、Alembic head 或备份产物。顶层字段固定为以下类别，不增加凭据或业务数据字段：

```yaml
release_id: <release-id>
backend_revision: <backend-commit>
backend_digest: <backend-image-digest>
backend_source_tree: <backend-git-tree-oid>
frontend_revision: <frontend-commit>
frontend_digest: <frontend-image-digest>
frontend_source_tree: <frontend-git-tree-oid>
frontend_backend_contract_revision: <backend-contract-revision>
openapi_sha256: <openapi-sha256>
permissions_sha256: <permissions-sha256>
alembic_head: <schema-version>
compose_project: <compose-project>
rendered_services: [<service>, ...]
running_services: [<service>, ...]
backup_artifact_path: <external-backup-path>
backup_sha256: <backup-sha256>
restore_evidence_id: <restore-evidence-id>
authorization_check_result: <result-and-evidence-id>
admin_login_gate_result: <result-and-evidence-id>
health_result: <result-and-evidence-id>
ready_result: <result-and-evidence-id>
frontend_result: <result-and-evidence-id>
verified_boundaries:
  engineering_gates: <result-and-evidence-id>
  deployment_technical_gates: <result-and-evidence-id>
unverified_boundaries:
  supplier_conformance: NOT VERIFIED
  real_ecs_wms_callback_loop: NOT VERIFIED
  physical_completion: NOT VERIFIED
  business_acceptance: NOT VERIFIED
started_at: <utc-timestamp>
completed_at: <utc-timestamp>
operator: <operator-id>
```

`backend_source_tree` 与 `frontend_source_tree` 记录 Git tree OID，不另造 source-tree SHA-256。批准提交使用
`git rev-parse '<approved-commit>^{tree}'` 读取；对应不可变镜像使用
`docker image inspect --format '{{ index .Config.Labels "com.zontec.wes.source-manifest" }}' <image-digest>` 读取，二者必须精确一致。OID 长度和散列算法由仓库对象格式决定，不能把当前 40 位 OID 标记为 SHA-256。

`verified_boundaries` 和 `unverified_boundaries` 合计且逐项覆盖六个 acceptance layer：`engineering gates`、`deployment technical gates`、`supplier conformance`、`real ECS/WMS callback loop`、`physical completion`、`business acceptance`。本部署计划只允许给前两层写入工程/部署门禁证据；任一外部层没有独立证据时必须明确写 `NOT VERIFIED`，不得留空、默认为 PASS，或由工程/部署门禁提升。

记录禁止包含 password、token、Cookie、`.env.prod` 内容或业务 payload；只记录结果、摘要、路径和 evidence ID。尤其不要把登录输入或请求/回调正文复制进记录。

### 10.1 位置、权限与归档

记录保存在项目外的受保护主机目录 `/srv/wes/releases/${RELEASE_ID}/`。目录权限为 `0700`，记录文件权限为 `0600`；目录中只放本次 release record 及其必要的非秘密摘要，不提交 Git：

```bash
RELEASE_ID=<approved-release-id>
RECORD_DIR="/srv/wes/releases/${RELEASE_ID}"
install -d -m 0700 "$RECORD_DIR"
umask 077
# 将上面的非秘密字段写入唯一记录文件后：
chmod 0600 "$RECORD_DIR/release-record.yaml"
```

完成发布后，将最终非秘密 record 复制到项目外的运维归档，并保留归档文件的校验值。禁止为每次 rollout 在 `docs/devops/upgrade-records/` 建档，禁止把服务器 dump、日志、凭据或业务 payload 存入 Git。

`alembic_head` 只使用带 schema 限定的查询读取：

```sql
SELECT version_num FROM wes_sys.alembic_version;
```

运维命令中禁止使用未限定的 `alembic_version`。记录本节字段时，继续引用第 3、7、8 节现有的 digest、Compose、readiness、登录、拓扑和 `scripts/wait_for_http.py` 命令；不得复制 helper 实现或另造 manifest。

## 11. 检查清单

- 已记录批准的前后端提交、digest、OpenAPI SHA 和权限 SHA；
- Nginx 在迁移/授权阶段关闭，外部端口实测不可达；
- Compose 标签发现无未知 service，迁移前只有 PostgreSQL/Redis 运行；
- 新后端镜像一次性 Alembic 成功；
- fresh DB bootstrap 或已有 DB `--apply` 成功；若出现精确 post-commit marker 则只 repair 一次且不重跑 mutation；随后 fresh `--check` 零漂移；
- post-commit cache failure 如发生，只执行 repair 后新的 check；
- 六个固定版本应用服务完成内部门禁；
- Nginx 最后恢复，外部健康与首页通过；
- 状态仍明确区分“已部署”与供应商/现场/业务验收。
