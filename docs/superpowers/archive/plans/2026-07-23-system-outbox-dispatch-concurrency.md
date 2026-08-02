# SystemOutbox dispatch concurrency 实施计划

> **执行方式：** 本任务按用户授权在当前隔离 worktree 内直接执行，严格 RED → GREEN → REFACTOR；不拆分到其它 Agent。

**目标：** 为 `SystemOutbox` 落地 PostgreSQL `SKIP LOCKED`、owner lease fencing、Provider profile + operation
公平桶和有界背压，使过期 worker 永远不能提交 outbox 或 attempt 状态。

**架构：** `SystemOutbox` 持久化不可变调度 identity 与当前 lease；共享调度服务按桶策略轮转，Repository 在
PostgreSQL 事务级 bucket 锁内核算并发/速率额度，再用 `FOR UPDATE SKIP LOCKED` 原子领取。两个现有 dispatcher
只消费带权威 lease 的 claim，所有 worker 写回同时校验状态、owner token 与有效期；`WorklineDispatchAttempt`
镜像同一 lease，形成 outbox/attempt 双围栏。

**技术栈：** Python 3.13、SQLModel/SQLAlchemy async、PostgreSQL、Alembic、Pytest。

## 全局约束

- 不从 `payload_json`、snapshot 或 correlation 解析调度身份。
- 不改变 T8b canonical bytes、T8c typed transport、T8d reducer/case 语义。
- 不实现 T8f credential/target snapshot、T8g crash matrix或业务数据迁移。
- 所有生产命令使用 `uv run ...`；所有拟改函数、类、方法先运行 GitNexus upstream impact。
- 规划文档只描述接口、边界和验收，不粘贴完整实现。

---

### Task 1：冻结调度与 lease schema

**文件：**

- 修改 `src/app/sys/models/outbox.py`
- 修改 `src/app/runtime/orchestration/models/dispatch_attempt.py`
- 新增 Alembic generator revision
- 测试 `tests/sys/test_system_outbox_dispatch_concurrency_contract.py`

**接口与验收：**

- `SystemOutbox` 必填不可变 `provider_profile_identity`、`operation_identity`，并持久化 owner token、lease expiry。
- `WorklineDispatchAttempt` 复用 outbox owner token并冻结 lease expiry。
- 建立 bucket claim、active lease、attempt lease 索引及状态/lease 一致性约束。
- migration 只改 schema，无 `UPDATE`、`INSERT`、backfill 或兼容映射。

### Task 2：实现公平桶策略与 PostgreSQL 原子领取

**文件：**

- 新增 `src/app/sys/dispatch_concurrency.py`
- 修改 `src/app/sys/repositories/outbox_repository.py`
- 测试 `tests/integration/test_system_outbox_dispatch_concurrency.py`（核心 HEAVY，显式运行）
- 集成测试 `tests/integration/test_system_outbox_dispatch_concurrency_postgresql.py`

**接口与验收：**

- 冻结 bucket key/policy/claim/snapshot 类型；policy 覆盖 concurrency、rate window、batch、retry 与 pause。
- Repository 只按显式列列出活跃 bucket、读取 SLI，并在 bucket 事务锁内 `SKIP LOCKED` 领取。
- 每轮至少访问一次所有可用 bucket；受限或暂停桶不能消耗其它桶额度，未领取项保持 durable backlog。
- 过期非 HTTP lease 可由新 owner 领取；过期 EXTERNAL_HTTP 仍按 T8c 收口 UNKNOWN，绝不自动重发。

### Task 3：统一 outbox/attempt fencing 写回

**文件：**

- 修改 `src/app/sys/repositories/outbox_repository.py`
- 修改 `src/app/runtime/orchestration/services/inbox/dispatch_attempt_service.py`
- 修改 `src/app/sys/external_http_evidence.py`
- 测试上述 sys/runtime 定向文件及 PostgreSQL 集成文件

**接口与验收：**

- 所有 worker 终结、失败、UNKNOWN、资源等待写回均携带 owner token；不匹配或过期返回 fenced 结果。
- attempt 创建必须与当前 outbox lease 完全一致；旧 attempt 不能在 lease 被夺取后终结。
- UNKNOWN 隔离恢复也必须匹配原 owner；不能覆盖新 owner 的状态。

### Task 4：两个 dispatcher 接入同一 claim 核心

**文件：**

- 修改 `src/app/sys/services/outbox_engine.py`
- 修改 `src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py`
- 修改现有 dispatcher 测试

**接口与验收：**

- generic 与 workline dispatcher 均先取得有界 claim，再创建同 lease attempt并提交，之后才执行 I/O。
- transport/attempt/reducer evidence 仍在原事务边界写入；fenced worker只计 skipped/lease loss，不改状态。
- blocked-resource 路径同样取得 owner lease，不保留旁路。

### Task 5：生产写入口显式提供 bucket identity

**文件：**

- 修改 `DispatchEnvelope` 的所有生产构造方及直接 `SystemOutbox` 构造方
- 修改相应 contract/unit tests

**接口与验收：**

- Provider profile 与 operation identity 来自 typed contract/调用上下文，持久化后不可变。
- worker SQL 和生产代码不存在从 JSON/snapshot 提取或兜底生成 identity 的分支。

### Task 6：真实 PostgreSQL、质量门禁与提交

**验证：**

- Docker PostgreSQL 验证并发 `SKIP LOCKED`、lease steal/loss、旧 owner outbox/attempt fencing。
- 公平桶验证受限桶不饿死其它桶，并覆盖 concurrency/rate/batch/retry/pause 与 backlog/queue-age SLI。
- 验证 Alembic upgrade/downgrade/upgrade、定向/扩大回归、test topology、collect-only 和 quality profile。
- staged `gitnexus_detect_changes` 复核影响，更新 `.superpowers/sdd/task-T8e-report.md` 后中文 Conventional Commit。
