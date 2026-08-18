# WES 现场运行时加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 独立修复 Celery Beat schedule、Redis 宿主参数、Nginx 文件描述符/日志和 PostgreSQL 空闲事务保护的现场运行缺口，不混入数据恢复或 schema 重建。

**Architecture:** Beat schedule 使用可重建的独立持久目录，在同机容器重建时保留、灾难恢复时允许从代码配置重新生成；Redis 的 `vm.overcommit_memory` 与 THP 由宿主 systemd/sysctl 管理；Nginx 同时约束 nofile 和宿主日志轮转；PostgreSQL 只增加空闲事务保险丝，应用会话生命周期继续由现有真实 prefork 回归拥有。

**Tech Stack:** Docker Compose、Celery 5.6、Redis 8、Nginx、PostgreSQL 17、Rocky Linux 10、systemd、sysctl、logrotate、pytest。

**Spec:** `docs/architecture/SRS.md`

**Operational inputs:** `docs/devops/rocky-linux-server-inspection.md`、`docs/devops/rocky-linux-server-initialization.md`

**Historical decision input:** `../archive_docs/wes_backend/local-development/2026-08-18-fresh-db-migration-history/2026-08-17-onsite-data-resilience-and-runtime-stability.original.md`。该归档只提供已批准的现场约束来源，不是当前执行真源。

## Global Constraints

- 本计划在 `develop@c579b18a` 上重基线；实施前必须从最新 `develop` 再次验证每个告警、Compose 渲染和现有测试 owner。
- 四个切片可以独立审核和部署；不得把一个切片的绿灯当成其它切片或数据恢复的证明。
- Beat schedule 不是业务权威数据，不进入灾难恢复源；同机重建保留 schedule，异机恢复允许清空并由代码中的 `beat_schedule` 重建。
- Redis AOF/RDB 不作为灾难恢复源。本计划不改变 Redis fail-open/fail-closed 业务语义，不引入 Sentinel 或集群。
- Nginx 只处理现有文件日志和 nofile 不匹配，不引入集中日志、HTTPS 或新网关。
- PostgreSQL `idle_in_transaction_session_timeout` 是保险丝，不替代应用正确关闭事务；不得全局添加 `statement_timeout` 或 `lock_timeout`。
- 现有 `tests/integration/test_celery_async_runtime_postgresql.py` 已拥有真实 prefork 连接与 `idle in transaction` 断言。若它在 current develop 通过，不修改 `src/celery_app/tasks/core.py` 或 `src/celery_app/async_runtime.py`。
- 数据备份/恢复由 `2026-08-18-wes-onsite-data-recovery.md` 拥有；数据库角色、checksums 和 Alembic 基线不在本计划。

## File Structure

### Create

- `deploy/onsite/systemd/wes-disable-thp.service`：开机关闭 THP 并可审计当前状态。
- `deploy/onsite/sysctl/90-wes-redis.conf`：持久化 `vm.overcommit_memory=1`。
- `deploy/onsite/logrotate/wes-nginx`：轮转 `/srv/wes/app/logs/nginx/*.log` 并向容器发送 reopen 信号。
- `tests/deployment/test_onsite_runtime_assets.py`：拥有 Beat、sysctl、THP、Nginx 和 PostgreSQL 配置合同。

### Modify

- `docker-compose.yml`：为 Beat 指定 schedule 路径/持久目录，并统一 Nginx nofile 合同。
- `docker-compose.deploy.yml`：生产覆盖保留相同 Beat 和 Nginx 合同，不恢复源码挂载。
- `postgresql/prod.conf`：增加 `idle_in_transaction_session_timeout = '10min'`。
- `docs/architecture/heavy-test-impact.toml`：为 Compose、PostgreSQL 和宿主资产建立精确 mapping。
- `docs/devops/rocky-linux-server-initialization.md`：增加安装、验证和逐服务回滚步骤。
- `docs/architecture/file_index.md`：登记宿主运行资产。
- `docs/superpowers/README.md`：维护本计划状态；全部切片完成后外部归档。

---

### Task 1: Persist the Celery Beat Schedule on the Same Host

**Files:**

- Create: `tests/deployment/test_onsite_runtime_assets.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.deploy.yml`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 当前 `celery_beat` command、镜像内 `beat_schedule` 和生产 Compose override。
- Produces: 显式 schedule path 与独立宿主持久目录，同机强制重建后仍可读取。

- [ ] **Step 1:** 先写 Compose 合同测试，锁定 `--schedule=/var/lib/celery/celerybeat-schedule`、独立 host bind、生产覆盖不丢卷、目录不与 Redis/PostgreSQL共享、没有第二个 Beat replica。
- [ ] **Step 2:** 运行 `uv run pytest tests/deployment/test_onsite_runtime_assets.py -q`，确认因当前 Beat 仅挂载 logs/profile 而 RED。
- [ ] **Step 3:** 最小修改两个 Compose 文件；不改变业务 schedule、queue、worker并发或 Redis数据库编号。
- [ ] **Step 4:** 运行聚焦测试与 `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.deploy.yml config -q`；缺少现场 profile 时使用测试提供的显式非秘密占位路径，不修改 `.env.prod`。
- [ ] **Step 5:** 在隔离 Compose project 中强制重建 Beat，验证 schedule 文件保留且当前代码 schedule 仍全部注册；随后清理该测试 project，不删除共享数据卷。
- [ ] **Step 6:** 更新 HEAVY mapping；获得授权后提交 `fix(ops): 持久化 Celery Beat 调度状态`。

### Task 2: Install Redis and Nginx Host Prerequisites

**Files:**

- Modify: `tests/deployment/test_onsite_runtime_assets.py`
- Create: `deploy/onsite/systemd/wes-disable-thp.service`
- Create: `deploy/onsite/sysctl/90-wes-redis.conf`
- Create: `deploy/onsite/logrotate/wes-nginx`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.deploy.yml`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 当前 Redis/Nginx service、宿主目录 `/srv/wes/app` 和现有 Docker `json-file` 轮转。
- Produces: 可安装、可检查、可逐项回滚的 THP/sysctl/logrotate/nofile 资产。

- [ ] **Step 1:** 先扩展 deployment 测试，锁定 `vm.overcommit_memory=1`、THP unit 幂等、Nginx host 日志路径、postrotate reopen、Compose nofile soft/hard 一致性和非秘密权限。
- [ ] **Step 2:** 运行聚焦测试确认 RED，再添加最小宿主资产和 Compose nofile 配置；不修改 Redis 持久化策略或 Nginx 网络端口。
- [ ] **Step 3:** 在开发机运行 `systemd-analyze verify`、deployment 测试中的 sysctl 文件解析、`logrotate --debug`、Compose config 和聚焦测试。`sysctl --system` 会修改宿主内核参数，不属于静态验证；没有独立现场授权时不得运行。
- [ ] **Step 4:** 在授权现场逐项安装并验收：先安装 sysctl/THP 资产并运行 `sysctl --system`、核对实时值，再处理 Nginx nofile/logrotate；每项失败只回滚当前资产。
- [ ] **Step 5:** 更新 mapping；获得授权后提交 `fix(ops): 固化 Redis 与 Nginx 宿主约束`。

### Task 3: Add the PostgreSQL Idle-transaction Safety Fuse

**Files:**

- Modify: `tests/deployment/test_onsite_runtime_assets.py`
- Modify: `postgresql/prod.conf`
- Verify: `tests/integration/test_celery_async_runtime_postgresql.py`
- Modify only if a fresh real regression fails: `src/celery_app/tasks/core.py`
- Modify only if a fresh real regression fails: `src/celery_app/async_runtime.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 当前生产 PostgreSQL配置和现有 Celery prefork HEAVY owner。
- Produces: 10分钟服务端保险丝以及不超过现有测试允许范围的应用会话证据。

- [ ] **Step 1:** 先在 deployment 测试中锁定 `idle_in_transaction_session_timeout = '10min'`，并断言没有新增全局 `statement_timeout` 或 `lock_timeout`。
- [ ] **Step 2:** 运行 deployment 测试确认 RED，最小修改 `postgresql/prod.conf` 后转 GREEN。
- [ ] **Step 3:** 在独占 PostgreSQL/Redis 环境运行 `RUN_WORKLINE_INTEGRATION=1 uv run pytest tests/integration/test_celery_async_runtime_postgresql.py -q`。PASS 时禁止修改 Celery生产代码；FAIL 只有真实 Worker 连接证明确有回归时才暂停并申请扩大变更面。
- [ ] **Step 4:** 运行 PostgreSQL配置解析、聚焦测试和 selector 选中的 HEAVY；获得授权后提交 `fix(database): 增加空闲事务保护`。

### Task 4: Publish Installation and Rollback Steps

**Files:**

- Modify: `docs/devops/rocky-linux-server-initialization.md`
- Modify: `docs/architecture/file_index.md`
- Modify: `docs/superpowers/README.md`

**Interfaces:**

- Consumes: Tasks 1–3 最终机器合同。
- Produces: 初级现场工程师可按切片安装、验收和回滚的当前步骤。

- [ ] **Step 1:** 在初始化手册中增加 Beat目录权限/SELinux、sysctl/THP、Nginx nofile/logrotate、PostgreSQL timeout 的安装与读取验证；不要复制资产正文。
- [ ] **Step 2:** 明确顺序为 Beat、Redis/THP、Nginx、PostgreSQL；每步只重建一个受影响服务，失败停止，不使用 `down -v`。
- [ ] **Step 3:** 更新文件索引和本计划状态，运行 `git diff --check`、引用扫描和路径存在性检查；纯文档部分不新增测试。
- [ ] **Step 4:** 在最终 executable snapshot 上运行聚焦测试、QUALITY、staged selector/HEAVY、GitNexus staged detection 和一次完整只读 Review。
- [ ] **Step 5:** 现场 rollout、Commit、Push、PR、Merge 和发布元数据分别取得授权；技术健康不等于 WMS/ECS或业务验收。

## Definition of Done

- Beat schedule 在同机强制重建后保留，灾难恢复仍允许空 schedule 由代码重建。
- Redis 无 overcommit/THP宿主告警；Nginx nofile 与日志轮转合同通过。
- `postgresql/prod.conf` 含10分钟空闲事务保险丝，现有真实 Celery prefork 回归非 skip 通过。
- 每个切片拥有聚焦测试、精确 HEAVY mapping、安装和回滚步骤；未借此宣称数据恢复或业务验收完成。
