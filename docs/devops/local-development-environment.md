# 本机开发调试环境规范

## 1. 唯一入口与边界

本机完整前后端调试由 `wes_backend` 统一编排，唯一入口是：

```bash
./scripts/dev-env.sh up
./scripts/dev-env.sh check
./scripts/dev-env.sh logs [service...]
./scripts/dev-env.sh down
```

前端仓库的 `docker-compose.yml` 只用于独立生产构建预览，不是前后端联调入口。不得复制 Compose、手工拼接另一套网络或凭历史命令猜测环境。

本环境证明本机代码、持久化基础设施、异步进程、HTTP代理和合同级 Mock 可运行，不证明 TEST 部署、真实 WMS/ECS、供应商一致性、现场物理闭环或业务验收。

## 2. 服务与端口

| 服务 | 本机入口 | 用途 |
| --- | --- | --- |
| frontend | `http://127.0.0.1:5173` | Vite 开发服务器与 HMR |
| nginx | `http://127.0.0.1` | 前端与 `/api/*` 同源代理 |
| api | `http://127.0.0.1:8001` | FastAPI、Swagger与OpenAPI |
| PostgreSQL | `127.0.0.1:5432` | 权威持久化数据 |
| Redis | `127.0.0.1:6379` | 缓存、Celery broker/result |
| ECS Mock | `http://127.0.0.1:8010` | 统一设备合同本机模拟 |
| WMS Transport Mock | `http://127.0.0.1:8011` | Transport T1与callback调试 |
| WMS Provider Mock | `http://127.0.0.1:8012` | 29项full-factory query/effect合同调试 |

Celery通用 worker、WMS fulfillment worker和Beat没有额外宿主机端口，由 `check` 校验运行状态。

## 3. 启动行为

`up` 按固定顺序执行：

1. 校验 Docker、`.env.dev` 和前端仓库路径。
2. 启动并等待 PostgreSQL、Redis健康。
3. 构建后端与Mock开发镜像。
4. 在 API 镜像中执行 `alembic upgrade head`。
5. 挂载当前前端源码，只读解析菜单，幂等收敛基础调试数据。
6. 挂载 `deployment/dev/wms-provider.yaml`，让 API 与异步进程统一连接独立的 WMS Provider Mock。
7. 启动 API、Celery、Beat、ECS Mock、WMS Transport Mock、WMS Provider Mock、Vite前端和Nginx。
8. 执行与 `check` 相同的容器、HTTP、数据和版本检查。

任何步骤失败都返回非零状态。先运行 `./scripts/dev-env.sh logs <service>` 查看失败服务，不得通过跳过 migration、seed或健康检查制造半成品环境。

## 4. 热更新合同

以下运行时代码修改不需要重建镜像：

- 后端 `src/` 与 `main.py`：Uvicorn自动 reload。
- `deployment/`、`packages/wes_plugin_sdk/src/` 与 `workline_plugins/rough_sorter/src/`：API自动 reload，开发态 Celery/Beat检测变更后重启子进程。
- WMS Provider Mock 的 `tests/mock/`、共享 fixture 与 `src/`：Uvicorn自动 reload。
- 前端仓库源码：Vite HMR；Docker Desktop使用文件轮询避免macOS文件事件丢失。

Celery/Beat按 Python 文件内容计算指纹，不依赖秒级修改时间；同一秒连续保存或同尺寸改写也会触发重启。

依赖输入如 `pyproject.toml`、`uv.lock`、`package.json`、`pnpm-lock.yaml` 或 Dockerfile 变化不属于代码热更新，重新执行 `up`。前端依赖只允许 `pnpm install --frozen-lockfile`；依赖缓存同时绑定 lockfile 与容器平台指纹，平台变化会自动重新安装对应原生依赖，锁文件不一致时启动失败且不会由容器改写源码。

## 5. 持久化与停止

- PostgreSQL：`docker_data/postgres_dev/`
- Redis：`docker_data/redis_dev/`
- 日志：`logs/`
- 前端 `node_modules` 与 pnpm store：Compose命名卷

`down` 只移除容器和网络，不传 `-v`/`--volumes`，即使前端 checkout 暂时不存在也可执行。再次 `up` 必须复用已有数据并重新执行幂等迁移和初始化检查。

本规范不提供自动清库命令。需要清理或重建数据时，先确认目标目录和影响，再使用专门的数据工具；不得把删除 volume 作为普通排障步骤。

## 6. 基础调试数据

`scripts/data/seed_initial_data.py` 仅允许由 `dev-env.sh` 在 `ENV=dev`、Compose数据库主机为 `db` 时运行，可重复执行，并把下列基础数据收敛到当前代码与前端源码：

- 当前 FastAPI 路由扫描出的权限及内置角色权限关系；
- 当前前端 router解析出的菜单及内置角色菜单关系；
- `系统管理员、管理员、运营人员、财务人员、普通用户` 五个内置角色；
- `admin、manager、operator、finance、user1、user2` 六个固定账号及对应角色。

开发账号密码由 `.env.dev` 的 `DEV_SEED_PASSWORD` 指定，默认 `admin123`。初始化器会补齐缺失数据、重建当前源码对应的同名软删除记录、修正固定字段和密码，并精确收敛固定账号及内置角色的角色、权限和菜单关联；内置角色关联属于本初始化器的确定性数据，自定义角色、自定义用户及独立权限/菜单记录不会被删除。所有数据库写入在同一事务内完成，任一步失败都会回滚；提交成功后会清理所有绑定内置角色用户的权限缓存。`check` 使用只读模式确认字段和关联均无漂移，权限或菜单定义为空时直接失败。

初始化数据不创建 WorkLine、库存、设备、命令、搬运任务或物理结果。此类数据属于具体业务场景或现场事实，应由对应测试fixture、Mock场景或人工调试操作拥有。

`deployment/dev/wms-provider.yaml` 也是本环境固定输入，只允许指向容器内 `mock-wms-provider:8012`。该 Provider Mock 从同一 profile 注册29项公开路由，并复用现有 typed operation fixture；`check` 会实际调用代表性的 query 与 effect。8011端口的 Transport Mock保持独立，不承载WMS业务Provider接口。两者都只用于本机合同调试，不得复制为 TEST/生产 Provider 配置。

## 7. 快速排障顺序

```bash
./scripts/dev-env.sh check
./scripts/dev-env.sh logs api
./scripts/dev-env.sh logs celery celery-wms-fulfillment celery_beat
./scripts/dev-env.sh logs frontend nginx
./scripts/dev-env.sh logs db redis mock_ecs mock_wms mock_wms_provider
```

`check` 要求除 Nginx 外的关键容器都显式处于healthy；Nginx当前没有容器healthcheck，仅验证running和真实HTTP入口。脚本还验证带超时的HTTP入口、WMS Provider代表性query/effect及其typed响应、只读seed收敛结果，并输出当前前后端分支和完整HEAD。Celery Worker与Beat均检查真实子进程，不把热更新wrapper存活误判为业务进程健康。健康检查只证明技术环境可用；需要验证业务路径时，再运行该领域拥有的聚焦测试或显式E2E，不能用本环境绿灯替代高层验收。
