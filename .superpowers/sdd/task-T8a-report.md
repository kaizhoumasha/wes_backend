# T8a 实施报告：EFFECT 双账本最终状态合同

## 状态与边界

T8a 已完成。`RuntimeIntentLog`、`SystemOutbox` 与 `WorklineDispatchAttempt` 已直接收敛到设计稿第 5.4
节的最终状态集合；旧 `dispatch_status`、RuntimeIntentLog transport attempt/retry/last-error 字段、
`BLOCKED_RESOURCE` 和重复 `OutboxStatus` 已从 active model/code 删除。没有兼容 alias、旧状态 mapping、
旧数据搬迁或跨 schema FK。

本任务只冻结枚举、转移矩阵、reducer event schema 与双账本持久化边界；没有提前实现 T8b canonical
bytes、T8c typed sender、T8d reducer 生命周期或 T8e lease/fencing。

## 实现结果

- `RuntimeIntentStatus` 固定为 `PROPOSED/ACCEPTED/COMPLETED/REJECTED/TECHNICAL_FAILED/UNKNOWN/RECONCILING`；
  RuntimeIntentLog 只保留语义状态、不可变请求/快照与 outcome evidence。
- `SystemOutboxStatus` 固定为 `NEW/DISPATCHING/RETRY_WAIT/SENT/FAILED/UNKNOWN/CANCELLED`；资源等待和明确未发送
  的有界重试统一使用非终态 `RETRY_WAIT`，`UNKNOWN` 不进入自动领取条件。
- `DispatchAttemptStatus` 增加终态 `UNKNOWN`，最终集合为
  `DISPATCHING/SENT/FAILED/UNKNOWN/CANCELLED`。
- 新增封闭 reducer event 枚举、严格 frozen event schema 以及三张只读转移矩阵；终态无 outgoing edge，
  reconciliation resolution 仅允许 `COMPLETED/REJECTED`。
- RuntimeIntentLog 与 SystemOutbox 两侧各自对 `dispatch_key` 建唯一约束；repository 提供同一调用方事务中
  一次加入一条 intent + 一条 outbox 并仅 `flush` 的 1:1 基础操作，不提交事务，也不猜测关联。
- runtime snapshot 改为投影 `dispatch_key/effect_status`，删除旧 dispatch 状态投影；既有 claim/outcome 路径
  直接写 final semantic enum。
- Alembic generator 生成 revision `8fb4b595a85c`：直接删除旧列、重建 final check constraints/partial index、
  新增 dispatch key 唯一索引；upgrade 无 `UPDATE`/`INSERT` 和状态转换。

## TDD 记录

1. RED：final contract 模块不存在，6 个 enum/matrix/event/1:1 合同测试失败；实现最小合同后 6 个 GREEN。
2. RED：既有 outbox、snapshot、intent repository 消费者仍引用旧状态，定向回归 6 failed / 17 passed；
   硬切换消费者并删除旧 dispatch 状态测试后 27 passed。
3. RED：schema-only migration contract 找不到目标 revision；用 Alembic generator 创建并实现 revision 后 GREEN。
4. 扩大回归发现旧 effect ledger 合同仍要求 nullable `effect_status`；更新为 non-null final enum、默认
   `PROPOSED` 与 dispatch key unique 合同后 GREEN。

## GitNexus

- 变更前所有被修改函数、类和方法均完成 upstream impact analysis。
- `RuntimeIntentLog`、`SystemOutbox` 及 `_load_blocked_outbox_projection` 为 HIGH；其中 query projection 只替换
  enum token，不改变查询形状。其余 repository/service/test 符号为 LOW/MEDIUM。
- 提交前以当前 worktree 绝对路径执行 staged detect changes 报告 HIGH：41 个任务文件、191 个变更符号、
  15 条受影响流程，集中于 outbox dispatch/资源等待与
  runtime blocked projection；相关 workline/sys/contracts 扩大回归和 quality gate 已覆盖。工作树原有未暂存
  `AGENTS.md/CLAUDE.md` 未进入检测 scope，也不会进入本提交。

## 验证

- final contract、schema、Outbox、RuntimeIntentLog、snapshot 与 idempotency 定向回归：`41 passed`。
- workline runtime、sys、system capability/workline contracts 与 test topology 扩大回归：最终 `917 passed`。
- 默认测试收集：`3594 tests collected`。
- Alembic：`heads` 为 `8fb4b595a85c`；`upgrade 8db8cbba582c:head --sql` 成功渲染 final check constraints、
  unique index 与 `RETRY_WAIT` partial index。
- `ruff format --check .`、`ruff check .`、`git diff --check` 通过。
- `./scripts/git-quality-gate.sh --profile quality` 通过：Bandit 0 issue、348 runtime contracts、11 process
  naming、import-linter、enforced architecture guardrails 与 test topology 全部通过。

## Concern / Blocker

- 本机 `localhost:5432/5433` 均无可用 PostgreSQL，因此未执行真实数据库 upgrade/downgrade；已完成 Alembic
  PostgreSQL offline SQL 渲染和 schema contract 测试。集成环境需在干净数据库执行该 revision；若存在旧行，
  non-null `dispatch_key/effect_status` 或已删除状态会使 upgrade 失败，这是“未发布系统、不迁移旧数据”的预期硬门禁。
- T8a 仅提供同事务 1:1 repository primitive；具体 typed EFFECT handler 的创建接线属于后续 T8f/T8g。

## Review finding 修复补充（2026-07-22）

- 将 final transition matrix 接入 RuntimeIntentLog、SystemOutbox 与 WorklineDispatchAttempt 的生产写路径；非法跳转、
  同状态重写和终态覆盖现在都会在写入 evidence 或时间戳前拒绝。该接线没有引入 T8d reducer 生命周期。
- `RETRY_WAIT` 仅在 `DEVICE_COMMAND` 且 reason 为 `DEVICE_BUSY` 或 `DEVICE_STATUS_PRECHECK_WAIT`、
  `blocked_at` 非空且 `finished_at` 为空时被识别为受控资源等待。普通退避会清空 blocked metadata，rack、station、
  query、diagnosis 与 migration partial index 使用同一谓词，已删除 `finished_at` 例外。
- SYSTEM_CAPABILITY intent 不再由通用 attempt ledger 提前落单；production device-command 路径携带已 claim 的
  RuntimeIntentLog，并通过 `add_proposed_pair` 在调用方事务内同时加入 command、intent 与 outbox。两侧要求相同且显式的
  `dispatch_key`，不再从 idempotency key 推断，也没有新增 sender。
- `dispatch_key` 已在 RuntimeIntent schema 设为必填，并在 RuntimeIntentLog claim 与 SystemOutbox update 两处执行不可变
  校验；更新 schema 也不再暴露该字段。

### 补充验证

- `uv run pytest tests/workline_runtime tests/sys tests/rack tests/contracts/system_capabilities tests/unit/runtime/orchestration tests/runtime/orchestration -q`：
  `1415 passed`。
- test topology：`6 passed`；默认收集：`3604 tests collected`。
- `./scripts/git-quality-gate.sh --profile quality` 再次通过；`git diff --check` 和新增 forbidden-write 搜索通过。
- 三份受影响的 PostgreSQL integration 文件可正常收集，共 `10 tests collected`。已显式尝试执行全部 10 个用例，
  但环境未配置 `INTEGRATION_DATABASE_URL`，均在 `missing_url` 预检处停止，未进入业务断言。

### 更新后的边界说明

- production device-command EFFECT 的双账本 1:1 接线已属于本次 review 修复，不再留待后续任务；typed sender、
  reducer lifecycle、lease/fencing 仍保持在 T8a 范围之外。

## P1 异步终态修复（2026-07-22）

- `OUTBOX_ASYNC` handler 的同步返回现在只表示 `RuntimeIntentLog + SystemOutbox(NEW)` 已同事务 durable accepted；
  不再调用 `record_outcome`，因此不会把入队成功写为 `COMPLETED`，也不会把当次可重试错误写为
  `TECHNICAL_FAILED`。RuntimeIntentLog 保持 `PROPOSED`，仅后续 transport/callback/reconciliation evidence 可推进语义终态。
- `SystemCapabilityEffectResult.evidence` 在 `OUTBOX_ASYNC` 路径保持为空，避免把同步 Success/RetryableFailure 当成可持久化
  完成 evidence；`LOCAL_TRANSACTIONAL` 维持原有 outcome 写入行为。
- `outbox.py` 的旧 `BLOCKED_RESOURCE` 注释已改为“受控资源等待投影”，与当前无该状态枚举的合同一致。

### P1 TDD / 验证

- RED：新增 OUTBOX_ASYNC 成功与可重试失败两例，均断言无 outcome 写入、无 result evidence、intent 仍为 `PROPOSED`；
  修复前两例均因无条件 `record_outcome` 失败。
- GREEN：system capability effect service `48 passed`；其关联 RuntimeIntentLog/effect-state/outbox repository 回归 `16 passed`。
- 扩大相关域回归 `1416 passed`；`./scripts/git-quality-gate.sh --profile quality` 通过。
