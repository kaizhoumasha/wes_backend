# 生产环境部署 Runbook

> 状态：`implementation_baseline`
>
> 系统尚未发布。本 Runbook 只适用于新的空数据库和当前仓库模型，不提供旧镜像、旧 schema、旧数据或旧
> Runtime 的升级、降级与兼容流程。本文服务于收敛期间仍在执行的 Jenkins/Compose 部署路径，不是 Phase 11
> 最终 Alembic 单一基线的设计真源；部署路径或 WMS 配置完成替换时必须同步更新。

## 1. 发布边界

- `develop` 产出 TEST 环境镜像；`main` 产出生产候选镜像。
- 部署必须使用 Jenkins 产出的 immutable tag，channel tag 只用于定位最新候选版本。
- 数据库必须为空，或已由当前干净基线创建；不允许把历史开发数据带入发布。
- 架构收敛期间如最终模型发生破坏性变化，直接清理开发、测试和预发布数据库并重新生成基线。
- 生产首次发布前不维护迁移链、旧镜像回滚或双版本共存能力。

## 2. 前置条件

部署机必须具备：

- Docker 与支持 `!override` 的 Docker Compose 2.24.4 或更高版本；
- 与目标镜像提交一致的 `docker-compose.yml`、`docker-compose.deploy.yml` 和 `.env.prod`；
- 当前工厂唯一且只读挂载的 WMS Provider profile；
- 当前模型对应的空 PostgreSQL 数据库和 Redis；
- 首次启动管理员所需的 bootstrap 凭据。

生产配置至少确认：

- `USE_SNOWFLAKE_ID=true` 及唯一的 datacenter/worker ID；
- `WMS_PROVIDER_PROFILE_HOST_FILE=/etc/wes/wms-provider.yaml`，且部署用户可读；
- Provider profile 显式提供 WMS origin `server_url` 和搬运提交相对路径 `transport_submit_path`，且与目标 WMS OpenAPI 路由一致；
- API、Celery 与 CLI 数据库角色、连接池和 overflow 配置符合容量预算；
- 不存在宿主机源码覆盖 `/app/src` 的 volume。

## 3. 选择并拉取镜像

```bash
export BACKEND_IMAGE=192.168.0.220:5050/wes/wes_backend:<immutable-tag>

docker login 192.168.0.220:5050
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  pull api celery celery-wms-fulfillment celery_beat flower
```

先验证合并配置，不启动应用：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  config >/dev/null
```

## 4. 启动基础设施并执行容量门禁

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  up -d --wait db redis

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  api python scripts/capacity_guard.py --services api,celery,celery-wms-fulfillment
```

容量门禁失败时保持数据库与 Redis 在线，停止发布并修正目标拓扑；不得部分启动应用服务。

## 5. 初始化当前 schema

确认目标数据库为空后，使用目标镜像创建当前 schema：

```bash
# 按 Compose project 静默所有会访问 schema 的应用容器；保留数据库、Redis 与无数据库访问的 Nginx 入口。
# 通过标签识别服务，不维护任何历史服务名清单。
compose_project_name=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env.prod | tail -n 1)
compose_project_name=${compose_project_name:-$(basename "$PWD")}
for container_id in $(docker ps -q \
  --filter "label=com.docker.compose.project=${compose_project_name}"); do
  compose_service=$(docker inspect --format \
    '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id")
  case "$compose_service" in
    db|redis|nginx) ;;
    *) docker stop "$container_id" ;;
  esac
done

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  api alembic upgrade head
```

`alembic upgrade head` 在这里是空库 schema 初始化入口，不是旧数据迁移。初始化失败时保持应用停止，修复当前
基线或重新创建空数据库；不得启动旧镜像、执行 downgrade 或增加兼容 revision。

## 6. 启动应用与初始化系统数据

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  up -d --remove-orphans --no-build --no-deps \
  api celery celery-wms-fulfillment celery_beat flower nginx

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api python scripts/data/sync_permissions.py

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api python scripts/data/sync_menus.py --frontend-path /opt/wes_frontend
```

首次部署且系统内没有管理员时，显式设置 `BOOTSTRAP_ADMIN_*` 后执行：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api bash scripts/data/bootstrap_admin.sh
```

不得在生产环境执行开发数据 seed。

## 7. 发布验收

```bash
curl --fail http://127.0.0.1:8001/health
curl --fail http://127.0.0.1/health

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  ps

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  logs --tail=200 api celery celery-wms-fulfillment celery_beat nginx
```

验收必须确认：

- 所有服务运行同一个 immutable tag；
- schema 已由当前基线创建；
- WMS Provider profile 加载成功；
- API、通用 Worker 和 WMS fulfillment Worker 使用各自明确队列；
- 健康检查、权限同步、菜单同步和管理员 bootstrap 符合预期。

## 8. 失败处理

系统未发布阶段只允许前向修复：

1. 停止全部应用服务，保持数据库和 Redis 在线取证；
2. 修复当前代码、配置或干净 schema 基线；
3. 对开发、测试和预发布环境删除旧数据库并重新创建；
4. 使用新的 immutable tag 从第 3 节重新部署。

不得保留旧镜像兼容路径、schema downgrade、数据转换脚本、双写、双读或旧字段 fallback。正式发布后的恢复与
数据保护策略必须在发布前另行批准，不能从本系统未发布阶段的历史实现推导。

## 9. 检查清单

- 发布来源为 `main` 且已记录 immutable tag；
- Compose 合并配置通过且无源码挂载；
- WMS Provider profile 存在、可读、显式挂载，且 `server_url + transport_submit_path` 与目标 WMS 环境一致；
- 目标数据库为空；
- live PostgreSQL 容量门禁通过；
- 当前干净 schema 初始化成功；
- 应用、Worker、Beat 和 Nginx 使用同一镜像版本；
- 权限、菜单和首次管理员初始化完成；
- 健康检查与关键日志检查通过。
