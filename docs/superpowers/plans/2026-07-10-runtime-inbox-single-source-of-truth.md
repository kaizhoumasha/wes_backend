# RuntimeInbox 主链路收敛实施计划

**目标：** 让 `RuntimeInbox` 成为 callback、内部事件、超时、重放进入 runtime orchestration 的唯一持久化事实源；删除 `WorklineInbox` 表、旧 repository/service、`InboxBatchProcessor`、`RuntimeInboxConsumer` facade 及全部运行时引用。

**交付策略：** 单 PR 全量切换。系统尚未发布，不保留旧 import、旧 task 名、兼容 shim 或双写路径；但必须保持 Alembic 历史不可变，并用 forward revisions 完成破坏性迁移。

**基础分支：** `develop`
**实施分支：** `feature/runtime-inbox-single-source-of-truth`

## 全局约束

- 分层保持 API → Service → Repository → Database。
- 所有项目命令使用 `uv run ...`。
- 修改函数、类、方法前运行 GitNexus upstream impact；HIGH/CRITICAL 必须先报告。
- Commit 前运行 GitNexus detect changes，并显式 stage 当前任务文件，禁止 `git add -A`。
- 数据库时间使用 `src/utils/timezone.py:timezone.now_for_db()` 的 naive UTC；API 输出再转 aware UTC ISO。
- `status` 与 `kind` 使用 VARCHAR + 命名 CHECK constraint。
- 每个任务遵循 TDD：失败测试 → 最小实现 → 回归/重构 → 独立 commit。
- 新增或迁移测试遵守 `tests/README.md`；重测试必须显式运行。
- 计划只描述合同、边界、验收和验证，不包含完整类、函数或测试实现。

## 已锁定架构

### 1. 单一事实源边界

`RuntimeInbox` 统一承载以下 ingress kind：

- `COMMAND_RESULT`
- `DEVICE_EVENT`
- `EXTERNAL_HTTP`
- `INTERNAL_EVENT`
- `TIMER_TIMEOUT`
- `REPLAY_REQUEST`

callback、SMT handoff、RuntimeHold 恢复、runtime intent effects、timeout scanner 和人工重放都通过同一 RuntimeInbox service 写入，不允许业务层直接创建旧 inbox 或绕过持久化队列。

### 2. Canonical envelope

RuntimeInbox 保存可重放的 canonical 业务 payload，而不是只保存 hash。合同至少包括：

| 类别 | 字段 |
|---|---|
| 身份 | `id`、`kind`、`provider_code`、`event_type`、`source_event_id` |
| 内容 | `payload_json`、`payload_hash`、`payload_schema_version` |
| 路由/证据 | `execution_session_id`、`workline_id`、`device_id`、`command_id`、`correlation_id`、`trace_id`、`event_id`、`causation_id` |
| claim | `claim_bucket_key`、`status`、`processor_token`、`lease_until` |
| 重试 | `attempt_count`、`max_retries`、`next_retry_at`、`last_error_code`、`last_error_message` |
| 时间 | `received_at`、`processed_at`、`failed_at`、Mixin 的 `created_at/updated_at` |

显式列只服务路由、trace、evidence 和热查询；其余业务字段保留在 `payload_json`，禁止消费者随意从 JSON 猜测可索引关系。

Canonical JSON 默认最大 1 MiB，并允许配置：

- HTTP 超限返回 413 且不落库。
- 内部 producer 超限抛领域校验错误且不创建消息。
- canonical payload 不包含认证 header。
- 处理 payload 完整保存；日志、diagnostic、审计快照单独脱敏和限长。

切换前已存在、没有 payload 的 RuntimeInbox 不做 WorklineInbox 回填：保留为 audit-only，迁移时从可 claim 集合移除并标记为不可重放；新写入必须包含 canonical payload。

### 3. 状态机与 token fencing

```text
                         claim(new token)
       +----------+  ---------------------->  +------------+
       | RECEIVED |                           | PROCESSING |
       +----------+                           +------------+
                                                    |
                         +--------------------------+-----------------------+
                         |                          |                       |
                         v                          v                       v
                  +-----------+              +-----------+          +-------------+
                  | PROCESSED |              |  FAILED   |          | DEAD_LETTER |
                  +-----------+              +-----------+          +-------------+
                                                   |
                                next_retry_at due  | claim(new token)
                                                   +--------------------> PROCESSING

PROCESSING + expired lease --claim(new token)--> PROCESSING
RESOURCE_WAIT --> FAILED + next_retry_at，且不增加 attempt_count
DEAD_LETTER replay --> 新建 RECEIVED 记录；原记录保持终态
```

原子 claim 同时匹配：

- `RECEIVED`
- `FAILED AND next_retry_at <= now AND attempt_count < max_retries`
- `PROCESSING AND lease_until <= now`

claim 必须更新 `processor_token`、`lease_until` 和适用的 attempt 计数。终态写回必须匹配 `id + status=PROCESSING + processor_token`；旧 token 返回 false、记录 fencing reject 指标，不覆盖新 owner。

### 4. 同桶 FIFO 与并发

- 入站时生成稳定 `claim_bucket_key`，优先 session、device、correlation、业务身份，无法解析时使用可审计 fallback。
- claim 复用现有 WorklineInbox 队首 anti-join：同一 bucket 只允许最早未终结消息进入 PROCESSING。
- 单 processor 使用 claim-one/process-one 循环；`limit` 只表示本轮最大处理量。
- 横向吞吐由多个 worker、`FOR UPDATE SKIP LOCKED` 和不同 bucket 并行提供。
- 不在单 Celery task 内批量占有 10 个 lease，不引入 heartbeat 或任务内并发。

### 5. 三阶段 processor

当前 PR 直接把旧 processor 拆为三个清晰阶段；拆分前先用 characterization tests 锁定现有行为，拆分后同一 case table 做 parity 验证。

| 阶段 | 职责 |
|---|---|
| Validation | canonical payload 校验、关联实体解析、SCAN_COMPLETED 校验、安全准入、重复入口/迟到结果判定，输出结构化 decision |
| Orchestration | ESTOP、TIMER_TIMEOUT 等专用路由；分布式锁；调用 `OrchestratorService.process_inbox`；不直接写终态 |
| Write-back | stale session snapshot guard、业务 effects、timeline/diagnostic、RESOURCE_WAIT、outbox enqueue disposition、fenced terminal update |

`RuntimeInboxProcessorService` 负责组合 validation 与 write-back，Celery task 只管理 DB session、claim-one 循环、单条 timeout、批次统计和 task retry。

结构化 outcome 复用现有 `WriteBackDisposition` 语义，并显式携带：

- disposition
- error code / message
- next retry time
- 是否消耗 attempt
- 是否需要 outbox dispatch

数据库仍保持五态，不新增 RESOURCE_WAIT 状态。

### 6. 分层与模块归属

```text
callback/internal producer
        |
        v
consumers/adapters
        |
        v
services/runtime_inbox/
  ├── RuntimeInboxService
  ├── RuntimeInboxValidationService
  ├── RuntimeInboxProcessorService
  └── RuntimeInboxWriteBackService
        |
        v
repositories/RuntimeInboxRepository
        |
        v
wes_runtime.runtime_inbox
```

- 只保留一个 `RuntimeInboxRepository`，统一 ACK/idempotency、claim、fencing、终态和查询。
- repository 位于 `orchestration/repositories/runtime_inbox_repository.py`。
- service 位于 `orchestration/services/runtime_inbox/`，并从目录 `__init__.py` 和上级 service export 导出。
- callback writer 等仅负责协议适配，留在 consumers。
- 不保留原 consumers repository/service import shim。

## 数据库与迁移策略

使用 Alembic generator 产生两个顺序 forward revisions，禁止修改任何历史 revision。

### Revision A：扩展 RuntimeInbox

- 增加 canonical payload、kind、路由/证据、processor token、naive UTC lease/retry/terminal 时间字段。
- 统一字段词汇为 `status + processor_token + lease_until`。
- 增加 `status` 与 `kind` 的命名 CHECK constraints。
- 处理 pre-cutover audit-only 行，使其不进入 claim/replay。
- 增加 hot-claim indexes：
  - RECEIVED FIFO
  - FAILED + `next_retry_at`
  - PROCESSING + `lease_until`
  - `claim_bucket_key + received_at + id` 队首查找
- 保留现有 source identity 唯一约束。

### Revision B：迁移引用并退役旧表

- 以消费者迁移矩阵为依据，迁移或解除所有指向 `wes_biz.workline_inbox.id` 的外键。
- 覆盖 RuntimeHold、SMT handoff 及 migration history 中识别出的其他引用。
- active code、测试 fixture 和数据库 FK 均为零引用后，drop `wes_biz.workline_inbox`。
- downgrade 至少能重建空旧表结构；再次 upgrade 必须成功。
- fresh database 和从当前 head 升级必须得到同一最终 schema。

## What already exists

| 现有能力 | 本计划复用方式 |
|---|---|
| `RuntimeInbox` 模型与 5 态目标合同 | 扩展字段和 DB constraints，不新建第三张 inbox 表 |
| `RuntimeInboxService.accept_received` | 保留幂等、同 hash ACK、异 hash conflict、人工 replay 语义 |
| `CallbackRuntimeInboxWriter` | 扩展为持久化 canonical payload，并保持三类 callback identity 规则 |
| `WorklineInboxRepository.claim_pending_messages` | 迁移 SKIP LOCKED、到期重试、stale reclaim、同桶 FIFO 和 token fencing |
| `InboxBatchProcessor` | characterization 来源；按三阶段拆分并做 parity，不直接丢弃业务分支 |
| `OrchestratorService`、distributed lock、write-back service | 保留编排、锁和 effect 语义 |
| `WriteBackDisposition` | 复用 PROCESSED/RESOURCE_RETRY 等结构化 outcome |
| callback enqueue + Beat fallback | 替换为 RuntimeInbox task，保持 commit-first 与兜底轮询 |
| RuntimeInbox acceptance/idempotency tests | 迁移 payload 断言并继续作为快速回归 |

## WorklineInbox 消费者迁移矩阵

实施前生成并维护一张至少覆盖 GitNexus 16 个直接依赖的矩阵。每行必须包含：当前文件/符号、使用字段、目标 RuntimeInbox 字段或查询、现有/新增测试、迁移状态。

必须覆盖：

- orchestrator bridge
- session resolver
- trace query
- runtime reconciliation
- RuntimeHold create/release
- SMT inbound handoff repository/service
- outbox repository/engine
- runtime status projection/counts
- workline unit of work
- callback ingress/orchestration
- integration fixtures/factories
- models/repositories/services exports
- capability port registry（删除 `RuntimeInboxConsumer` port 抽象与注册入口）
- inbound normalizer registry（删除 consumer 隔离上下文相关 wiring）

只有矩阵全部完成、行为测试通过、GitNexus 与 active-code guardrail 均为零旧引用时，才执行 Revision B 和文件删除。

## 实施任务

### Task 1：Characterization 与契约冻结

**状态：** ✅ 100% 完成（feature/runtime-inbox-single-source-of-truth 9c398350 + e33a83fa + 303d5ea4）

**目标：** 在改生产逻辑前锁定旧 processor 分支、消费者字段和目标 RuntimeInbox envelope。

**范围：**

- 为 SCAN validation、ESTOP、TIMER_TIMEOUT、missing context、duplicate entry、late command result、stale session、normal write-back、RESOURCE_WAIT、失败/死信建立 characterization case table。
- 建立 16 行消费者迁移矩阵。
- 更新测试专用状态机规格，使其与原子 `FAILED/stale PROCESSING → PROCESSING` claim 一致；保留其规格属性，但不把它当生产证据。

**验收：**

- characterization tests 在旧 processor 上通过。
- 每个直接消费者都有明确替代合同和测试归属。
- GitNexus impact 结果保存到 PR 说明。

**落地：** 迁移期 characterization case 已收敛到现存的 `tests/runtime/orchestration/test_runtime_inbox_processor_parity.py` 与 `tests/characterization/workline_legacy/test_business_semantics_characterization.py`；旧 processor 专用测试随 Task 7 一并删除。消费者迁移矩阵保留在本计划，GitNexus 基线影响为 32 个上游依赖、MEDIUM 风险。

### Task 2：RuntimeInbox 模型与 Revision A

**状态：** ✅ 100% 完成（c7d2973c + 7ace41d7）

**目标：** 建立完整、可约束、可索引的 RuntimeInbox 数据合同。

**范围：**

- 扩展模型字段、naive UTC 时间、kind/status CHECK、payload 1 MiB 配置和 hot indexes。
- 用 Alembic generator 创建 Revision A。
- pre-cutover 无 payload 行标为 audit-only，不回填、不重放、不进入 claim。

**验收：**

- model/constraint tests 覆盖合法全集、非法值、默认值和时间类型。
- fresh/upgrade schema 均符合合同。
- `EXPLAIN` 可使用预期 hot indexes。

**落地：** `src/app/runtime/orchestration/runtime_inbox.py` 加 14 字段（kind / payload_json / payload_schema_version / workline_id / device_id / command_id / trace_id / event_id / causation_id / claim_bucket_key / processor_token / received_at / processed_at / failed_at）+ 4 hot-claim partial index；`migrations/versions/20260711_1815_b8a28e1bfec8_extend_runtime_inbox.py` Revision A 迁移；`tests/runtime/orchestration/test_runtime_inbox_schema_contract.py` 覆盖模型合同。后续 `tests/integration/test_runtime_inbox_migration_postgresql.py` 已在隔离 PostgreSQL 验证 fresh、parent→A、audit-only、命名约束、毫秒时间和往返升级，不再保留“真实数据库未验证”的警告。

### Task 3：统一 Repository 与 RuntimeInboxService

**状态：** ✅ 100% 完成（e0035efa + 1e27c464）

**目标：** 单一 persistence owner 闭合 ACK、claim、fencing、retry、terminal state。

**范围：**

- 移动并合并为一个 RuntimeInboxRepository。
- 实现 claim-one 原子 SQL、同桶队首 anti-join、旧 token 拒绝。
- RuntimeInboxService 迁入 `services/runtime_inbox/`，统一命名并导出。
- 结构化 outcome 映射五态；RESOURCE_WAIT 不增加 attempt。

**验收：**

- 真实 PostgreSQL 双 session 测试覆盖并发 claim、FIFO、lease reclaim 和 fencing。
- 生产 service/repository 状态机覆盖全部合法/非法路径。
- 不再存在第二个 RuntimeInbox repository。

**落地：** `src/app/runtime/orchestration/repositories/runtime_inbox_repository.py` 是唯一持久化 owner，`src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py` 是 producer、claim、fencing、retry、replay 与终态写入的正式服务路径；旧 repository 已在 Task 7 物理删除。五态状态机由 focused service 测试与真实 PostgreSQL claim/concurrency 测试共同覆盖。

### Task 4：迁移所有 Producer

**状态：** ✅ 100% 完成（补充提交：95ed6a58 + 2e2082f2 + e7d5a04a + 0e57ed66 + 3467fb37 + 1e2f7b0f + d87e1ad8 + 77589f1c + 7722ec04）。callback result/event/external、timeout scanner、runtime intent、SMT handoff 与 RuntimeHold release 均只写 RuntimeInbox；active producer 已无 WorklineInbox create 调用。

**目标：** 所有 orchestration ingress 只写 RuntimeInbox。

**范围：**

- callback result/event/external 删除 WorklineInbox 双写并保存 canonical payload。
- command result、internal event、timer timeout、replay、SMT handoff、RuntimeHold 恢复和 runtime intent effects 改用 RuntimeInboxService。
- 每种 kind 固化必填字段、idempotency identity、bucket key 和错误响应。
- API commit 成功后 enqueue；enqueue 失败由 Beat 处理已提交消息。

**验收：**

- API facade tests 覆盖 413、same-hash ACK、different-hash conflict 和 enqueue fallback。
- producer contract tests 覆盖全部 kind。
- callback 路径无 WorklineInbox 写入。

**落地：** 三类 callback 一次性写入完整 canonical envelope，external duplicate 在 lifecycle/enqueue 前早返，新消息按“持久化与 lifecycle 同事务提交 → enqueue”执行；canonical UTF-8 JSON 默认限制 1 MiB，超限在幂等查询前返回 413、零新增记录，审计仅保存大小与身份摘要。timeout scanner 使用 RuntimeInbox，并隔离 execution session、legacy hold 与 source identity 命名空间。lifecycle-only external 由生产 processor 规范化为空 intents 后 fenced `PROCESSED`，不重复 ingress lifecycle。主线程关键回归 127/127 通过。

### Task 5：三阶段 Processor 拆分与 parity

**状态：** ✅ 100% 完成（补充提交：1b24dad4 + a26d29c8 + 7b13a364 + 90c68678 + e3fa67b6 + 7af7c283 + 439a4d79）。validation、orchestration、write-back 三阶段 service 与 RuntimeInbox bridge 已覆盖 characterization parity、重复/迟到、stale snapshot、RESOURCE_WAIT、失败/死信和 ESTOP 特例；旧 processor 已在 Task 7 物理删除。

**目标：** 在当前 PR 完成 validation、orchestration、write-back 边界拆分且保持行为等价。

**范围：**

- 实现三个阶段 service 与组合服务。
- 迁移旧 processor 的全部校验、安全、对账、重复/迟到处理、锁、stale snapshot、write-back、diagnostic/timeline 和 resource wait。
- 同一 characterization case table 同时验证旧实现与新实现，随后删除旧 processor 测试入口。

**验收：**

- parity case 全部一致。
- 每阶段有成功、失败、边界单元测试。
- Celery/API 不直接访问 repository 或数据库查询。

**落地：** RuntimeInbox Celery 链路调用生产三阶段 processor；迁移期 parity case table 验证旧、新入口后，旧类型、旧 bridge 与旧 processor 已在 Task 7 完成物理删除。ESTOP 安全副作用保持 fail-safe 独立提交，terminal write 仍受 token fencing；普通路径保持单条事务围栏。

### Task 6：Celery Task、Gateway 与调度

**状态：** ✅ 100% 完成（补充提交：ecf38b00 + 7c1f95e5 + c128bd50 + 1d6bf0e3；Task 7 删除提交 e56ad2eb 至 906790b1）。RuntimeInbox task、gateway、Beat 与即时 enqueue caller 已切换到新任务名；claim-one/process-one、timeout、retry、空批次、过期 lease 恢复和终态状态机均已验收。旧 task、Beat 与 gateway 表面已物理删除，`tests/architecture/test_workline_inbox_retirement_guardrail.py` 锁定 active code/test 零引用。

**目标：** 用 RuntimeInbox task 替换旧 workline inbox task，不保留兼容名。

**范围：**

- 新 task 使用 claim-one/process-one 循环、单条 timeout、批次上限和 task retry。
- gateway、Beat schedule、task routes 和所有 enqueue caller 使用新 task 名。
- 保留 Beat 10 秒兜底语义；即时 enqueue 失败不回滚已提交 inbox。
- 记录批次与最小 SLI。

**验收：**

- task 注册、路由、即时 enqueue、Beat fallback、空批次和 task retry 均有测试。
- 慢消息不会使尚未开始的消息 lease 过期。
- 旧 task 名在 active code 中为零。

**落地：** task 在 claim commit 后调用生产 processor，失败按 retryable/exhausted/RESOURCE_WAIT 分流，stale recovery 使用原子领取并将耗尽记录置为 DEAD_LETTER；Beat 保持 10 秒兜底，即时 enqueue 失败不回滚已提交消息。task 注册、路由、批次、timeout、retry 和 gateway 合同均通过独立测试。

### Task 7：迁移 Consumers 与 Revision B

**状态：** ✅ 100% 完成（补充提交：e56ad2eb + e1f19c17 + 34c4fed1 + 881e4e54 + c3fd9c08 + 3b89125c + fe6c307 + df286887 + 583e9b93 + b07a96ab + 05b6ca23 + 906790b1）。query/reconciliation/trace/UoW/outbox 已迁至唯一 RuntimeInbox repository 与 typed projection；旧 model/repository/service/facade/processor/fixtures 已物理删除；Revision B `ec426c628516` 已重绑三个 FK、增加 WorklineSession 显式列并退役 `wes_biz.workline_inbox`。

**目标：** 完成读路径、evidence、FK 和 fixture 迁移后物理删除 WorklineInbox。

**范围：**

- 按 16 行矩阵迁移 trace、reconciliation、hold、SMT、outbox、status、UoW 等消费者。
- 迁移有价值的旧测试断言，禁止为通过门禁而删除业务覆盖。
- 生成 Revision B，处理 `wes_biz.workline_inbox` 外键并 drop 表。
- 删除旧 model/repository/service/processor/consumer facade 及 exports。
- 增加 active production code 零引用 guardrail。

**验收：**

- 矩阵 100% 完成。
- GitNexus detect changes 与零引用 guardrail 证明旧运行入口消失。
- migration round-trip 与相关 heavy tests 通过。

**落地：** RuntimeInbox 显式区分 `workline_session_id` 与 `execution_session_id`，FIFO bucket、diagnostic 和 trace 均不跨命名空间回退；三个旧 FK 的不可映射值在迁移中安全清空后改指 RuntimeInbox。Revision B 在隔离 PostgreSQL 临时库完成 A→B→A→B 回环，downgrade 恢复旧表 24 列、8 constraints、19 indexes；可重复 heavy integration 已纳入测试，临时库全部清理，共享 dev 保持 `f0851c5bcfdb`。

### Task 8：系统级测试、性能与韧性

**状态：** ✅ 100% 完成（fd9e6b5a + 77bdd093 + 5c15a00 + 97af9ed + 9a7087d，含后续审查修复）。真实 PostgreSQL 已覆盖完整生产处理链路、两个 crash window、Revision A/B 升降级回环，以及 1000 条混合 backlog / 4 worker 并发 benchmark；成功前任务队列唤醒为 0，最终成功后恰好为 1，heavy test 不接触真实 broker。

**目标：** 证明目标链路在真实数据库、并发、崩溃和 backlog 下可运行。

**范围：**

- integration：callback → RuntimeInbox → claim → processor → session/timeline/outbox → terminal state。
- resilience：claim 后崩溃；write-back 后、terminal update 前崩溃。
- migration：fresh/current-head/downgrade/upgrade。
- benchmark：真实 PostgreSQL，至少 1000 条混合 backlog、4 worker，记录 p50/p95、吞吐、锁等待、重复 claim 和 query plan。
- SLI：各状态数量、最老 claimable age、claim/processing duration、lease reclaim、fencing reject、RESOURCE_WAIT、dead-letter。

**验收：**

- 两个 crash window 均幂等收敛，不产生重复设备命令/outbox。
- 真实 benchmark 基线通过评审后锁定阈值。
- 无 silent critical gap。

**落地：** 两个 crash window 均通过 lease reclaim、新 token fencing 和事务回滚幂等收敛，最终只生成一条目标 timeline、设备命令与 outbox；旧 owner 无法写终态。RuntimeInbox 毫秒时间字段从最初建表 revision 起即为 BIGINT，A→parent→A 使用真实毫秒值验证无窄化和数据损坏。所有 heavy test 强制要求显式 `INTEGRATION_DATABASE_URL`，只操作安全前缀临时库。最终 benchmark 使用 700 RECEIVED、200 到期 FAILED、100 stale PROCESSING，100 个 FIFO bucket；40 次真实 claim 调用的 p50 31.739ms、p95 76.214ms、吞吐 1593.023 条/秒，重复 claim 与等待锁均为 0，门禁锁定为 p95 ≤150ms、吞吐 ≥1000 条/秒。SLI 已覆盖五态数量、实际 FIFO 可推进的最老 claimable age、claim/processing duration、lease reclaim、fencing reject、RESOURCE_WAIT 与 dead-letter；批次 claim/reclaim 指标在 commit 后发射，不阻塞 repository 热路径。独立非 5432 TimescaleDB 验证完成后均已清理。

### Task 9：文档、索引与文档门禁

**状态：** ✅ T9 文档同步完成；T10 最终验收待执行（3df84112 + 本轮文档收束提交）。当前业务、架构、运行手册、所有权地图、可观测性合同、文件索引和 TODO 已同步到 RuntimeInbox 单一真源；本任务只记录文档一致性、legacy scanner、链接/路径与质量门禁证据，不把历史全量数字或 T10 严格验收声明为本轮结果。

**目标：** 让当前架构文档、文件索引和运行说明只描述 RuntimeInbox 权威链路。

**范围：**

- 更新 current business/architecture/runtime 文档、`docs/architecture/file_index.md`、本计划和验收设计。
- 删除 active legacy 模块索引、双写描述和已完成 TODO。
- 保留尚未交付的运营 UI、告警与 runbook TODO，不在本任务实现运营界面。

**验收命令：**

```bash
uv run pytest tests/deployment/test_runtime_inbox_documentation_consistency.py tests/architecture/test_workline_inbox_retirement_guardrail.py -q
uv run python scripts/workline_inbox_retirement_guardrail.py --format text
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
./scripts/git-quality-gate.sh --profile quality
```

真实 PostgreSQL integration/resilience/migration/benchmark、全量回归与 evidence artifact 的最终复跑归 Task 10，不在 Task 9 提前标记完成。

Commit 前：

- 显式 stage 当前任务文件。
- 检查 `git status --short` 与 `git diff --cached --stat`。
- 运行 GitNexus detect changes。

## 测试覆盖图

```text
INGRESS / PRODUCERS                                  CLAIM / STATE
├── [DONE ★★★] result/event/external payload        ├── [DONE ★★★] RECEIVED
├── [DONE ★★★] same/different hash                  ├── [DONE ★★★] due FAILED
├── [DONE ★★★] 1 MiB boundary                       ├── [DONE ★★★] stale PROCESSING
├── [DONE ★★★] INTERNAL_EVENT                       ├── [DONE ★★★] active/future excluded
├── [DONE ★★★] TIMER_TIMEOUT                        ├── [DONE ★★★] same-bucket FIFO
└── [DONE ★★★] replay                               └── [DONE ★★★] fencing

PROCESSOR PARITY                                     SYSTEM FLOWS
├── [DONE ★★★] SCAN validation                      ├── [DONE →E2E] callback full pipeline
├── [DONE ★★★] ESTOP / TIMER                        ├── [DONE →E2E] retry recovery
├── [DONE ★★★] duplicate / late result              ├── [DONE →E2E] two crash windows
├── [DONE ★★★] stale session                        ├── [DONE] migration round-trip
├── [DONE ★★★] RESOURCE_WAIT                        └── [DONE] consumer parity rows
└── [DONE ★★★] success/failure/dead-letter

TARGET: 36/36 behavior paths covered
Legend: ★★★ behavior + edge + error | →E2E integration/resilience boundary
```

测试归属：

- `tests/api/`：callback route、response、permission/facade。
- `tests/workline_runtime/`：RuntimeInbox service、三阶段 processor 纯逻辑。
- `tests/contracts/`：kind、envelope、消费者迁移和零引用合同。
- `tests/deployment/`：Alembic schema contract。
- `tests/integration/test_runtime_inbox_*.py`：真实 DB/Celery pipeline、claim concurrency 与迁移回环。
- `tests/resilience/`：worker crash 与幂等恢复。
- `tests/load/`：真实 PostgreSQL benchmark，默认不收集。

## Failure modes

| 生产失败场景 | 测试 | 错误处理 | 用户/运维感知 |
|---|---|---|---|
| payload 超过 1 MiB | API/producer boundary | 拒绝且不落库 | HTTP 413 或明确领域错误 |
| DB commit 成功、即时 enqueue 失败 | integration | Beat 兜底 | ACK 正常；backlog age 可观测 |
| task 未注册或 worker 停止 | deployment/integration | Beat 重试仍失败但不丢消息 | backlog/oldest-age SLI 明确暴露 |
| 同桶消息并发/乱序 | 双 session integration | 队首 anti-join | 后续消息等待，不静默跳过 |
| 到期 FAILED 永久卡住 | state integration | 原子 due claim | retry/dead-letter 指标可见 |
| claim 后 worker 崩溃 | resilience | lease reclaim + 新 token | 自动恢复 |
| write-back 后、终态前崩溃 | resilience | effect/outbox 幂等 + fencing | 无重复物理命令 |
| 旧 token 写终态 | concurrency integration | update 返回 false | fencing reject 指标 |
| malformed/不可重试消息 | processor parity | diagnostic + DEAD_LETTER | trace/dead-letter 可见 |
| RESOURCE_WAIT | processor parity | FAILED 到期重试且不耗 attempt | resource-wait 指标 |
| migration FK 未解除 | migration round-trip | Revision B 阻止 drop | 部署失败而非部分成功 |
| pre-cutover 无 payload 行 | migration contract | audit-only、不可 claim/replay | 明确审计标记 |
| consumer 漏迁 | 16 行矩阵 + behavior tests | 零引用门禁阻止 drop | CI 阻断 |

本计划完成后没有“无测试、无错误处理且静默”的 critical failure mode。

## 性能与容量门禁

- claim query 必须命中 targeted partial/composite indexes。
- 单 worker claim-one；扩容优先增加 worker，不在任务内增加并发。
- benchmark 使用真实 PostgreSQL，不再接受 deque 指标作为生产证据。
- payload 最大 1 MiB；日志/diagnostic 有独立限长。
- 记录并评审：claim p50/p95、处理 p95、吞吐、锁等待、duplicate claim、oldest backlog age。
- 完整 dashboard、告警阈值和现场 Runbook 继续由现有 P2 TODO 承担。

## NOT in scope

- 不保留 WorklineInbox 只读表、旧 task 名、旧 import、deprecated shim 或双写兼容层。
- 不回填 pre-cutover RuntimeInbox payload；旧行只保留 audit-only。
- 不修改或 squash 历史 Alembic revisions。
- 不引入对象存储处理大 payload；超过 1 MiB 直接拒绝。
- 不建设完整运营 dashboard、告警 UI 或 Runbook。
- 不改 callback 对外成功/冲突响应合同，除新增明确的 413。
- 不做前端/UI 改动。
- outside voice 本次按用户选择跳过。

## TODOS.md 更新

本轮无新增 TODO：当前 PR 通过 Task 5（三阶段 Processor 拆分与 parity）覆盖了"稳定后拆分 processor"的潜在 TODO。现有"统一运营看板、告警与 Runbook"P2 项保持不变。

## WorklineInbox 消费者迁移矩阵（GitNexus impact 报告）

由 `mcp__gitnexus__impact(target="WorklineInboxClaim", direction="upstream")` 拉取，基线 `a18a8bd2`（plan 起点）：

**风险等级**：MEDIUM（32 个上游依赖 / 5 depth=1 / 13 depth=2 / 14 depth=3）

下表覆盖 14 个直接消费者（plan §WorklineInbox 消费者迁移矩阵要求"至少 16 个"，本表覆盖最关键的 14 个 + depth=3 提及 18 个）：

| # | 文件/符号 | 使用 WorklineInbox 字段 | RuntimeInbox 替代 |
|---|---|---|---|
| 1 | `src/app/runtime/orchestration/services/inbox/inbox_service.py:WorklineInboxService` | `create_*_inbox` / `mark_as_*` / `park_for_retry` / `claim_pending_messages` | 全部映射 RuntimeInbox（Task 3） |
| 2 | `src/app/runtime/orchestration/services/inbox/inbox_batch_processor.py:InboxBatchProcessor` | `_process_claimed_message` 全部分支 | 三阶段 RuntimeInboxProcessorService（Task 5） |
| 3 | `src/app/runtime/orchestration/services/intent/operation_service.py` | `runtime_intent_effects` 创建 inbox | 改用 `RuntimeInboxService`（Task 4） |
| 4 | `src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py` | `runtime_hold` 关联 inbox 查询 | 改用 `RuntimeInboxRepository`（Task 3） |
| 5 | `src/celery_app/tasks/workline.py:process_inbox_batch` | `WorklineInboxClaim` claim + 调 `InboxBatchProcessor` | 替换为 `process_runtime_inbox_batch`（Task 6） |
| 6 | `src/app/workline/unit_of_work.py` | WorklineInbox 状态查询 | 删除（runtime UoW 统一 runtime_inbox） |
| 7 | `src/app/workline/services/safety_service.py` | `assert_accepting_work` 读 inbox | 不变（不读 inbox） |
| 8 | `src/app/runtime/orchestration/runtime_intent_effects.py` | `create_device_event_inbox` | 改用 `RuntimeInboxService.accept_received`（Task 4） |
| 9 | `src/app/resource/services/projection_service.py` | inbox active 状态 | 改用 `RuntimeInboxRepository` 投影 |
| 10 | `src/app/resource/services/active_rack_snapshot_service.py` | inbox active 状态 | 改用 `RuntimeInboxRepository` |
| 11 | `src/app/runtime/orchestration/services/trace/trace_query_service.py` | `WorklineInbox` 类型注解 | 改 RuntimeInbox |
| 12 | `src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py` | `WorklineInboxClaim` 类型 | 改 RuntimeInbox |
| 13 | `src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py` | SMT handoff inbox | 改用 `RuntimeInboxService`（Task 4） |
| 14 | `src/app/workline/v1/operation.py` | `enqueue_workline_inbox` | 改用 `enqueue_runtime_inbox`（Task 6） |
| 15 | `src/app/callback/services/callback_orchestration_service.py` | 双写 `create_device_event_inbox` | 删双写（Task 5） |
| 16 | `src/app/callback/v1/callback.py` | `_enqueue_workline_processing` | 改 `_enqueue_runtime_inbox_processing`（Task 5） |
| 17 | `src/app/runtime/orchestration/consumers/runtime_inbox_consumer.py` | `consume_sync` 委托 `process_inbox_payload` | 删 facade（Task 7） |
| 18 | `src/app/runtime/capability_port_registry.py` | `RuntimeInboxConsumer` port 注册 | 迁移矩阵必覆盖（Task 7） |
| 19 | `src/app/runtime/inbound_normalizer_registry.py` | `RuntimeInboxConsumer` normalizer 上下文 | 迁移矩阵必覆盖（Task 7） |
| 20 | `src/app/runtime/orchestration/services/query/runtime_query_service.py` | WorklineInbox 查询 | 改 RuntimeInbox |
| 21 | `src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py` | WorklineInbox 引用 | 删除依赖 |
| 22 | `src/app/sys/repositories/outbox_repository.py` | 间接依赖 | 不变 |
| 23 | `src/app/workline/repositories/workline_repository.py` | 间接依赖 | 不变 |
| 24 | `src/app/workline/services/__init__.py` | re-export inbox_service | 删除 re-export |
| 25 | `src/app/runtime/orchestration/models/__init__.py` | re-export WorklineInbox | 删除 re-export |
| 26 | `src/app/runtime/orchestration/repositories/__init__.py` | re-export WorklineInboxRepository | 删除 re-export |
| 27 | `src/app/runtime/orchestration/services/inbox/__init__.py` | re-export inbox_service | 删除 re-export |
| 28 | `src/app/workline/services/safety_service.py` | 间接 | 不变 |

迁移状态（同步于 2026-07-11，HEAD `906790b1`）：

- 已完成写路径切换：8（runtime intent effects）、13 的 RuntimeInbox write/read evidence 主路径（SMT inbound handoff）、RuntimeHold release 的 command result producer。
- 1-28 全部完成：生产写入、claim/fencing、query/trace/reconciliation/UoW/outbox、callback/timeout/SMT/RuntimeHold、exports 与测试 fixture 均已迁移。
- 旧 model/repository/service/processor/facade/task API 已物理删除，active source/tests 由零引用 guardrail 锁定。
- Revision B `ec426c628516` 已生成并通过 PostgreSQL upgrade/downgrade/upgrade；旧表及其数据库 FK 已退役。

只有当 1-28 全部迁移完成、characterization case table 全部 parity 通过、`grep -rn "WorklineInbox" src/ tests/` 仅保留在 `runtime_inbox` 抽象层引用时，才执行 Revision B drop `wes_biz.workline_inbox`。

## 并行化策略

Sequential implementation, no parallelization opportunity.

原因：

- Revision A → repository/service → producer/processor → consumer/FK → Revision B 形成严格依赖。
- 主要任务共同修改 `src/app/runtime/orchestration/`、共享 exports、fixtures 和 migration head。
- 在多个 worktree 并行会制造高冲突和不一致中间态；本计划应按 Task 1→9 顺序执行。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 2 | CLEAR | 本周评审周期共接受 3 项 scope proposal，0 unresolved |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | SKIPPED | 用户选择跳过 outside voice |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 13 | CLEAR | 本轮 46 issues/gaps，0 critical gaps，全部折入计划 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | backend-only，无 UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run |

**VERDICT:** CEO + ENG CLEARED — 计划已锁定，可进入实现；实施仍须逐任务通过 GitNexus、测试、migration 与质量门禁。

NO UNRESOLVED DECISIONS
