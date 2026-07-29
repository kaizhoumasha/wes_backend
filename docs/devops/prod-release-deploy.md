# 生产环境发布部署 Runbook

## 1. 适用范围

本文档用于 P9 WES Backend 的生产环境手动发布。当前正式策略如下：

- `develop` 分支：推送 `develop` channel 镜像，供 TEST 自动部署使用
- `main` 分支：推送 `prod` channel 镜像，供生产发布使用
- 其他分支：推送分支同名 channel 镜像，仅用于临时验证，不作为正式生产发布来源

生产发布应优先记录并使用 immutable tag，避免直接依赖可漂移的 `prod` tag。

示例：

```text
192.168.0.220:5050/wes/wes_backend:123-abc1234   # immutable tag
192.168.0.220:5050/wes/wes_backend:prod          # channel tag
```

## 2. 发布前置条件

发布前确认：

- 生产机已准备 backend 部署目录，例如 `/opt/wes_backend`
- 生产机已准备 frontend 源码目录，例如 `/opt/wes_frontend`
- backend 使用 `.env.prod`
- frontend 使用 `.env.frontend.prod`
- `.env.prod` 已启用雪花 ID：
  - `USE_SNOWFLAKE_ID=true`
  - `SNOWFLAKE_DATACENTER_ID`
  - `SNOWFLAKE_WORKER_ID`
- `.env.prod` 已确认 RuntimeInbox canonical payload 上限：
  - `RUNTIME_INBOX_PAYLOAD_MAX_BYTES=1048576`（默认 1 MiB；超过上限的 payload 会在入站边界被拒绝）
- 首次生产部署前，已准备 `BOOTSTRAP_ADMIN_USERNAME` 与 `BOOTSTRAP_ADMIN_PASSWORD`

前后端分开维护 `.env` 文件是正常做法，不会影响部署。后端发布只依赖：

- backend 自身 `.env.prod`
- 前端路由源码 `src/router/index.ts`，供菜单同步脚本读取

## 3. 推荐发布方式

推荐使用镜像化手动部署：

- 基础拓扑由 `docker-compose.yml` 提供
- 运行时镜像覆盖由 `docker-compose.deploy.yml` 提供，并通过 Compose `!override` 显式移除开发源码挂载
- 实际执行时同时加载这两个 Compose 文件

部署机必须使用支持 `!override` 的 Docker Compose 2.24.4 或更高版本。生产 API 与 Celery Worker
只运行目标镜像内的 `/app/src`，不得用宿主机源码或 `/dev/null` 覆盖该目录。

本地已验证以下命令可以正确生成合并后的 Compose 配置：

```bash
BACKEND_IMAGE=example.invalid/wes/wes_backend:prod \
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  config
```

## 4. 标准发布流程

### 4.1 选择发布镜像

在 Jenkins `wes_backend-ci` 成功后，记录目标镜像 tag。

生产发布建议：

- 优先使用 `main` 分支产出的 immutable tag
- `prod` tag 只作为人工确认后的 channel 别名，不作为唯一回滚依据

示例：

```bash
export BACKEND_IMAGE=192.168.0.220:5050/wes/wes_backend:123-abc1234
```

### 4.2 同步部署清单

在生产机同步部署仓库到目标提交，确保 `docker-compose.yml`、`docker-compose.deploy.yml`、脚本和文档与镜像版本对应。

```bash
cd /opt/wes_backend
git fetch origin
git checkout main
git pull --ff-only origin main
```

### 4.3 准备环境文件

```bash
cd /opt/wes_backend
cp -f .env.prod .env
```

如使用外部注入方式，也可以直接保留 `.env.prod`，后续命令统一显式带 `--env-file .env.prod`。

### 4.4 登录镜像仓库并拉取镜像

```bash
docker login 192.168.0.220:5050

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  pull api celery celery-wms-fulfillment celery_beat flower
```

### 4.5 启动基础设施

首次部署或基础设施重建时：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  up -d db redis
```

等待数据库与 Redis 健康检查通过后，保持基础设施在线，再继续容量门禁和应用服务发布。

### 4.6 执行容量门禁

先使用目标应用镜像内的统一脚本读取 live PostgreSQL 的 `max_connections`，并按 `.env.prod` 中完整的
API/Celery 目标拓扑校验连接预算。该短连接固定使用 `cli` 角色、连接池 `1`、overflow `0`：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  api python scripts/capacity_guard.py --services api,celery,celery-wms-fulfillment
```

容量门禁失败时必须停止发布，不得停止、启动或重建任何应用服务；`db`、`redis` 基础设施保持在线，供排障和重试。

### 4.7 静默应用并执行数据库迁移

当前 RuntimeInbox 切换包含破坏性迁移：新代码依赖新增列，旧代码依赖即将删除的 `wes_biz.workline_inbox`。
降级是显式 fail-closed 的：Revision A 不会丢弃 canonical payload，Revision B 不会清空已映射的 RuntimeInbox 身份引用。Revision C 的并发索引可单独降级，但不代表数据迁移可逆。
因此迁移前必须停止所有会访问运行时表的 API、Worker 和 Beat，并使用目标镜像的一次性 CLI 容器执行迁移：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  stop api celery celery-wms-fulfillment celery_beat

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  run --rm --no-deps \
  -e DATABASE_RUNTIME_ROLE=cli \
  -e DATABASE_POOL_SIZE=1 \
  -e DATABASE_MAX_OVERFLOW=0 \
  api alembic upgrade head
```

迁移失败时保持应用停止和基础设施在线，先排查迁移；不得启动新旧任一版本的应用进程。

### 4.8 启动应用服务

迁移成功后才执行：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  up -d api celery celery-wms-fulfillment celery_beat flower nginx
```

### 4.9 同步权限与菜单

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api python scripts/data/sync_permissions.py

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api python scripts/data/sync_menus.py --frontend-path /opt/wes_frontend
```

说明：

- 权限同步会扫描后端路由定义并幂等写入数据库
- 菜单同步依赖 `/opt/wes_frontend/src/router/index.ts`
- 生产环境不要执行 `scripts/data/seed_initial_data.py`

### 4.10 首次生产部署时创建首个管理员

仅在系统中还没有超级管理员时执行：

```bash
export BOOTSTRAP_ADMIN_USERNAME=admin
export BOOTSTRAP_ADMIN_PASSWORD='StrongPassw0rd!'
export BOOTSTRAP_ADMIN_FULL_NAME='系统管理员'
export BOOTSTRAP_ADMIN_EMAIL='admin@example.com'

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api bash scripts/data/bootstrap_admin.sh
```

`bootstrap_admin.sh` 是幂等的：如果库里已经存在超级管理员，会安全跳过。

### 4.11 发布后验收

健康检查：

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1/health
```

容器状态：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  ps
```

关键日志：

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  logs --tail=200 api celery celery-wms-fulfillment celery_beat nginx
```

菜单数量校验：

```bash
docker exec wes_postgres_prod \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select count(*) from wes_sys.menus"
```

## 5. 生产机无法访问镜像仓库时

如果生产机不能直连 `192.168.0.220:5050`，使用离线导入流程：

在可访问仓库的机器上：

```bash
docker pull 192.168.0.220:5050/wes/wes_backend:123-abc1234
docker save -o wes_backend_123-abc1234.tar 192.168.0.220:5050/wes/wes_backend:123-abc1234
```

将 tar 包传到生产机后：

```bash
docker load -i wes_backend_123-abc1234.tar
export BACKEND_IMAGE=192.168.0.220:5050/wes/wes_backend:123-abc1234
```

随后继续执行本文第 4 节的标准发布流程。

## 6. 回滚策略

本次 RuntimeInbox 发布包含破坏性迁移，迁移后禁止自动切回旧镜像。旧镜像仍读取已经删除的
`wes_biz.workline_inbox`，仅替换镜像不能恢复服务，反而会造成持续故障。

- 迁移尚未执行：可以保持旧应用或重新启动旧应用，数据库结构仍与旧镜像兼容。
- 迁移已经成功：停止新应用，确保基础设施保持在线；优先发布兼容当前 schema 的前向修复镜像。
- 必须恢复旧版本：先停止全部应用，使用发布前备份和已核准的数据修复方案恢复数据库，再启动旧镜像。
- 禁止为强行 downgrade 手工清空 RuntimeHold、SMT handoff 或 Session 引用；这些引用是必须保留的审计身份。

Jenkins 在迁移成功后的任意步骤失败（包括容器部分启动、testing 数据同步或健康检查失败）都会停止 API、
Worker 和 Beat，不会自动替换镜像或回滚数据库。恢复动作必须记录当前 Alembic revision、目标镜像和备份点，
并经过人工核准。

## 7. 发布检查清单

- 已确认发布来源为 `main`
- 已记录 immutable tag
- 已备份数据库
- 已确认 `.env.prod` 中雪花 ID 配置正确
- 已确认 `/opt/wes_frontend` 与目标前端版本一致
- 已保持 `db`、`redis` 在线并通过 live PostgreSQL 容量门禁
- 已执行迁移
- 已执行权限同步
- 已执行菜单同步
- 首次上线时已执行管理员 bootstrap
- 已完成健康检查与关键日志检查
