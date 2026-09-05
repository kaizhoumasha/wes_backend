# P9 WES Backend

**Version**: 0.31.2.0

P9 WES Backend 是基于 FastAPI + SQLModel + SQLAlchemy 2.0 的快速开发框架，专为 WMS/WES 系统设计。采用分层架构和零代码开发模式。

**核心特性**：

- **零代码 CRUD**：继承 BaseAPI 自动生成 REST API
- **ModelFactory**：自动生成 Create/Update Schema
- **Hook 系统**：Repository 层业务逻辑扩展
- **Mixin 组合**：复用模型字段和行为
- **RBAC 权限**：基于角色的访问控制
- **TimescaleDB**：时序数据存储

## Environment Setup

### Prerequisites

- Python 3.13+
- Docker & Docker Compose
- `uv` (recommended for dependency management)

### Development

完整前后端本机调试统一从后端仓库启动：

```bash
./scripts/dev-env.sh up
./scripts/dev-env.sh check
./scripts/dev-env.sh logs api celery frontend
./scripts/dev-env.sh down
```

`up` 会构建开发镜像、启动持久化 PostgreSQL/Redis、执行 migration、幂等初始化权限/角色/固定开发账号，并启动
API、Celery/Beat、WMS/ECS Mock、Vite 前端与 Nginx。前后端运行时代码均支持热更新；`down` 保留数据库、Redis 和前端依赖卷。

- 前端：http://localhost:5173
- Nginx 同源入口：http://localhost
- API：http://localhost:8001
- Swagger UI：http://localhost:8001/api/docs

完整规则、端口、初始化数据和排障方式见[本机开发调试环境规范](docs/devops/local-development-environment.md)。

## Configuration

容器化开发环境固定使用 `.env.dev` 和 `.env.frontend.dev`；宿主机直跑才使用 `./scripts/init-env.sh dev` 生成的 `.env`，两种模式不得混用。

## WES 架构与开发文档

当前架构与实施入口：

- [软件需求规格说明书](docs/architecture/SRS.md)：产品范围、参与方职责以及功能与非功能需求真源。
- [WES 最小执行架构收敛设计](docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md)：当前目标架构真源。
- [WES - WMS 对接接口需求](docs/integration/wes-wms-interface-requirements.md)：WES/WMS 公共接口外发真源；当前公共协议和 Transport 为 `Approved`，WES 实现已 `ALIGNED`；Phase 8 粗分业务合同已批准并完成仓内交付，其余业务附录按各自状态评审。
- [WES 出库操作顶层设计](docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md)：评审中的 `PickingTask` 分批计划、不可逆执行和安全取消业务设计；当前不构成实施授权。
- [WMS / WES 自动入库与上架交互要求](docs/contracts/wms-inbound-putaway-integration-requirements.md)：Phase 13 满箱交换和自动上架的业务合同评审真源；当前为 `ReviewRequired`，不代表已实施。
- [WES 架构收敛十四阶段总控](docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md)：Phase 6–11 已完成；最新 backend `develop@fdfa4725` 与联调部署 revision `e7e3d6af` 具有相同 tree `46d568d1`，frontend 联调部署 revision 为 `a6c3193f`。Transport 0.3 WES 实现已 `ALIGNED`；510056 backend/frontend canonical consumer 已交付，本轮已由操作员确认直接录入值进入真实 WMS/RCS、`SCAN12` 驱动料箱回架并触发最终 `CTU03`，以及刷新或重新登录后仍可恢复终态步骤历史。该确认不替代 release evidence、部署验收或业务 owner 签署；其它 `EVENT_DEBUG` 对账状态仍须按各自证据独立判断。Phase 8 分层证据见[粗分机后端开发验收与现场边界状态](docs/integration/rough-sorter-joint-acceptance.md)。
- [粗分机 WorkLine Epoch 激活与多 Endpoint 派发计划](docs/superpowers/plans/2026-08-19-rough-sorter-workline-epoch-activation.md)：后端工程包 1–4 已完成并提交；前端 Device、WorkLine 配置与 START 操作按独立合同冻结门禁待实施。
- [WORKLINE 业务插件二次开发指南](docs/plugin_development_guide.md)：最小插件 SPI、封闭 Decision 与独立插件包交付约定。
- [WMS 北向交互合同](docs/contracts/wms-northbound-interaction-contract.md)：定义共享 Client 与后续新增具体 WMS API 的开发标准；业务结果由 WMS 给出，搬运与 RCS 状态归 Phase 4。
- [AGV/CTU 通用搬运能力合同](docs/contracts/transport-fulfillment-contract.md)：定义四类搬运请求、同步接纳 ACK、成员位置事实、异步最终结果、幂等和对账边界；Phase 6 已接入唯一生产路由、API/Celery 生命周期和可靠队列，当前唯一生产业务 producer 是粗分换架 `OLD_OUT/NEW_IN`。
- [Transport 自动联调联合验收](docs/integration/transport-joint-acceptance.md)：记录直接录入货架、货架面、料箱与原槽位后的 CTU01/CTU02/CTU03、`SCAN12` Evidence、恢复和现场证据边界。
- [Transport 运维诊断 Runbook](docs/runbooks/transport-operations.md)：优先通过本地 API、结构化日志和 PostgreSQL 事实诊断任务；数据可丢弃的联调环境可按 `transport_task_id` 预检并清理单个任务的完整本地链路。
- [WES 第三方设备统一接口白皮书](docs/integration/third_party_integration_whitepaper.md)：所有固定式设备供应商长期遵循的顶层统一接口（wire）真源。
- [DeviceCommand 核心边界合同](docs/architecture/device-command-contract.md)：设备可靠性、统一接口、设备合同附录与插件的所有权边界。
- [DeviceCommand 运维诊断 Runbook](docs/runbooks/device-command-operations.md)：按数据库事实排查派发、证据、对账和 Epoch fencing，不直接改表或换身份重放。
- [项目文档生命周期与外部归档索引](docs/superpowers/README.md)：查看保留/归档判定与外部归档路径。

以下文档承担现场信息采集、发布或机器门禁职责，不是目标架构设计输入：

- [休斯顿现场服务器现状信息采集表](docs/devops/rocky-linux-server-inspection.md)
- [休斯顿现场服务器初始化与基础支撑环境配置手册](docs/devops/rocky-linux-server-initialization.md)
- [生产发布 Runbook](docs/devops/prod-release-deploy.md)
- [Jenkins CI/CD 配置](docs/devops/JENKINS.md)

## Production Bootstrap

Production should not use `scripts/data/seed_initial_data.py`. That script is dev-only and contains default accounts such as `admin/admin123`.

Use separate env files for backend and frontend:

- Backend: `.env.prod`
- Frontend: `.env.frontend.prod`

This separation is expected and does not affect deployment. Backend route declarations are the only API-permission definition source;
`wes_sys.permissions` is their read-only materialized catalog. The permission management page can query the catalog but cannot create, edit,
move or delete definitions.

Recommended first-time production initialization order:

```bash
./scripts/migrate.sh upgrade
export BOOTSTRAP_ADMIN_USERNAME=admin
export BOOTSTRAP_ADMIN_PASSWORD='StrongPassw0rd!'
export BOOTSTRAP_ADMIN_FULL_NAME='系统管理员'
export BOOTSTRAP_ADMIN_EMAIL='admin@example.com'
bash scripts/data/bootstrap_foundation.sh
uv run python scripts/data/sync_permissions.py --check
```

Notes:

- `bootstrap_foundation` idempotently converges the five built-in roles, route-owned permission catalog, built-in role grants and first superuser.
- Existing deployed databases use the captured-output `sync_permissions.py --apply` control flow in the production runbook, followed by a fresh
  `--check`; `--preview` does not connect to the database.
- `--repair-cache` is only for one exact bare `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED` line; diagnostics are emitted separately with
  `CACHE_INVALIDATION_FAILURE_DETAIL:`. Keep Nginx closed, repair once, never rerun `--apply`, then run the fresh `--check`.
- 前端镜像自行包含静态路由与 `meta.menu` 元数据；后端部署不读取 frontend 源码或菜单产物，也不维护菜单表、菜单 API 或菜单同步步骤。
- Production should enable snowflake IDs in `.env.prod` with `USE_SNOWFLAKE_ID=true`.
- Keep real bootstrap credentials outside git-managed files and inject them from the deployment environment.
- These commands and local tests prove authorization-foundation convergence only; they do not prove deployment, supplier consistency, onsite
  integration or business acceptance.
- Full production release steps: `docs/devops/prod-release-deploy.md`
