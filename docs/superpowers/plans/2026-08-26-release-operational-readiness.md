# 发布运行静默门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan. 这是一个高风险完整行为切片；先完成复用审计，再批量建立一次 RED，集中实现后运行一次 GREEN，不得按文件重复启动 PostgreSQL、HEAVY 或 Review。

status: Tasks 1–4 implemented and verified on codex/phase10-implementation@834fe59e; Task 5 not run
implementation_commit: eb9b1a54
deployment_status: Tasks 1–4 included in Phase 10 Task 7 integration cutover; Task 5 TEST Deploy not run

**Goal:** 在 `BACKEND FULL` 和 `BOTH FULL` 停止执行进程前，以两阶段只读门禁阻止未决、歧义或仍在途物理执行被发布切断。

**Architecture:** 发布 CLI 通过业务 Service 调用一个发布专用只读 Repository，在同一 PostgreSQL statement snapshot 中聚合 `DeviceCommand`、`TransportTask`、`InboundEvidence` 和 `WmsConfirmation` 四张目标权威表；Jenkinsfile 不拼接业务 SQL。Phase 10 一次性 legacy drain 只是原子 cutover 前置条件，不进入长期查询。FULL 发布先在线预检，再停止 Nginx、API 和 Beat admission，保留 worker 收敛已落账内部工作并取得稳定 `READY`，之后才能停止执行 worker。复用现有 candidate-container runner、目标领域状态枚举、失败路径和测试 owner，不创建独立合同模块或通用编排框架。

**Tech Stack:** Python 3.13、SQLModel/SQLAlchemy、PostgreSQL、pytest、Jenkins Pipeline、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-08-26-development-workflow-optimization-design.md`

## Global Constraints

- 本计划改变 FULL 发布时序和物理执行保护，按大型/高风险处理，采用一次 RED → DEV → GREEN。
- Task 2–4 共同组成该内聚切片：Task 2 的 RED、Task 3 的实现和 Task 4 的 GREEN 不得分别提交，也不得在中间快照声称可发布。
- 严格遵守 CLI → Service → Repository → Database；Jenkinsfile 只调用 CLI。
- 门禁只读，不执行取消、重试、修复、claim、lease、ACK、对账或状态迁移。
- 生命周期外状态、`RECONCILING`、不可能字段组合和查询失败 fail closed；不得提供 `force`、`ignore` 或自动重发入口。
- `FRONTEND` scope 不连接数据库，也不运行本门禁。
- 输出只包含状态、分类计数、汇总和生成时间，不输出 payload、endpoint、credential、设备参数或逐行业务明细。
- 本项目未发布，不增加 `v1`、兼容字段、双路径或未来扩展接口。
- Commit、Push、PR、Merge 和 Deploy 分别授权；TEST 验收不得人工制造真实未决物理任务。
- RED/GREEN 需要 selector 覆盖完整行为切片；若 checkout 存在无关机器配置或生产代码差异，优先使用精确暂存快照，只有当前必选验证仍无法隔离时才使用 worktree，不能把混合 manifest 当作本 Task 证据。
- 长期门禁只复用 `CommandStatus`、`TransportTaskStatus`、`InboundEvidenceApplyStatus` 和 `WmsConfirmationStatus`；不引用 `RuntimeIntentStatus`、`SystemOutboxStatus` 或 `RuntimeInbox` 状态机。
- 四张表的所有分类计数、未知状态和不可能字段组合必须来自同一组有界 aggregate subquery 组成的 SQL statement；PostgreSQL statement 使用 10 秒硬 `statement_timeout`，Service 同时用 `asyncio.timeout(10)` 取消超时查询，候选容器单次探测硬超时 15 秒。
- `generated_at` 使用 `timezone.now_utc().isoformat()`；不得使用 naive datetime 或 Jenkins 主机时间生成业务结果。
- 不默认新增索引或 migration；只有实际 `EXPLAIN` 和代表性数据证明 10 秒目标无法满足时，才暂停并另行确认索引变更。
- `RuntimeInbox`、`RuntimeIntentLog` / Effect、`SystemOutbox`、`RuntimeHold` 和 `ExecutionSession` 是 Phase 10 legacy owner；不得成为新 DTO、registry、查询、兼容路径或空 schema 依赖。Phase 11 只删除 Phase 10 证明零生产消费者的无 owner schema。
- Phase 9 已合入 `develop@c5a93872`。Phase 10 Task 0 prerequisite freeze、本计划 Tasks 1–4 的 RED/DEV/GREEN/Review、
  Task 0 final admission 与 Execution Lock 已在 `codex/phase10-implementation` 闭合；实现已纳入 `834fe59e` 不可变候选并由
  Phase 10 Task 7 完成联调环境 cutover，但发布分支尚未合入 `develop`，本计划 Task 5 TEST Deploy 仍未执行。
  不使用 feature flag、legacy adapter、双查询或兼容 facade。

## FULL 发布时序

```text
ONLINE
  │
  ├─ online preflight 非 READY ───────────────> 在线终止，无停机
  │
  └─ READY ─> stop nginx ─> graceful stop api ─> stop celery_beat ─> workers drain
                                                                     │
                                                                     ├─ 连续两次 READY（间隔 2s，最多 60s）
                                                                     │      └─ stop workers -> switch -> backup/migrate
                                                                     └─ BLOCK / query error / timeout
                                                                            └─ 维护态保持，禁止自动修复或重发
```

当前 Compose 同时发布 Nginx、API `8002` 和 Redis `6380` 宿主端口，只停 Nginx 不能关闭 admission。FULL 门禁必须停止 Nginx、优雅停止 API、停止 `celery_beat`，但保留 Redis 与 worker 用于排空。Task 1 还必须产出 Redis 宿主端口的 listener/bind、防火墙、ACL/credential owner 和 producer 连接清单，并证明任一可产生下游可靠对象的工作均持久落入四表谓词；证明失败则停止实施并更新合同，不能靠延长等待掩盖盲区。Phase 10 首次发布另有一次性 legacy drain 和零旧 owner 原子切换前置；后续普通 FULL 发布不再读取 legacy 表。

API 关闭后，依赖新 HTTP callback 的状态不保证自然排空；60 秒窗口只允许 worker 收敛不依赖新 callback 的已落账内部工作。窗口结束仍为 `WAIT_DRAIN` 时按 cutover 失败处理，不自动重开入口或声称排空成功。

---

### Task 1: 冻结复用边界和当前合同

**Classification:** 只读架构审计；不修改文件、不运行测试。

**Inspect:**

- `src/app/device/repositories/command_repository.py`
- `src/app/transport/repository.py`
- `src/app/execution/repositories/inbound_evidence_repository.py`
- `src/app/execution/repositories/wms_confirmation_repository.py`
- `src/app/execution/models/inbound_evidence.py`
- `src/app/execution/models/wms_confirmation.py`
- Phase 10 已批准详细计划的当前边界，以及 Task 0 prerequisite freeze 的 minimum cutover order、candidate runner 和 readiness insertion-point interface
- `Jenkinsfile.test-deploy`
- `docker-compose.test-deploy.yml`
- `tests/deployment/test_test_deploy_cutover.py`

**Success:** 证明哪些现有读取能力可直接复用，确认不存在等价的跨账本发布静默门禁，并冻结真实状态字段和部署插入点。

- [x] **Step 1: 核对目标符号、索引和当前调用链**

按项目规则对计划修改的生产符号批量运行 GitNexus upstream impact；GitNexus 不可用时使用精确 `rg`、调用点和现有测试降级。记录 DeviceCommand、TransportTask、InboundEvidence、WmsConfirmation 的权威状态定义、字段不变量和可用索引，不能以计划中的字符串代替当前模型事实。冻结 Task 4 性能验证要使用的代表性数据规模、分布、建数据程序和现有索引清单；此时最终 statement 尚未实现，不执行或伪造 `EXPLAIN`，也不新增索引。

- [x] **Step 2: 完成复用清单**

逐项记录四个目标领域现有 count/query 方法是否只读、是否接受状态集合、是否会 lock/claim/commit、是否已经覆盖所需账本。复用领域枚举和查询表达式经验；现有分表 count 不能提供同一 statement snapshot，因此统一读取由一个发布专用只读聚合 Repository 承担，不把它扩成通用监控 Repository。旧 Inbox/Intent/Outbox/Hold/Session 读取能力只能作为 Phase 10 一次性 drain 证据，不进入本 Repository。

- [x] **Step 3: 证明 admission closure 完整**

枚举 Nginx、API `8002` 直连、Redis `6380`、`celery_beat`、三个 worker 队列和可能产生下游工作的任务。必须证明：Nginx 与 API listener 都关闭后不存在仍可写入执行账本的 HTTP 入口；停止 `celery_beat` 后不再产生周期工作；Redis 宿主端口有实测 listener/bind、防火墙、ACL/credential owner 和 producer 连接清单；Celery 消息只是扫描提示。从任一可产生下游可靠对象的上游持久记录首次提交可见，到对应下游对象提交可见，每个 snapshot 都至少命中四表中一个 `WAIT_DRAIN` 或 `BLOCK` 谓词。

额外核对 Phase 10 详细计划和 Task 0 前置冻结。一次性 cutover 必须先由 candidate-container 在线预检，再关闭旧 admission/Beat，保留旧 worker 排空 legacy owner；legacy 连续稳定为零且四表连续 `READY` 后才停旧 worker、激活只装配四个目标 owner 的 candidate，并在重开 admission 前复核四表和旧 import/task/Compose/schema owner absence。此处只消费 Task 0 冻结的 minimum cutover order、candidate runner 和 readiness insertion-point interface；具体 legacy SQL、broker inspection 命令和 rehearsal 仍由 Phase 10 Task 4 独占。上述接口、successor 或 cutover 最小顺序未冻结时，本 Task 仅能更新审计证据，不得进入 Task 2；不要求 Phase 10 已退出或已取得 Execution Lock。

- [x] **Step 4: 冻结部署插入点和复用点**

只复用 Phase 10 Task 0 已冻结的现有 `business_preflight()` candidate `api` 容器 Python 调用边界，提取一个最小 `candidate_backend_python` helper 供最小 WMS target config/readiness 与本门禁共用，不创建第二套 runner；不得复用旧 Provider Profile 的加载、校验、digest 或语义。在线预检位于方向兼容与最小 WMS target config/readiness 检查后、任何 live runtime、maintenance 或数据库 mutation 前；权威复核位于 Nginx、API 和 Beat admission 关闭后、执行 worker 停止和 `switch_live_deploy_source` 前。当前 API entrypoint 使用 `exec uvicorn` 直接接收停止信号；实施固定使用 `compose stop -t 30 api` 并验证 listener 关闭，不得省略为无等待硬停止。

出现状态不一致、admission closure 无法证明、候选镜像不能只读连接当前数据库或插入点无法满足上述顺序时，停止实施并更新设计，不进入 RED。

### Task 2: 建立一次完整 RED

**Classification:** 高风险回归测试；复用现有测试目录，不拆成多轮 RED。

**Files:**

- Create: `tests/runtime/orchestration/test_release_operational_readiness_service.py`
- Create: `tests/integration/test_release_operational_readiness_postgresql.py`
- Create: `tests/scripts/test_check_release_operational_readiness.py`
- Modify: `tests/deployment/test_test_deploy_cutover.py`
- Modify: `docker-compose.test-deploy.yml`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Required behavior:**

- `BLOCK` 优先于 `WAIT_DRAIN`，两者都为空才是 `READY`。
- `READY/BLOCK/WAIT_DRAIN/查询失败` 分别返回退出码 `0/2/3/1`。
- PostgreSQL 用一个 statement snapshot 聚合四张表的全部分类、未知状态和不可能字段组合；结果不包含业务行和 payload。
- `DeviceCommand` 仅 `PENDING/DISPATCHING/ACKNOWLEDGED` 为 `WAIT_DRAIN`，`RECONCILING` 为 `BLOCK`。
- `TransportTask` 的 `PENDING/ACCEPTED` 或 `outcome_version > published_outcome_version` 为 `WAIT_DRAIN`，`RECONCILING` 为优先级更高的 `BLOCK`；`published_outcome_version > outcome_version` 或版本差存在但 `outcome_json IS NULL` 为 invalid。
- `InboundEvidence.PENDING` 或可 claim 的 `APPLIED + published_at IS NULL` 为 `WAIT_DRAIN`，`RECONCILING` 为 `BLOCK`；未绑定 `material_execution_id` 的 `DEVICE_RESULT` 保持真实 claim 排除，`IGNORED` 和已发布 `APPLIED` 是本门禁终态。
- 仅对 `material_execution_id IS NOT NULL` 且可能驱动 FactProcessor 的绑定业务执行结果，Device result 交接必须保持连续谓词：设备结果事务前，命令本身在进入确定终态前必须命中 `WAIT_DRAIN/BLOCK`；同一事务内，命令进入确定终态时必须已有并保持可 claim 的 `InboundEvidence.APPLIED + published_at IS NULL`；事务后继续由该 unpublished evidence 命中 `WAIT_DRAIN`，不得出现 cleared-before-evidence snapshot。`MANUAL_DEBUG` / `EVENT_DEBUG` 等 `material_execution_id IS NULL` 的未绑定诊断结果按既有例外终态化，不要求 claimable evidence、不唤醒 FactProcessor，也不制造虚假 `WAIT_DRAIN`。
- `WmsConfirmation.PENDING/DISPATCHING` 为 `WAIT_DRAIN`，`RECONCILING` 为 `BLOCK`，`COMPLETED` 为终态。
- 任一表出现生命周期之外状态或其它不可能字段组合时退出码为 `1`，不得因已知分类计数为零而返回 `READY`。
- `FRONTEND` 和 FAST 不调用门禁；`BACKEND FULL`、`BOTH FULL` 先在线预检，再在优雅停止 Nginx/API/Beat admission 后权威复核。
- TEST deploy Redis 宿主端口必须显式绑定 `127.0.0.1`，并由 deployment contract test 锁定；共享强密码仍是当前 credential owner，Firewalld 不开放 Redis 端口。不得在本阶段建设 ACL 平台或多租户 credential 系统。
- 在线 `BLOCK`、`WAIT_DRAIN` 和查询失败保持服务在线并终止；维护态权威复核必须连续两次 `READY`，`BLOCK`、查询失败或 60 秒超时保持入口关闭并终止。
- 权威复核稳定前不得停止 worker、切换部署源、备份或 migration。

- [x] **Step 1: 一次性编写四个现有所有权测试**

Service 测试覆盖优先级、非负计数、四表未知状态和不可能字段组合；PostgreSQL 测试覆盖上述四表谓词、真实 `DEVICE_RESULT` claim 排除、Transport 未发布 outcome、终态排除、同一 statement snapshot 和查询只读；CLI 测试覆盖四个退出码、canonical JSON、`generated_at`、10 秒查询取消和异常脱敏；部署测试覆盖三种 scope、FAST/FULL、Redis `127.0.0.1` 宿主绑定、Nginx/API/Beat 关闭与 listener 验证顺序、连续 READY、超时和失败短路。测试还必须以外部可观察 snapshot 断言锁定以下交易边：对 `material_execution_id IS NOT NULL` 的绑定业务执行结果，Device result 处理前由未终态 command 命中、同一事务内命令转确定终态时 claimable unpublished evidence 已存在并保持、事务后由该 evidence 继续命中以证明无 cleared-before-evidence snapshot；对 `MANUAL_DEBUG` / `EVENT_DEBUG` 等 `material_execution_id IS NULL` 的未绑定诊断结果，断言其按例外终态化、不要求 claimable evidence、不唤醒 FactProcessor 且不产生虚假 `WAIT_DRAIN`；execution fact 的下游对象与 `published_at` 原子交接；Transport outcome 在 evidence 可见后才回写 published version；WMS result evidence 与 confirmation 终态原子交接。

测试断言必须使用外部可观察结果，不能从生产常量导入期望状态形成同源断言。

- [x] **Step 2: 同步 PostgreSQL HEAVY owner 与 deployment FAST owner**

`tests/integration/test_release_operational_readiness_postgresql.py` 是四表 aggregate、只读、同 statement、数据库硬超时和性能边界的 PostgreSQL HEAVY owner；为该测试、计划新增的生产查询路径和 CLI 增加精确 HEAVY mapping。现有 `tests/deployment/test_test_deploy_cutover.py` 是部署顺序、候选探测超时和 maintenance-state 失败的 FAST cutover owner，不进入仅允许 `integration/e2e/resilience/load/mock` 的 HEAVY manifest；Jenkins TEST pipeline 继续遵循既有 selector ignore 合同，Compose 等候选执行输入继续复用其当前精确 HEAVY mapping，并由 deployment FAST owner 锁定本切片的静态顺序和 loopback 合同。未知路径继续 fail closed，不创建宽泛 glob 或 `heavy_tests = []`，也不为本功能扩大 selector 测试拓扑。

- [x] **Step 3: 运行一次 RED 批次**

```bash
uv run pytest tests/runtime/orchestration/test_release_operational_readiness_service.py tests/scripts/test_check_release_operational_readiness.py tests/deployment/test_test_deploy_cutover.py -q
./scripts/run_selected_heavy_local.sh --scope unstaged
```

Expected: FAST 因 Service、CLI 和 pipeline 行为尚不存在而失败；HEAVY 在 mapping 已闭合后因只读聚合尚未实现而失败。环境未启用导致的 skip 不算 RED。

### Task 3: 实现最小只读门禁

**Classification:** DEV；只实现 Task 2 已冻结的行为。

**Files:**

- Create: `src/app/runtime/orchestration/repositories/release_operational_readiness_repository.py`
- Create: `src/app/runtime/orchestration/services/query/release_operational_readiness_service.py`
- Modify: corresponding repository/service `__init__.py` exports
- Create: `scripts/check_release_operational_readiness.py`
- Modify: `Jenkinsfile.test-deploy`
- Modify: `docker-compose.test-deploy.yml`
- Modify: `docs/devops/prod-release-deploy.md`

**Interfaces:**

- Service input: 当前数据库会话。
- Service output: `state`、分类 `counts`、`wait_drain_total`、`block_total` 和 `generated_at`。
- CLI output: stdout 单行 JSON；stderr 只包含脱敏错误；退出码 `0/1/2/3`。

- [x] **Step 1: 实现单一判定所有者**

状态分组、输出 DTO、退出状态和优先级放在同一个 Service 模块；复用 `CommandStatus`、`TransportTaskStatus`、`InboundEvidenceApplyStatus` 和 `WmsConfirmationStatus`。不引用 legacy 状态枚举，不增加独立合同模块、`schema_version`、Protocol、兼容 alias、singleton 或通用状态注册中心。Service 只聚合计数并判定，不承担部署编排。

- [x] **Step 2: 实现最小只读查询**

Repository 使用 SQLAlchemy scalar subquery/conditional aggregate 在一个 SQL statement 中返回四表全部分类计数、未知状态和不可能组合计数；每个 aggregate 只按冻结状态/不变量谓词计数，不加载业务行、不锁表、不写审计、不 commit。查询设置 10 秒硬 PostgreSQL `statement_timeout`，Service 再用 `asyncio.timeout(10)` 包裹该唯一查询并将取消/超时映射为查询失败。它是发布读模型，不替代各领域业务 Repository。本 Step 不新增索引或 migration；最终查询的性能证明在 Task 4 GREEN 执行。

- [x] **Step 3: 实现薄 CLI**

CLI 只负责配置、数据库会话、调用 Service、JSON 序列化和退出码映射。异常统一映射为退出码 `1`，不得打印数据库 URL、SQL 参数或 payload；生成时间只调用 `timezone.now_utc().isoformat()`。

- [x] **Step 4: 接入 FULL 部署**

提取并复用 `candidate_backend_python`，保留现有 `business_preflight()` 行为。只对 `BACKEND/BOTH FULL` 执行：

1. 在 maintenance 前运行一次在线探测，非 `READY` 调用 `abort_pre_cutover`；
2. 在线 `READY` 后进入维护态，关闭并验证 Nginx listener，再执行 `compose stop -t 30 api`、验证 `APP_HOST_PORT` listener 关闭，随后停止 `celery_beat`；
3. 保持 worker 运行，每 2 秒运行探测，连续两次 `READY` 才继续，整体最多 60 秒；仍为 `WAIT_DRAIN` 只表示未静默，不保证能在 API 关闭后排空；
4. 稳定 `READY` 后才允许 `switch_live_deploy_source` 和 `run_full_cutover` 停止 worker；
5. 维护态内 `BLOCK`、查询失败或超时调用既有 `fail_cutover`，保持外部入口关闭。

`FRONTEND` 和 FAST 路径保持原样；不增加 force/ignore，不在 pipeline 内修改业务记录。

同时把 TEST deploy Redis 宿主端口从未限定地址的 `${REDIS_HOST_PORT:-6380}:6379` 收紧为 `127.0.0.1:${REDIS_HOST_PORT:-6380}:6379`，与现有服务器初始化合同“数据库/Redis 只绑定 loopback、Firewalld 不开放端口”一致。容器内服务继续通过 Compose network 的 `redis:6379` 使用共享强密码；本切片不新增 Redis ACL 平台。

- [x] **Step 5: 更新当前 Runbook**

只更新现有生产发布文档，说明在线预检、维护态稳定复核、四种结果、60 秒超时、重新触发方式和明确禁止的自动修复行为；不新建同义流程文档。

### Task 4: 运行一次 GREEN、性能验证和最终同快照 Review

**Classification:** GREEN、代表性性能验证与唯一主 Review；只刷新当前行为切片覆盖的证据。

- [x] **Step 1: 冻结、精确暂存并记录最终 cached fingerprint**

冻结 exact code/test snapshot；只按 Tasks 2–4 文件清单精确暂存，核对 `git diff --cached --name-status`、cached diff 和 `git diff --cached --check`，记录 cached tree/diff fingerprint。空 index、stale index、可执行树混入 unstaged/untracked 变化或 fingerprint 不匹配均为阻断错误；Commit 仍需单独授权。

- [x] **Step 2: 对同一 cached snapshot 运行一次 FAST GREEN**

```bash
uv run pytest tests/runtime/orchestration/test_release_operational_readiness_service.py tests/scripts/test_check_release_operational_readiness.py tests/deployment/test_test_deploy_cutover.py -q
```

- [x] **Step 3: 对同一 cached snapshot 运行一次 PostgreSQL/deployment GREEN**

```bash
uv run scripts/select_heavy_tests.py --scope staged
./scripts/run_selected_heavy_local.sh --scope staged
```

Expected: selector manifest 精确包含 `tests/integration/test_release_operational_readiness_postgresql.py`，真实 PostgreSQL 测试执行且零 skip；现有 deployment cutover owner 覆盖停止顺序、候选探测超时和 maintenance-state 失败；有界聚合在同一 statement 中完成，PostgreSQL 硬 `statement_timeout` 与 Service 取消均被验证。不得在 GREEN 后为统计耗时再次运行相同 HEAVY。

- [x] **Step 4: 验证最终单 statement 的代表性性能**

在隔离数据库或经批准的只读副本上，使用 Task 1 冻结的代表性数据集，对实现后的唯一有界 aggregate statement 执行 `EXPLAIN (ANALYZE, BUFFERS)`，并通过实际 Service 路径测量端到端耗时、验证 10 秒 PostgreSQL `statement_timeout` 和 Service 取消边界。记录数据规模/分布、执行计划、实际耗时、buffer 和当前索引，并与 Step 1 的 cached fingerprint 绑定。若现有索引不能满足预算，立即停止本切片并单独申请 index/migration 批准；未获批准不得修改索引、migration 或放宽超时。

- [x] **Step 5: 完成静态、差异检查与主 Review**

```bash
git diff --check -- src/app/runtime/orchestration scripts/check_release_operational_readiness.py tests/runtime/orchestration/test_release_operational_readiness_service.py tests/integration/test_release_operational_readiness_postgresql.py tests/scripts/test_check_release_operational_readiness.py tests/deployment/test_test_deploy_cutover.py Jenkinsfile.test-deploy docker-compose.test-deploy.yml docs/devops/prod-release-deploy.md docs/architecture/heavy-test-impact.toml
```

确认所有结果仍绑定 Step 1 的 cached fingerprint，再由一个只读 Reviewer 对相同 cached diff 完成唯一主 Review；Reviewer 不重复 GREEN 或 HEAVY。任何生产、测试、脚本、配置或 staged snapshot 变化都会使 Steps 1–5 证据失效，必须从冻结与精确暂存重新开始。QUALITY 由已授权 Commit 的 hook 产生，CI 再对候选 Commit 运行权威 QUALITY 和 selector HEAVY；这是不同环境的交付门禁，不是本地重复测量。

- [x] **Step 6: 提交（仅已授权时）**

再次核对 cached fingerprint、`git diff --cached --name-only`、cached diff 和 `git diff --cached --check` 与 Steps 1–5 完全相同后提交。暂存不等于 Commit 授权；不得使用 `--no-verify` 或把无关计划、联调资料带入 Commit。

### Task 5: TEST 环境验收

**Classification:** 部署验收；只有获得 Deploy 授权后执行。

当前状态：本计划 Task 5 未取得 TEST Deploy 授权，以下步骤均未执行。Phase 10 Task 7 的一次性联调 cutover 证据不能替代普通
TEST FULL 发布验收，也不代表供应商、物理或业务验收完成。

- [ ] **Step 1: 验证 READY 路径**

Tasks 1–4 已在 Phase 10 Execution Lock 前独立完成 RED/DEV/GREEN/Review；Phase 10 Task 6 复核该基线并将其纳入不可变 backend candidate。首次切换由 Phase 10 Task 7 使用 candidate-container 读取现场四表，并展示 legacy drain、producer 封闭、旧 API/Beat/worker 停止和旧 import/task/Compose/schema owner 缺席证据；这些是一次性 cutover 前置，不得改造为长期查询。Phase 10 完成后的普通 TEST FULL 验收使用同一门禁，日志必须依次出现在线 `READY`、maintenance-stop、Nginx/API listener 关闭、Beat 停止、两次稳定 `READY`，随后才允许 worker stop 和 migration；健康检查不能替代此顺序证据。

- [ ] **Step 2: 复用隔离测试证明失败路径**

`BLOCK`、`WAIT_DRAIN`、四表未知状态、不可能字段组合、查询失败和静默超时由 Task 4 的隔离 PostgreSQL 及 pipeline 测试证明。不得在共享 TEST 或现场数据库人工插入、修改或滞留物理执行记录。

- [ ] **Step 3: 报告验收边界**

报告候选 digest、四表门禁结果、Phase 10 一次性 cutover/absence 证据（首次切换时）、部署顺序、CI 状态和未验证边界。READY 部署只证明门禁与部署路径可用，不等于设备动作、供应商一致性或现场业务验收。
