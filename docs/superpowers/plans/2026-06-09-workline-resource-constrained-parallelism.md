# WorkLine 资源约束并发 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 WorkLine runtime 从“同一工作线只能有一个 open session”改成由设备、Station、rack/bin/cell 等真实资源约束的并发模型。

**Architecture:** 删除工作线级 entry-admission blocker，只保留同 `workline_id + business_key` 查建锁。Inbox 仍通过数据库 token claim、物化且 `NOT NULL` 的 `claim_bucket_key` 和同 bucket 队首围栏保证 FIFO；编排阶段资源忙表达为 `RESOURCE_WAIT` intent，由 effect 层写 Session 等待态和幂等诊断，并沿 `RuntimeIntentEffectApplier.apply()` → `OrchestratorWriteBackService.write_back()` → `InboxBatchProcessor` 显式返回 `WriteBackDisposition`；最终由 `InboxBatchProcessor` 单一写入 Inbox `RETRY` 或 `PROCESSED`。设备 dispatch 忙保留在 Outbox `BLOCKED_RESOURCE` 围栏，blocked outbox 只能由 ECS 实时 `IDLE` probe 重新放行。本地设备状态只作为诊断投影。不引入通用 `resource_lease` 表，不保留旧兼容 alias。

**Document Boundary:** SPEC 只维护目标、决策、业务约束和验收标准；本文维护执行任务、失败模式、质量门禁和评审状态。不要在 SPEC 中复制本文的任务清单或 `GSTACK REVIEW REPORT`。

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async, Celery, pytest/pytest-asyncio, Ruff, GitNexus, uv。

**Implementation Status (2026-06-09):** 资源约束并发代码、迁移、用户文档和本地门禁已落地；`uv run ruff format/check <changed-python-files-only>` 通过，focused tests 为 364 passed / 1 skipped，全量 `uv run pytest tests/` 为 2011 passed / 9 skipped。本地未提供 PostgreSQL 集成环境时，`RUN_WORKLINE_INTEGRATION=1` / `INTEGRATION_DATABASE_URL` claim/EXPLAIN 门禁需由 CI 或集成环境补跑并留痕。

---

## 执行前要求

- 基础分支：`develop`。
- 推荐分支：`feature/workline-resource-constrained-parallelism`。
- 所有项目命令使用 `uv run ...`。
- 修改任何函数、类、方法前，按仓库规则运行 GitNexus impact analysis；HIGH/CRITICAL 风险先汇报。
- 已知影响面：`RuntimeIntent` 为 HIGH，`WorklineInbox` 为 CRITICAL，`InboxBatchProcessor` 为 MEDIUM；实现者必须把风险说明、focused tests 和最终 `detect_changes` 结果写入交付说明。
- 每个任务完成后做一次 focused test；最终任务跑全量相关测试和质量门禁。
- 当前系统未发布，按破坏性优化执行；不要保留旧 `parallelism`、entry-admission blocker、历史 alias 或兼容 facade。
- 实现阶段不要在 T1-T5 中间提交；所有代码、测试、文档和 TODO 更新完成并通过 GitNexus detect changes 后，在 T6 做一次最终中文 Conventional Commit。

## 已确认实现决策

- `claim_bucket_key` 使用应用层普通列物化；禁止用数据库生成列替代，禁止继续在 claim 热路径从 `payload_json` 推导；migration backfill 后必须设置 `NOT NULL`。
- `claim_bucket_key` 只允许在入库、`create_idempotent()` 或 claim 前纠偏时补齐/重算；一旦消息进入 `PROCESSING` 或写入 `processor_token`，该 key 冻结，后续 `RETRY/PROCESSED` 不得因字段变化重算。
- `WorklineInboxRepository` 是 `claim_bucket_key` 的最终持久化 guard；必须覆盖 `InboxService` 通用 create/timeout、`OperationService` replay/manual/sandbox event/sandbox external callback/sandbox command result、`RuntimeHoldReleaseService` continue-result 等直接写入形态。
- `WriteBackDisposition` 必须定义在 runtime 中立合同模块（例如 `src/workline_runtime/effect_result.py`），至少包含 `PROCESSED` 与 `RESOURCE_RETRY`；`RuntimeIntentEffectApplier.apply()`、`OrchestratorWriteBackService.write_back()` 和 `InboxBatchProcessor` 三层都不得通过异常、写后读状态或可变 `ctx` 标记表达正常 `RESOURCE_WAIT`。
- `RESOURCE_RETRY` 是正常资源等待统计：`InboxBatchProcessor` 计入 `processed += 1` 与 `resource_wait += 1`，不计 `success`、`failed` 或 `skipped`，不增加 `attempt_count`。
- `WorklineInbox` 新增非空 `claim_bucket_key` 后，测试必须通过统一 inbox builder/helper 生成默认 claim key；不得在大量测试 fixture 中手写第二套 bucket 规则。
- 同一 Inbox 从资源 A 恢复后又等待资源 B 时，必须先 resolve 资源 A 的 ACTIVE `RESOURCE_WAIT` 诊断，再记录资源 B 的等待诊断；ACTIVE 诊断只表达当前阻塞资源。
- PostgreSQL-backed 集成测试是 claim 并发语义的验收门槛；SQLite 只能覆盖普通分支逻辑。本地无 PostgreSQL 时可以 skip 并记录原因，但最终交付或 CI 必须跑 PostgreSQL claim 并发与 `EXPLAIN` 门禁，不能把 SQLite-only 结果视为通过。
- PostgreSQL-backed 门禁复用现有 `tests/integration/conftest.py` 接入方式：设置 `RUN_WORKLINE_INTEGRATION=1` 和 `INTEGRATION_DATABASE_URL` 后运行 claim/EXPLAIN 子集；不能新建一套并行 fixture 体系。
- 设备 dispatch 放行事实源是 ECS 实时 `IDLE` probe；本地 `DeviceStatus`、`current_command_id` 和 command terminal 状态只作为诊断/业务投影。
- 单个 `InboxBatchProcessor` 顺序 claim 和处理；多 worker 并发只由数据库 claim 围栏承载，不恢复 batch 内 `asyncio.gather`、Redis bucket lock 或 wave 调度。
- 核心实现顺序执行，不拆并行 worktree：runtime、Inbox、diagnostic、plugin 路径共享同一批合同，拆开会提高合并和语义漂移风险。

## 并发层级边界

```text
业务并发容量
  = 设备 / Station / rack / bin / cell / 外部任务状态
        |
        v
数据库消费围栏
  = WorklineInbox token claim + claim_bucket_key + 同 bucket 队首约束
        |
        v
单 processor 执行模型
  = 每轮只 claim 1 条，处理完成或终态失败后再 claim 下一条
```

实现时必须保持三层分离：业务容量不由环境变量表达，数据库围栏不替代设备/Station 资源判断，单个 processor 不做 batch 内并发。用户可观察并发来自多 open session、多 worker PostgreSQL claim、Outbox/device callback 和真实资源释放，而不是单个 batch 内的 `asyncio.gather`。旧 design doc 中的“有界分桶并发”只保留为历史背景，本计划明确删除 `parallelism` 参数和 Redis bucket lock。

设备与 Station 必须分层实现：设备级互斥继续由 device command / Outbox governance 管，Station lease 只管 Station scope 的外部派发和单层货架相关站位占用。不要把“设备是现场工位”理解成给每个设备套 Station lease，也不要把设备 busy 从 Outbox `BLOCKED_RESOURCE` 回退成 Inbox `RESOURCE_WAIT`。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `src/app/workline/constants.py` | 删除 Inbox 并发/bucket 锁常量；新增 `WORKLINE_RESOURCE_WAIT_RETRY_SECONDS`。 |
| `src/workline_runtime/runtime_intent.py` | 新增 `RuntimeIntentKind.RESOURCE_WAIT` 和 `RuntimeIntent.resource_wait(...)` 工厂；校验资源字段。 |
| `src/workline_runtime/effect_result.py` | 新增 runtime 中立 effect result / `WriteBackDisposition` 合同，供 effect、write-back 和 processor 共同引用。 |
| `src/workline_runtime/runtime_intent_effects.py` | 支持 RESOURCE_WAIT effect，写 Session 等待态和诊断证据，显式返回中立 effect result。 |
| `src/workline_runtime/resource_wait_evidence.py` | 新增唯一 ResourceWaitEvidence helper/model，统一 diagnostic key、session context 与 diagnostic evidence 合并规则。 |
| `src/app/workline/services/write_back_service.py` | 直接返回 runtime 中立 effect result；不在 service 层定义 disposition enum。 |
| `src/workline_runtime/session_resolver.py` | 删除工作线级 entry-admission blocker；保留 business_key 查建锁。 |
| `src/app/workline/repositories/session_repository.py` | 删除 `get_open_entry_blocker_for_workline()`。 |
| `src/app/workline/inbox_claim_bucket.py` | 新增纯 helper，统一生成应用层物化 `claim_bucket_key`。 |
| `src/app/workline/models/inbox.py`、`migrations/versions/` | 新增物化 `claim_bucket_key` 字段和热队列索引；同步清理重复 `DataTableMixin`；更新模型内联状态图，删除旧 batch 并发表述。 |
| `src/app/workline/repositories/inbox_repository.py` | 只保留 claim 消费入口；删除 `get_new_messages()` 和 pending entry-admission 查询；把 `claim_bucket_key` 作为 repository 持久化兜底不变量，并显式排序返回 claim。 |
| `src/app/workline/services/inbox_service.py` | 删除旧消费/entry-admission wrapper；不重复实现 claim key 逻辑；保留 `park_for_retry()` 并用于 RESOURCE_WAIT。 |
| `src/app/workline/services/inbox_batch_processor.py` | 删除 parallelism、Redis bucket lock、entry-admission retry 分支；按 claim 顺序处理；`ProcessResult` 增加 `resource_wait` 计数。 |
| `src/celery_app/tasks/workline.py` | 删除 `process_inbox_batch(..., parallelism=...)` 参数和历史 alias；返回值透出 `resource_wait` 计数。 |
| `src/app/workline/services/diagnostic_service.py` | 增加 RESOURCE_WAIT 幂等诊断写入/更新入口，只调用 ResourceWaitEvidence helper/model，不重复生成 key 或 evidence 字段。 |
| `src/workline_runtime/diagnostics/codes.py`、`registry.py`、`builder.py` | 增加 `RESOURCE_WAIT` 诊断码、默认严重度和恢复语义。 |
| `src/workline_plugins/smt_sorting_inbound/flow_service.py` | Station busy 从 `RuntimeIntent.block()` 改为 `RuntimeIntent.resource_wait()`。 |
| `tests/helpers/workline_inbox_factory.py` 或现有测试 helper | 新增统一 WorklineInbox 测试构造 helper，默认生成 `claim_bucket_key`。 |
| `docs/hardware/粗分机硬件供应商联调操作手册.md`、`docs/hardware/粗分机内部Mock与Sandbox调试手册.md`、`docs/workline_diagnostics_quickstart.md`、`docs/business/workline_runtime_workflow_guide.md` | 删除旧串行/parallelism 表述，补 RESOURCE_WAIT 观察说明。 |

## What already exists

- `OutboxDispatchService` 已有设备 `BLOCKED_RESOURCE`、ECS 实时 status probe、blocked 队首 claim 和资源等待观测更新；本计划复用该设备副作用围栏，不把设备 busy 改成 Inbox retry。
- `WorklineStationLeaseService` 已覆盖 Station scope 的外部派发互斥；本计划只把 Station busy 从 Runtime block 改成 `RESOURCE_WAIT`，不把设备统一套进 Station lease。
- Inbox 已有 `processor_token`、`PROCESSING`、`RETRY`、stale reclaim 和 `park_for_retry()`；本计划保留 token fencing，只把 claim bucket 从 JSON 热路径推导改成物化列。
- `WorklineInboxRepository.claim_pending_messages()` 已有 `SKIP LOCKED` claim 框架和同 bucket 队首围栏；本计划保留该消费入口，删除旧 `get_new_messages()` 消费路径。
- trace/runtime monitor 已能展示 Outbox 资源等待摘要；本计划补 Inbox `RESOURCE_WAIT` 证据，使 UI/trace 可统一展示但不混淆写入边界。

## NOT in scope

- 不新增通用 `resource_lease` 表；现阶段复用设备 Outbox、Station lease、rack/bin reservation。
- 不恢复 batch 内 `asyncio.gather`、Redis bucket lock、bucket wave 调度或 `WORKLINE_INBOX_BATCH_PARALLELISM`。
- 不新增 WES API，不做前端大改，不定义生产监控阈值；监控和 benchmark 已在 `TODOS.md` 中作为后续生产化事项跟踪。
- 不保留 `WorklineEntryAdmissionBlocked` 新路径、pending entry-admission debug 合同、Celery 历史 alias 或旧兼容 facade。
- 不把本地 `DeviceStatus`、`current_command_id` 或 command terminal 状态作为 blocked outbox 放行事实源。

## Failure modes

| Failure mode | Handling | User-visible state | Required test |
| --- | --- | --- | --- |
| `claim_bucket_key` 允许 NULL 或 helper/backfill 漂移 | migration 先 backfill + NULL 验证再设 `NOT NULL`；migration SQL/SQLAlchemy 与 Python helper 跑同一 case matrix | 同一资源消息可能落入不同冲突域，FIFO 围栏失效 | `test_claim_bucket_key_backfill_matches_helper_priority`、PostgreSQL `EXPLAIN` gate |
| `claim_bucket_key` 在 `PROCESSING` 后被重算 | repository 只允许入库/claim 前补齐或纠偏；写入 `processor_token` 后冻结 | 早到消息可能被移动到新 bucket，后序消息超车 | `test_claim_bucket_key_frozen_after_processing_claim` |
| 直接写 Inbox 路径漏写 `claim_bucket_key` | repository create/create_idempotent 兜底；direct writer matrix 覆盖所有现有写入形态 | 某些手工、沙箱或 Hold 恢复消息无法被正确 claim 或落入错误冲突域 | `test_direct_inbox_writers_receive_claim_bucket_key` |
| `WorklineInbox` 继续重复继承 `DataTableMixin` | Task 3 修改模型时一并删除重复 mixin，并用模型合同测试保护继承形态 | 后续字段/mixin 变更时出现重复列、MRO 或元数据噪声 | `test_workline_inbox_has_single_data_table_mixin` |
| `RESOURCE_RETRY` 被计为 failed 或 skipped | `ProcessResult` 增加 `resource_wait`；资源等待只计 `processed + resource_wait` | 失败率或跳过率被正常资源忙污染，监控误判系统故障 | `test_resource_retry_counts_as_processed_resource_wait_not_skipped_failed_success` |
| effect 层直接写 Inbox `RETRY` 后 processor 又 mark processed | `WriteBackDisposition` 显式返回，Inbox 终态只由 processor 写入 | 资源等待被覆盖为成功，消息不再重试 | `test_resource_retry_disposition_parks_inbox_and_does_not_mark_processed` |
| `WriteBackDisposition` 定义在 app service 层 | 放在 `src/workline_runtime/effect_result.py` 中立合同模块 | runtime effect 与 app service 互相依赖，后续循环 import 风险上升 | import graph / focused unit tests |
| 同一 Inbox 从资源 A 转等资源 B 时旧诊断仍 ACTIVE | 新资源等待前 resolve 同 inbox 的旧 ACTIVE wait | 现场看到已经释放的资源仍在告警 | `test_resource_wait_transition_resolves_previous_active_resource_wait_for_same_inbox` |
| 设备 busy 被改成 Inbox `RESOURCE_WAIT` | 设备 busy 保持 Outbox `BLOCKED_RESOURCE`，ECS `IDLE` probe 才放行 | 命令副作用可能重复创建或绕过 Outbox 幂等围栏 | `test_device_busy_stays_outbox_blocked_resource_not_inbox_resource_wait` |
| 测试 fixture 手写 `claim_bucket_key` | 统一 WorklineInbox 测试构造 helper；只有异常数据测试允许 override | 多套 bucket 规则漂移，全量测试在后期集中失败 | `test_workline_inbox_test_factory_populates_default_claim_bucket_key` |

## Planned Test Coverage Diagram

```text
CODE PATHS                                      REQUIRED COVERAGE
SessionResolver entry admission
  ├── same business_key reuse                   unit: reuse existing/latest session
  └── different business_key same workline       unit+integration: multiple open sessions

Inbox claim + bucket fence
  ├── claim_bucket_key generation               unit: helper priority + direct repo guard
  ├── direct inbox writers                      unit: InboxService + OperationService + Hold release paths
  ├── migration backfill                         migration: no NULL + helper parity
  ├── same bucket earlier PROCESSING             unit/integration: later message not claimed
  └── PostgreSQL hot queue                       integration: EXPLAIN validates each access path

RESOURCE_WAIT write-back
  ├── effect result                              unit: RESOURCE_RETRY returned through all layers
  ├── processor terminal update                  unit: park retry, no mark processed
  ├── result counters                            unit: processed+resource_wait, not skipped/failed/success
  └── duplicate-entry gate                       unit: same inbox retry bypass only

Diagnostic lifecycle
  ├── same inbox + same resource                 unit: update first/last/wait_count
  ├── same inbox + new resource                  unit: resolve old ACTIVE, record new ACTIVE
  └── successful retry                           unit/integration: resolve current ACTIVE

Device dispatch boundary
  ├── local projection IDLE                      unit: does not release blocked outbox
  └── ECS realtime IDLE probe                    unit/integration: releases blocked outbox
```

## Implementation Tasks

Synthesized from Eng Review findings. These are already folded into Task 1-6 below; do not implement them as a separate branch.

- [ ] **R1 (P1, human: ~30min / CC: ~5min)** — runtime — 定义 `RESOURCE_RETRY` 统计语义
  - Surfaced by: Architecture Review — 当前等待分支按 failed 计数，资源等待不应污染 failed 或 skipped。
  - Files: PLAN、SPEC、`tests/workline_runtime/test_inbox_batch_processor.py`
  - Verify: `test_resource_retry_counts_as_processed_resource_wait_not_skipped_failed_success`
- [ ] **R2 (P1, human: ~30min / CC: ~5min)** — inbox claim — 锁定 `claim_bucket_key` 生命周期
  - Surfaced by: Architecture Review — claim 后重算 key 会移动冲突域，破坏同 bucket 队首围栏。
  - Files: PLAN、SPEC、`tests/workline_runtime/test_inbox_claim_plan.py`
  - Verify: `test_claim_bucket_key_frozen_after_processing_claim`
- [ ] **R3 (P2, human: ~1h / CC: ~10min)** — tests — 加入 WorklineInbox 测试构造 helper
  - Surfaced by: Code Quality Review — `claim_bucket_key` 非空后，多处直接 `WorklineInbox(...)` fixture 会重复手写字段。
  - Files: `tests/helpers/workline_inbox_factory.py` 或现有测试 helper、相关 WorkLine tests
  - Verify: 全量 WorkLine runtime focused tests 不因缺 `claim_bucket_key` 失败。
- [ ] **R4 (P2, human: ~30min / CC: ~5min)** — runtime — 把 `WriteBackDisposition` 放到中立小模块
  - Surfaced by: Code Quality Review — runtime effect 和 app write-back service 已有局部互引，disposition 不应放在 service 层。
  - Files: `src/workline_runtime/effect_result.py`、effect/write-back/processor
  - Verify: import graph 无 runtime -> app service enum 依赖。
- [ ] **R5 (P1, human: ~45min / CC: ~10min)** — diagnostics — 明确连续 `RESOURCE_WAIT` 诊断关闭规则
  - Surfaced by: Test Review — 同一 Inbox 从资源 A 等到资源 B 时旧 ACTIVE wait 必须关闭。
  - Files: diagnostic service/repository、resource wait tests
  - Verify: `test_resource_wait_transition_resolves_previous_active_resource_wait_for_same_inbox`
- [ ] **R6 (P1, human: ~45min / CC: ~10min)** — inbox claim — 补齐 direct writer 覆盖矩阵
  - Surfaced by: Code Quality Review — 当前文档只点名 sandbox external callback，但现有代码还有 replay、manual、sandbox event/result、timeout 和 Runtime Hold continue-result 等写入形态。
  - Files: PLAN、SPEC、`tests/workline_runtime/test_workline_operation_service.py`、`tests/workline_runtime/test_runtime_hold_release_service.py`、`tests/workline_runtime/test_inbox_service.py`
  - Verify: `test_direct_inbox_writers_receive_claim_bucket_key`
- [ ] **R7 (P2, human: ~15min / CC: ~5min)** — inbox model — 清理重复 `DataTableMixin`
  - Surfaced by: Code Quality Review — `WorklineInbox` 当前重复继承 `DataTableMixin`，Task 3 已修改模型，适合同步消除结构噪声。
  - Files: `src/app/workline/models/inbox.py`、model contract tests
  - Verify: `test_workline_inbox_has_single_data_table_mixin`
- [ ] **R8 (P1, human: ~30min / CC: ~10min)** — PostgreSQL gate — 写明强制运行入口
  - Surfaced by: Test Review — 默认 `tests/conftest.py` 使用 SQLite，`SKIP LOCKED` 和 partial index 不能靠 SQLite 证明。
  - Files: PLAN、SPEC、PostgreSQL-backed claim/EXPLAIN tests
  - Verify: `RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL=... uv run pytest ...`

## Worktree parallelization strategy

Sequential implementation, no parallelization opportunity.

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Runtime intent + constants | `src/workline_runtime/`, `src/app/workline/constants.py` | — |
| Entry blocker deletion | `src/workline_runtime/`, `src/app/workline/services/`, `src/app/workline/repositories/` | Runtime intent + constants |
| Inbox claim + migration | `src/app/workline/models/`, `src/app/workline/repositories/`, `src/app/workline/services/`, `migrations/` | Entry blocker deletion |
| RESOURCE_WAIT effect + diagnostics | `src/workline_runtime/`, `src/app/workline/services/`, `src/app/workline/repositories/`, `src/workline_plugins/` | Inbox claim + migration |
| Integration + docs + gates | `tests/`, `docs/` | All prior steps |

Runtime、Inbox、diagnostic、plugin 路径共享同一批合同；并行 worktree 会提高合并冲突和语义漂移风险。按 Task 1 → Task 6 顺序执行。

## Task 1: RuntimeIntent 与常量合同

**Files:**
- Modify: `src/app/workline/constants.py`
- Modify: `src/workline_runtime/runtime_intent.py`
- Modify: `src/workline_runtime/diagnostics/codes.py`
- Modify: `src/workline_runtime/diagnostics/registry.py`
- Modify: `src/workline_runtime/diagnostics/builder.py`
- Test: `tests/workline_runtime/test_workline_constants.py`
- Test: `tests/workline_runtime/test_runtime_intent_contract.py`
- Test: `tests/workline_runtime/test_runtime_intent.py`
- Test: `tests/workline_runtime/test_diagnostics_builder.py`

- [ ] **Step 1: 运行影响分析**

在 GitNexus MCP 中运行：

```text
impact(repo="wes_backend", target="RuntimeIntent", direction="upstream")
impact(repo="wes_backend", target="RuntimeIntentKind", direction="upstream")
```

通过标准：记录直接 import/callers；若 HIGH/CRITICAL，先汇报再继续。

- [ ] **Step 2: 写失败测试**

新增或更新以下测试点：

- `test_resource_wait_intent_requires_resource_kind_and_key`
- `test_resource_wait_intent_preserves_reason_and_evidence`
- `test_resource_wait_diagnostic_code_defaults_to_auto_retryable_warning`
- `test_workline_resource_wait_retry_default_matches_inbox_beat_interval`
- `test_inbox_processing_stale_floor_no_longer_depends_on_bucket_lock`

关键断言：

```text
RuntimeIntent.resource_wait(resource_kind="STATION", resource_key="station:TARGET_STATION", reason_code="STATION_BUSY", ...)
  -> kind == RuntimeIntentKind.RESOURCE_WAIT
  -> payload_json.resource_kind == "STATION"
  -> payload_json.resource_key == "station:TARGET_STATION"

RuntimeIntent.resource_wait(resource_kind="", resource_key="x", ...) raises ValueError
RuntimeIntent.resource_wait(resource_kind="STATION", resource_key="", ...) raises ValueError
WORKLINE_RESOURCE_WAIT_RETRY_SECONDS == 10
env WORKLINE_RESOURCE_WAIT_RETRY_SECONDS override is honored
WORKLINE_INBOX_BATCH_PARALLELISM is not exported
INBOX_BUCKET_LOCK_TTL_SECONDS is not exported
```

- [ ] **Step 3: 运行失败测试**

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_intent_contract.py \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_workline_constants.py \
  tests/workline_runtime/test_diagnostics_builder.py -q
```

期望：RESOURCE_WAIT enum/factory/constant/diagnostic code 相关测试失败。

- [ ] **Step 4: 最小实现**

实现合同：

- `constants.py`
  - 删除 `WORKLINE_INBOX_BATCH_PARALLELISM`。
  - 删除 `WORKLINE_INBOX_BATCH_MAX_PARALLELISM`。
  - 删除 `INBOX_BUCKET_LOCK_TTL_SECONDS`。
  - 新增 `WORKLINE_RESOURCE_WAIT_RETRY_SECONDS = int(os.getenv("WORKLINE_RESOURCE_WAIT_RETRY_SECONDS", "10"))`；该间隔只表示资源等待重试节奏，不表示业务容量，并允许现场按环境配置调优。
  - `WORKLINE_INBOX_PROCESSING_STALE_SECONDS` 下限改为 `INBOX_PROCESS_TIMEOUT_SECONDS + INBOX_PROCESSING_STALE_MARGIN_SECONDS`。
  - 更新注释和 `__all__`。
- `runtime_intent.py`
  - 在 `RuntimeIntentKind` 增加 `RESOURCE_WAIT = "RESOURCE_WAIT"`。
  - 增加 `RuntimeIntent.resource_wait(...)` 工厂，资源字段放入 `payload_json`。
  - `validate_intent()` 校验 `reason_code`、`message`、`payload_json.resource_kind`、`payload_json.resource_key`。
- diagnostics
  - 增加 `ErrorCode.RESOURCE_WAIT`，默认 `ErrorDomain.WORKFLOW`。
  - registry fix 文案指向“等待物理资源释放或检查资源占用证据”。
  - builder 默认值使用 `Severity.WARNING`、`Recoverability.AUTO_RETRYABLE`、`ProblemClass.SOFTWARE`。

- [ ] **Step 5: 运行通过测试**

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_intent_contract.py \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_workline_constants.py \
  tests/workline_runtime/test_diagnostics_builder.py -q
```

期望：全部通过。

- [ ] **Step 6: 记录阶段结果**

不提交。确认 focused tests 通过后继续 Task 2；最终统一在 Task 6 跑全量门禁、GitNexus detect changes 并单次提交。

## Task 2: 删除工作线级入口 blocker

**Files:**
- Modify: `src/workline_runtime/session_resolver.py`
- Modify: `src/app/workline/repositories/session_repository.py`
- Modify: `src/app/workline/services/diagnostic_service.py`
- Modify: `src/app/workline/services/integration_debug_service.py`
- Test: `tests/workline_runtime/test_session_resolver.py`
- Test: `tests/workline_runtime/test_session_repository.py`
- Test: `tests/workline_runtime/test_integration_debug_service.py`
- Test: `tests/workline_runtime/test_workline_diagnostic_service.py`

- [ ] **Step 1: 运行影响分析**

```text
impact(repo="wes_backend", target="SessionResolver", direction="upstream")
impact(repo="wes_backend", target="get_open_entry_blocker_for_workline", direction="upstream")
impact(repo="wes_backend", target="WorklineEntryAdmissionBlocked", direction="upstream")
```

通过标准：确认影响主要集中在 `inbox_batch_processor` 和 workline tests；HIGH/CRITICAL 先汇报。

- [ ] **Step 2: 写失败测试**

新增/改写测试点：

- `test_device_event_creates_new_session_when_other_business_key_open`
- `test_device_event_keeps_same_business_key_reuse`
- `test_workline_entry_admission_blocker_symbol_removed`
- `test_latest_cases_does_not_include_pending_entry_admission_backlog`
- `test_entry_admission_diagnostic_resolver_removed_from_service_contract`

关键断言：

```text
两个不同 business_key + 同 workline_id:
  旧 open session 存在
  新 SCAN_COMPLETED resolve_or_create()
  返回新 session，且不 raise WorklineEntryAdmissionBlocked

integration debug latest cases:
  不调用 list_pending_entry_admission_cases
  不返回 WORKLINE_ENTRY_ADMISSION_BLOCKED synthetic case
```

- [ ] **Step 3: 运行失败测试**

```bash
uv run pytest \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_session_repository.py \
  tests/workline_runtime/test_integration_debug_service.py \
  tests/workline_runtime/test_workline_diagnostic_service.py -q
```

期望：旧 blocker 行为和旧 debug/diagnostic 入口导致测试失败。

- [ ] **Step 4: 最小实现**

实现合同：

- `session_resolver.py`
  - 删除 `WorklineEntryAdmissionBlocked`。
  - 删除 `_lock_workline_entry_admission()`。
  - 删除 `_find_entry_admission_blocker_session()`。
  - `_resolve_device_event()` 中只保留同 `workline_id + business_key` 查建锁，不再检查其它 open session。
  - 更新 `__all__`。
- `session_repository.py`
  - 删除 `get_open_entry_blocker_for_workline()`。
  - 保留 `list_open_by_workline_id()` 和 station/rack 相关候选查询。
- `diagnostic_service.py`
  - 删除 `resolve_entry_admission_diagnostics()`。
- `integration_debug_service.py`
  - 删除 pending entry-admission backlog 加载、计数和 synthetic case 构造。
  - 保留真实 trace/diagnostic 查询。

- [ ] **Step 5: 全仓搜索旧合同**

```bash
rg "WorklineEntryAdmissionBlocked|get_open_entry_blocker_for_workline|workline-entry-admission|WORKLINE_ENTRY_ADMISSION_BLOCKED|pending_entry_admission" src tests
```

期望：只允许 SPEC/plan 文档命中；`src/` 和 `tests/` 不再命中旧实现合同。

- [ ] **Step 6: 运行通过测试**

```bash
uv run pytest \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_session_repository.py \
  tests/workline_runtime/test_integration_debug_service.py \
  tests/workline_runtime/test_workline_diagnostic_service.py -q
```

期望：全部通过。

- [ ] **Step 7: 记录阶段结果**

不提交。确认旧 blocker 搜索和 focused tests 通过后继续 Task 3；最终统一在 Task 6 单次提交。

## Task 3: 简化 Inbox claim 与 batch 处理合同

**Files:**
- Modify: `src/app/workline/models/inbox.py`
- Create: `migrations/versions/<alembic_generated>_add_workline_inbox_claim_bucket_key.py`
- Modify: `src/app/workline/repositories/inbox_repository.py`
- Modify: `src/app/workline/services/inbox_service.py`
- Modify: `src/app/workline/services/inbox_batch_processor.py`
- Modify: `src/celery_app/tasks/workline.py`
- Create/Modify: `tests/helpers/workline_inbox_factory.py` 或现有测试 helper
- Test: `tests/workline_runtime/test_inbox_service.py`
- Test: `tests/workline_runtime/test_inbox_batch_processor.py`
- Test: `tests/workline_runtime/test_inbox_claim_plan.py`
- Test: `tests/workline_runtime/test_workline_operation_service.py`
- Test: `tests/workline_runtime/test_runtime_hold_release_service.py`
- Test: `tests/workline_runtime/test_celery_task_entrypoints.py`
- Test: `tests/workline_runtime/test_celery_internal_signals.py`

- [ ] **Step 1: 运行影响分析**

```text
impact(repo="wes_backend", target="InboxBatchProcessor", direction="upstream")
impact(repo="wes_backend", target="process_batch", file_path="src/app/workline/services/inbox_batch_processor.py", direction="upstream")
impact(repo="wes_backend", target="claim_pending_messages", file_path="src/app/workline/repositories/inbox_repository.py", direction="upstream")
impact(repo="wes_backend", target="process_inbox_batch", file_path="src/celery_app/tasks/workline.py", direction="upstream")
impact(repo="wes_backend", target="WorklineInbox", file_path="src/app/workline/models/inbox.py", direction="upstream")
```

通过标准：确认 task entrypoint、tests、service exports 的影响范围；HIGH/CRITICAL 先汇报。

- [ ] **Step 2: 写失败测试**

新增/改写测试点：

- `test_process_batch_rejects_parallelism_kwarg`
- `test_process_batch_claims_one_message_at_a_time_until_limit`
- `test_process_batch_does_not_use_bucket_lock_provider`
- `test_repository_claim_blocks_same_bucket_when_earlier_processing_exists`
- `test_get_new_messages_is_removed_from_repository_and_service`
- `test_celery_process_inbox_batch_signature_has_no_parallelism`
- `test_workline_task_aliases_removed`
- `test_claim_pending_messages_sorts_returning_rows_by_received_at_id`
- `test_workline_inbox_claim_bucket_key_populated_for_claimable_messages`
- `test_workline_inbox_claim_bucket_key_is_not_nullable_after_backfill`
- `test_claim_bucket_key_backfill_matches_helper_priority`
- `test_workline_inbox_model_requires_claim_bucket_key`
- `test_workline_inbox_has_single_data_table_mixin`
- `test_repository_create_injects_claim_bucket_key`
- `test_repository_create_idempotent_injects_claim_bucket_key_for_direct_paths`
- `test_inbox_service_create_populates_claim_bucket_key`
- `test_inbox_service_timeout_populates_claim_bucket_key`
- `test_operation_service_replay_populates_claim_bucket_key`
- `test_operation_service_manual_operation_populates_claim_bucket_key`
- `test_operation_service_sandbox_event_populates_claim_bucket_key`
- `test_operation_service_sandbox_external_callback_populates_claim_bucket_key`
- `test_operation_service_sandbox_command_result_populates_claim_bucket_key`
- `test_runtime_hold_continue_result_populates_claim_bucket_key`
- `test_claim_bucket_key_completed_before_processing_claim`
- `test_claim_bucket_key_frozen_after_processing_claim`
- `test_workline_inbox_test_factory_populates_default_claim_bucket_key`
- `test_workline_inbox_test_factory_allows_explicit_claim_bucket_override`

关键断言：

```text
InboxBatchProcessor.process_batch(db, limit=10, parallelism=2) raises TypeError
process_inbox_batch.run(limit=10, parallelism=2) raises TypeError
process_batch(limit=10) 每轮调用 claim_pending_messages(limit=1)，处理完成后再 claim 下一条
claim_pending_messages(limit=N) 仓储合同仍按 received_at/id 返回各 bucket 队首，供多 worker/未来入口复用
数据库 UPDATE RETURNING 乱序时，repository 返回值仍按 received_at/id 排序
同 bucket 早到 PROCESSING 存在时，晚到 NEW 不会被 claim
claim_bucket_key 是 repository 持久化兜底不变量；InboxService create/timeout、OperationService replay/manual/sandbox event/sandbox external callback/sandbox command result、RuntimeHoldReleaseService continue-result 都不能漏写
claim_bucket_key migration backfill 后没有 NULL；模型字段非空，数据库约束拒绝 NULL
claim_bucket_key migration/backfill 表达式与 inbox_claim_bucket helper 通过同一 case matrix 验证优先级一致：session_id > device_id > device_code/location > workline_id > serial:unknown
claim_bucket_key 在入库和 claim 前纠偏时补齐/重算；进入 PROCESSING 或写入 processor_token 后冻结；claim 查询不从 payload_json 重复推导 bucket
测试侧直接构造 WorklineInbox 时必须通过统一 helper 生成 claim_bucket_key；确需测试异常数据时显式传入 override
WorklineInbox 只继承一次 DataTableMixin
scan_timeouts / device_heartbeat_scanner alias 不再可 import
```

- [ ] **Step 3: 运行失败测试**

```bash
uv run pytest \
  tests/workline_runtime/test_inbox_service.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_inbox_claim_plan.py \
  tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_celery_task_entrypoints.py \
  tests/workline_runtime/test_celery_internal_signals.py -q
```

期望：旧 `parallelism`、bucket lock、`get_new_messages` 和 alias 合同导致失败。

- [ ] **Step 4: 最小实现**

实现合同：

- `inbox.py` + migration
  - 删除 `WorklineInbox` 重复继承的 `DataTableMixin`，保持 mixin 列表单一且可读。
  - 新增普通列 `claim_bucket_key`，作为数据库 claim 的物化冲突域键；不要使用数据库生成列。
  - 新迁移必须用 Alembic revision generator 生成 revision id，再编辑迁移文件。
  - migration 先以 nullable 添加字段并 backfill 全量历史消息，执行 `claim_bucket_key IS NULL` 验证为 0 后设置 `NOT NULL`，最后建立索引。
  - migration backfill 的 SQL/SQLAlchemy 表达式必须和 `inbox_claim_bucket.py` helper 通过同一 case matrix 验证优先级一致；测试覆盖各优先级输入，防止迁移和运行时 helper 漂移。
  - 模型字段声明为非空字符串；兜底 `serial:unknown` 只用于确实缺少 session/device/workline 归属的系统消息，不作为常态入口。
  - 建立 hot queue 索引，并按访问路径验收：claimable 队列排序、同 bucket 队首围栏、到期 `RETRY` 和 stale `PROCESSING` 回收都必须有可解释的 PostgreSQL 访问路径。
  - 保留现有 `received_at/id` FIFO 语义；`claim_bucket_key` 只表达资源冲突域，不表达业务容量。
  - 更新 `WorklineInbox` 模型 docstring 中的 ASCII 状态机和处理约束：删除“不同 bucket 可按配置有界并发处理”，改为“同 bucket 队首围栏由 `claim_bucket_key` 保证；单 processor 顺序 claim，跨 worker 并发由数据库 claim 承载”。
- `inbox_claim_bucket.py`
  - 新增无副作用 helper，统一按 `session_id` > `device_id` > `device_code/location` > `workline_id` > `serial:unknown` 生成 key。
  - helper 只做确定性字段归一化，不访问数据库、不读取环境变量。
- tests helper
  - 新增或扩展统一 WorklineInbox 测试构造 helper，默认调用 `inbox_claim_bucket.py` 生成 `claim_bucket_key`。
  - 迁移现有直接 `WorklineInbox(...)` fixture 到 helper，除非测试正在验证模型/数据库拒绝 NULL。
  - helper 必须允许显式覆盖 `claim_bucket_key`，用于同 bucket、不同 bucket、NULL 拒绝等边界测试。
- `inbox_repository.py`
  - 将 `claim_bucket_key` 作为 repository 层持久化兜底不变量；`create()`、`create_idempotent()` 以及 claim 前归属字段纠偏路径统一补齐或重算 key。
  - 一旦消息进入 `PROCESSING` 或写入 `processor_token`，不得再自动重算 `claim_bucket_key`；`RETRY/PROCESSED` 状态更新只改变处理状态和 retry 元数据，不移动 bucket。
  - service 可以传入 key，但 repository 必须是最终 guard，覆盖 `InboxService` 通用 create/timeout、`OperationService` replay/manual/sandbox event/sandbox external callback/sandbox command result、`RuntimeHoldReleaseService` continue-result 等写入形态。
  - 删除 `get_new_messages()`。
  - 删除 `list_pending_entry_admission_cases()`、`count_pending_entry_admission_cases()`、`_pending_entry_admission_filters()`。
  - 删除 `_claim_bucket_key_expr()` 的 JSON payload 热路径表达式，claim 查询改用物化 `claim_bucket_key`。
  - 保留 `_claimable_condition()`、`claim_pending_messages()`。
  - 修正 `returning()` 中重复 `received_at` 字段。
  - 将 `result.mappings().all()` 转换为 `WorklineInboxClaim` 后，显式按 `(received_at, id)` 排序再返回。
- `inbox_service.py`
  - 删除 `get_new_messages()` wrapper。
  - 删除 pending entry-admission wrapper。
  - 不在 service 层复制 claim key 生成规则；创建路径正常调用 repository，由 repository 统一兜底写入 `claim_bucket_key`。
  - 将 `park_for_retry()` 注释改为“资源/安全状态暂不可用时挂起消息，不增加 attempt_count”。
- `inbox_batch_processor.py`
  - 删除 parallelism import、`_clamp_parallelism()`、`_bucket_key()`、Redis bucket lock provider 和 `_process_claims()` wave 调度。
  - `process_batch(self, db, limit=10)` 在 while 循环中每次只 claim 1 条；处理完成后再 claim 下一条，直到达到 `limit` 或无可处理消息。
  - 每条 claim 使用独立 processor token 调用 `_process_claimed_message()`；不要在共享 `AsyncSession` 内引入 `asyncio.gather()`。
  - 保留 repository `claim_pending_messages(limit=...)` 的批量能力，但本 processor 不预先 claim 多条，避免顺序处理时后续消息长时间占用 `PROCESSING` 后被 stale reclaim。
  - 删除 `except WorklineEntryAdmissionBlocked` 分支。
- `tasks/workline.py`
  - `process_inbox_batch(self, limit=10)` 删除 `parallelism` 参数。
  - 删除 `scan_timeouts = TimeoutScanner` 和 `device_heartbeat_scanner = DeviceHeartbeatScanner` alias。
  - 更新 `__all__` 和 docstring。

- [ ] **Step 5: 搜索旧合同**

```bash
rg "WORKLINE_INBOX_BATCH_PARALLELISM|WORKLINE_INBOX_BATCH_MAX_PARALLELISM|INBOX_BUCKET_LOCK_TTL_SECONDS|get_new_messages|parallelism|bucket_lock_provider|scan_timeouts =|device_heartbeat_scanner =" src tests
```

期望：`src/` 不再命中旧合同；测试只保留“已删除合同”的反向断言。

- [ ] **Step 6: 运行通过测试**

```bash
uv run pytest \
  tests/workline_runtime/test_inbox_service.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_inbox_claim_plan.py \
  tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_celery_task_entrypoints.py \
  tests/workline_runtime/test_celery_internal_signals.py -q
```

期望：全部通过。

- [ ] **Step 7: 记录阶段结果**

不提交。确认 claim 合同、旧参数搜索和 focused tests 通过后继续 Task 4；最终统一在 Task 6 单次提交。

## Task 4: RESOURCE_WAIT effect、幂等诊断与插件入口

**Files:**
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Create: `src/workline_runtime/effect_result.py`
- Create: `src/workline_runtime/resource_wait_evidence.py`
- Modify: `src/app/workline/services/write_back_service.py`
- Modify: `src/app/workline/services/inbox_batch_processor.py`
- Modify: `src/app/workline/services/diagnostic_service.py`
- Modify: `src/app/workline/repositories/diagnostic_repository.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_orchestrator_write_back_service.py`
- Test: `tests/workline_runtime/test_resource_wait_evidence.py`
- Test: `tests/workline_runtime/test_workline_diagnostic_service.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_context.py`

- [ ] **Step 1: 运行影响分析**

```text
impact(repo="wes_backend", target="RuntimeIntentEffectApplier", direction="upstream")
impact(repo="wes_backend", target="WorklineDiagnosticService", direction="upstream")
impact(repo="wes_backend", target="_target_station_ready_snapshot", file_path="src/workline_plugins/smt_sorting_inbound/flow_service.py", direction="upstream")
```

通过标准：确认 effect、插件和诊断查询影响；HIGH/CRITICAL 先汇报。

- [ ] **Step 2: 写失败测试**

新增/改写测试点：

- `test_apply_resource_wait_sets_waiting_external_and_returns_resource_retry_disposition`
- `test_runtime_intent_effect_applier_returns_resource_retry_disposition`
- `test_orchestrator_write_back_returns_resource_retry_disposition`
- `test_apply_resource_wait_does_not_create_runtime_hold_or_manual_timeline`
- `test_apply_resource_wait_records_diagnostic_by_inbox_and_resource_key`
- `test_resource_retry_disposition_parks_inbox_and_does_not_mark_processed`
- `test_processed_disposition_still_marks_inbox_processed`
- `test_resource_retry_counts_as_processed_resource_wait_not_skipped_failed_success`
- `test_resource_wait_must_be_final_intent`
- `test_resource_wait_cannot_follow_command_producing_intent`
- `test_record_resource_wait_updates_existing_diagnostic_evidence`
- `test_resource_wait_evidence_merges_first_seen_last_seen_and_wait_count`
- `test_resource_wait_evidence_is_single_source_for_key_context_and_diagnostic_payload`
- `test_resource_wait_retry_same_inbox_bypasses_duplicate_entry_gate`
- `test_resource_wait_success_resolves_active_diagnostic`
- `test_resource_wait_transition_resolves_previous_active_resource_wait_for_same_inbox`
- `test_smt_target_station_busy_returns_resource_wait_intent`
- `test_device_busy_stays_outbox_blocked_resource_not_inbox_resource_wait`

关键断言：

```text
RESOURCE_WAIT effect:
  session.status == WAITING_EXTERNAL
  session.current_wait_type == RESOURCE_WAIT
  RuntimeIntentEffectApplier.apply(...).disposition == RESOURCE_RETRY
  OrchestratorWriteBackService.write_back(...).disposition == RESOURCE_RETRY
  diagnostic_key == RESOURCE_WAIT:<inbox_id>:<resource_key>
  session.context_json.resource_wait.inbox_id == inbox.id
  session.context_json.resource_wait.resource_key == payload_json.resource_key
  no RuntimeHold / no MANUAL_HOLD timeline
  [RESOURCE_WAIT, COMMAND] 和 [COMMAND, RESOURCE_WAIT] 都被合同校验拒绝，RESOURCE_WAIT 必须是本轮最后一个 intent

InboxBatchProcessor disposition:
  RESOURCE_RETRY -> inbox_service.park_for_retry(delay_seconds=WORKLINE_RESOURCE_WAIT_RETRY_SECONDS)
  RESOURCE_RETRY -> no mark_as_processed
  RESOURCE_RETRY -> result.processed += 1, result.resource_wait += 1, no skipped/failed/success increment
  PROCESSED -> mark_as_processed

RESOURCE_WAIT retry:
  current_wait_type == RESOURCE_WAIT 且 context_json.resource_wait.inbox_id == inbox.id 时，入口 duplicate gate 放行
  其它 inbox 或其它 wait_type 仍按重复入口归档
  同一 inbox 从 resource_key=A 转为 resource_key=B 时，A 的 ACTIVE diagnostic 先置为 RESOLVED，B 记录/更新为 ACTIVE
  retry 成功后 ACTIVE RESOURCE_WAIT 诊断变 RESOLVED

SMT target station busy:
  intent.kind == RuntimeIntentKind.RESOURCE_WAIT
  payload_json.resource_kind == STATION
  payload_json.resource_key == station:TARGET_STATION

Device busy:
  later device command remains SystemOutboxStatus.BLOCKED_RESOURCE
  current Inbox is not parked as RESOURCE_WAIT
```

- [ ] **Step 3: 运行失败测试**

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_orchestrator_write_back_service.py \
  tests/workline_runtime/test_resource_wait_evidence.py \
  tests/workline_runtime/test_workline_diagnostic_service.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py -q
```

期望：RESOURCE_WAIT effect/diagnostic/plugin 相关测试失败。

- [ ] **Step 4: 最小实现**

实现合同：

- `resource_wait_evidence.py`
  - 新增唯一 `ResourceWaitEvidence` helper/model，统一生成 diagnostic key、合并 `first_seen_at/last_seen_at/wait_count`，并输出 session context 与 diagnostic evidence。
  - runtime effect 和 diagnostic service 都复用该 helper；不要在两个文件内各自拼接 key 或 evidence 字段。
- `effect_result.py`
  - 新增 runtime 中立小型 result / enum，至少包含 `WriteBackDisposition.PROCESSED` 与 `WriteBackDisposition.RESOURCE_RETRY`。
  - `runtime_intent_effects.py`、`write_back_service.py` 和 `inbox_batch_processor.py` 都引用该模块；不要在 app service 文件中定义 disposition enum。
- `runtime_intent_effects.py`
  - 将 `RuntimeIntentKind.RESOURCE_WAIT` 加入支持集合。
  - 将 RESOURCE_WAIT 视为终止当前 intent list 的等待型 intent；后续 command-producing intent 不应继续执行。
  - 将 `RuntimeIntentEffectApplier.apply(ctx, intents)` 返回值改为 `effect_result.py` 中的小型 result/disposition；没有 RESOURCE_WAIT 时返回 `PROCESSED`，RESOURCE_WAIT 返回 `RESOURCE_RETRY`。
  - 抽本文件内的小型等待态 helper，复用 `start_wait`、`persist_external_wait` 和 `WAIT_STARTED` timeline 逻辑；不要为 RESOURCE_WAIT 新建独立 service。
  - 增加 `_apply_resource_wait(ctx, intent)`：
    - 通过等待态 helper 调 `workline_session_lifecycle_service.start_wait(session, wait_type="RESOURCE_WAIT", occurred_at=ctx["now"])`。
    - 使用 `resource_wait_evidence.py` 中的 `ResourceWaitEvidence` 合并并写入 `session.context_json.resource_wait`，字段包括 `inbox_id`、`resource_kind`、`resource_key`、`reason_code`、`first_seen_at`、`last_seen_at`、`wait_count`。
    - 调 `WorklineSessionRepository.persist_external_wait(..., wait_type="RESOURCE_WAIT", context_json=...)`。
    - 调 `workline_diagnostic_service.record_resource_wait(...)`。
    - 返回 `RESOURCE_RETRY` disposition，不直接调用 `inbox_service.park_for_retry()`。
    - 发 WAITING timeline，不发 MANUAL timeline。
- `write_back_service.py`
  - 直接返回 `effect_result.py` 中定义的 disposition/result；不要用异常、写后读状态或 `ctx` 副作用表达正常 RESOURCE_WAIT。
  - `OrchestratorWriteBackService.write_back(...)` 必须直接返回 effect applier 的 disposition，供 `InboxBatchProcessor` 决定 Inbox 终态。
- `diagnostic_service.py`
  - 增加 `record_resource_wait(...)`；diagnostic key 和 evidence payload 必须来自 `ResourceWaitEvidence`，已有诊断存在时更新 `evidence_json.first_seen_at/last_seen_at/wait_count` 和 `message`，不要创建第二条。
  - 增加 `resolve_resource_wait_diagnostics(...)`；同一 `inbox_id + resource_key` 成功重试后把 ACTIVE 诊断置为 RESOLVED。
  - 增加同 Inbox 转换资源等待的关闭规则：记录新 `resource_key` 前，先 resolve 同一 `inbox_id` 下其它 ACTIVE RESOURCE_WAIT 诊断。
- `diagnostic_repository.py`
  - 增加按 `diagnostic_key` 更新 evidence/status 的方法。
  - 增加按 `diagnostic_key` resolve ACTIVE 诊断的方法。
- `inbox_batch_processor.py`
  - 根据 write-back disposition 统一写 Inbox 终态：`RESOURCE_RETRY` 调 `park_for_retry()`，`PROCESSED` 调 `mark_as_processed()`。
  - `RESOURCE_RETRY` 分支不得继续执行后续 `mark_as_processed()`，防止 `RETRY` 被覆盖为 `PROCESSED`。
  - `ProcessResult` 增加 `resource_wait` 计数；`RESOURCE_RETRY` 统计为正常等待：`processed += 1`、`resource_wait += 1`，不增加 `skipped`、`failed` 或 `success`。
  - 增加 RESOURCE_WAIT retry 白名单：只有 `current_wait_type == "RESOURCE_WAIT"` 且 `session.context_json.resource_wait.inbox_id == inbox.id` 时，入口 duplicate gate 放行。
  - 成功写回同一 inbox 后调用 `resolve_resource_wait_diagnostics(..., auto_commit=False)`。
- `smt_sorting_inbound/flow_service.py`
  - `SORTING_TARGET_STATION_LEASE_BUSY` 改为 `RuntimeIntent.resource_wait(...)`。
  - `SORTING_TARGET_STATION_LEASE_UNKNOWN` 保持 `BLOCK`，因为这是配置/状态来源缺失，不是资源暂忙。
- 设备 dispatch 边界
  - 保留 `_enforce_device_command_governance(..., allow_busy=True)` 与 Outbox dispatch `BLOCKED_RESOURCE` 逻辑。
  - 不把 device busy 改写为 Inbox `RESOURCE_WAIT`，只在 trace/monitor 展示层统一呈现为资源等待。

- [ ] **Step 5: 运行通过测试**

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_orchestrator_write_back_service.py \
  tests/workline_runtime/test_resource_wait_evidence.py \
  tests/workline_runtime/test_workline_diagnostic_service.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py -q
```

期望：全部通过。

- [ ] **Step 6: 记录阶段结果**

不提交。确认 RESOURCE_WAIT、diagnostic、disposition 和设备 Outbox 边界 tests 通过后继续 Task 5；最终统一在 Task 6 单次提交。

## Task 5: 多物料并发、claim 执行计划和回归覆盖

**Files:**
- Modify: `tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py`
- Modify: `tests/integration/workline_runtime/test_real_mock_driven_sandbox_e2e.py`
- Modify: `tests/workline_runtime/test_inbox_service.py`
- Modify: `tests/workline_runtime/test_inbox_batch_processor.py`
- Modify: `tests/workline_runtime/test_outbox_dispatch_service.py`
- Modify: `tests/workline_runtime/test_inbox_claim_plan.py`

- [ ] **Step 1: 写失败测试**

新增/改写测试点：

- `test_real_entry_allows_five_business_keys_to_open_sessions_on_same_workline`
- `test_mock_driven_sandbox_multiple_materials_no_entry_admission_diagnostic`
- `test_same_device_never_has_two_active_commands`
- `test_device_busy_keeps_later_outbox_blocked_resource_without_reparking_inbox`
- `test_local_device_idle_projection_does_not_release_blocked_outbox`
- `test_ecs_idle_probe_releases_blocked_device_outbox_for_dispatch`
- `test_resource_wait_retry_does_not_increment_attempt_count`
- `test_resource_wait_retry_same_inbox_reexecutes_after_station_free`
- `test_resource_wait_success_resolves_diagnostic`
- `test_process_batch_does_not_preclaim_limit_when_processing_sequentially`
- `test_claim_pending_messages_explain_uses_claimable_indexes`

关键断言：

```text
连续 5 个 SCAN_COMPLETED:
  open sessions count >= 5
  diagnostics does not contain WORKLINE_ENTRY_ADMISSION_BLOCKED

同一设备 busy:
  active command count for device <= 1
  later outbox enters BLOCKED_RESOURCE with device resource evidence
  current inbox is not parked to RETRY by device busy
  本地 DeviceStatus=IDLE 且 current_command_id=None 不释放 BLOCKED_RESOURCE
  ECS status probe 返回 IDLE 后才允许重新 claim/dispatch blocked outbox

RESOURCE_WAIT retry:
  首次 Station busy 后 inbox.status == RETRY 且 attempt_count 不变
  Station free 后同一 inbox 重新执行插件并进入后续正常意图
  成功后 RESOURCE_WAIT 诊断为 RESOLVED

顺序 claim:
  process_batch(limit=10) 不会一次 claim 10 条
  每条消息完成或终态失败后才 claim 下一条

EXPLAIN gate:
  PostgreSQL 环境使用足够热队列数据，执行 ANALYZE 后检查 EXPLAIN (ANALYZE, FORMAT JSON)
  分别断言 claimable 队列排序、同 bucket 队首围栏、到期 RETRY、stale PROCESSING 回收路径使用匹配的 hot queue partial index
  不用小样本 no Seq Scan 误报规则，也不把 SQLite 编译 SQL 当成执行计划证明
  SQLite 环境 skip，理由写明 "PostgreSQL plan gate only"；该 skip 只允许作为本地开发降级，最终交付或 CI 必须提供 PostgreSQL-backed 结果
```

- [ ] **Step 2: 运行失败测试**

```bash
uv run pytest \
  tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py \
  tests/integration/workline_runtime/test_real_mock_driven_sandbox_e2e.py \
  tests/workline_runtime/test_inbox_service.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_outbox_dispatch_service.py \
  tests/workline_runtime/test_inbox_claim_plan.py -q
```

期望：多 session、RESOURCE_WAIT、EXPLAIN gate 覆盖在当前实现上失败或部分失败。

- [ ] **Step 3: 补齐测试辅助数据**

实现要求：

- 使用现有 workline/plugin/device fixtures，不新建大型 fixture 框架。
- PostgreSQL EXPLAIN gate 使用固定种子数据：
  - 足够多的历史/终态行，让 planner 不会因小表选择 Seq Scan。
  - 多条 `NEW`。
  - 多条 `RETRY` 且 `next_retry_at <= now`。
  - 多条同 bucket `PROCESSING`。
  - 不同 bucket 的可 claim 头部消息。
  - 所有 active queue 种子都写入 `claim_bucket_key`，并覆盖同 bucket 与不同 bucket 场景。
  - 插入后执行 `ANALYZE wes_biz.workline_inbox`。
- EXPLAIN 测试读取 JSON plan，只断言 active queue 访问路径使用 `claim_bucket_key` partial index，不断言本机毫秒耗时。
- EXPLAIN 测试必须复用现有 integration fixture 入口；不要为本计划新增并行的 PostgreSQL fixture 框架。

- [ ] **Step 4: 运行通过测试**

```bash
uv run pytest \
  tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py \
  tests/integration/workline_runtime/test_real_mock_driven_sandbox_e2e.py \
  tests/workline_runtime/test_inbox_service.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_outbox_dispatch_service.py \
  tests/workline_runtime/test_inbox_claim_plan.py -q
```

期望：全部通过；SQLite 环境中的 PostgreSQL-only plan gate 可以本地明确 skip，但最终交付或 CI 必须有 PostgreSQL-backed 通过记录。

PostgreSQL-backed claim/EXPLAIN 子集必须单独留下通过记录：

```bash
RUN_WORKLINE_INTEGRATION=1 \
INTEGRATION_DATABASE_URL="<postgresql+asyncpg url>" \
uv run pytest \
  tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py \
  tests/integration/workline_runtime/test_real_mock_driven_sandbox_e2e.py \
  tests/workline_runtime/test_inbox_claim_plan.py -q
```

期望：PostgreSQL 环境不 skip；`SKIP LOCKED`、同 bucket 队首围栏和 `EXPLAIN` 断言全部通过。

- [ ] **Step 5: 记录阶段结果**

不提交。确认集成、资源等待和 EXPLAIN gate tests 通过；本地 PostgreSQL 缺失时可以记录 skip 原因继续文档/代码收尾，但最终交付或 CI 必须补 PostgreSQL-backed 通过记录。最终统一在 Task 6 单次提交。

## Task 6: 文档、质量门禁和提交前检测

**Files:**
- Modify: `docs/hardware/粗分机硬件供应商联调操作手册.md`
- Modify: `docs/hardware/粗分机内部Mock与Sandbox调试手册.md`
- Modify: `docs/workline_diagnostics_quickstart.md`
- Modify: `docs/business/workline_runtime_workflow_guide.md`
- Modify: `docs/superpowers/specs/2026-06-09-workline-resource-constrained-parallelism-spec.md`
- Modify: `docs/superpowers/plans/2026-06-09-workline-resource-constrained-parallelism.md`

- [ ] **Step 1: 更新用户文档**

文档必须表达以下事实：

- 同一 WorkLine 可以有多个 open Session。
- 并发容量来自设备、Station、rack/bin/cell 状态，不来自环境变量。
- 设备 command terminal 和本地 `DeviceStatus` 只是投影；blocked outbox 必须等 ECS 实时 `IDLE` probe 放行。
- 删除 `WORKLINE_INBOX_BATCH_PARALLELISM` 调参说明。
- 明确 `limit` 只控制 worker 单轮处理上限，不代表业务并发。
- `RESOURCE_WAIT` 表示资源暂忙；用户应查看 `resource_kind`、`resource_key`、首次等待、最近等待和等待次数。
- `WORKLINE_ENTRY_ADMISSION_BLOCKED` 不再是新运行过程的正常诊断。
- 内部 MOCK/Sandbox 文档必须展示多物料并行观察步骤，不再把 worker `limit` 或旧 `parallelism` 写成业务并发开关。
- Runtime workflow guide 必须区分 Inbox `RESOURCE_WAIT` 和 Outbox `BLOCKED_RESOURCE`，并明确二者都可展示为资源等待但写入边界不同。

- [ ] **Step 2: 更新 SPEC 状态和职责边界**

在 `docs/superpowers/specs/2026-06-09-workline-resource-constrained-parallelism-spec.md` 中把状态更新为当前实现态：

```text
状态：已实现 - 待 PostgreSQL-backed claim/EXPLAIN 集成门禁最终确认
```

SPEC 不维护 `GSTACK REVIEW REPORT`、Implementation Tasks 或 Failure Modes；这些内容只保留在本文，避免合同文档和执行计划重复维护后漂移。

- [ ] **Step 3: 运行文档旧术语门禁**

```bash
rg "WORKLINE_INBOX_BATCH_PARALLELISM|WORKLINE_INBOX_BATCH_MAX_PARALLELISM|INBOX_BUCKET_LOCK_TTL_SECONDS|工作线一次只能跑一个|一次只能跑一个 SESSION|parallelism|entry-admission|WORKLINE_ENTRY_ADMISSION_BLOCKED" \
  docs/hardware/粗分机硬件供应商联调操作手册.md \
  docs/hardware/粗分机内部Mock与Sandbox调试手册.md \
  docs/workline_diagnostics_quickstart.md \
  docs/business/workline_runtime_workflow_guide.md
```

期望：无命中，或只命中明确说明“旧行为已删除/不再作为正常路径”的段落；不允许用户操作文档继续指导旧串行、旧 `parallelism` 调参或旧 entry-admission 诊断。

- [ ] **Step 4: 运行格式和 focused tests**

```bash
uv run ruff format <changed-python-files-only>
uv run ruff check <changed-python-files-only>
uv run pytest \
  tests/workline_runtime/test_runtime_intent_contract.py \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_workline_constants.py \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_inbox_service.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_inbox_claim_plan.py \
  tests/workline_runtime/test_outbox_dispatch_service.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_orchestrator_write_back_service.py \
  tests/workline_runtime/test_resource_wait_evidence.py \
  tests/workline_runtime/test_workline_diagnostic_service.py \
  tests/workline_runtime/test_integration_debug_service.py \
  tests/workline_runtime/test_diagnostics_builder.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py \
  tests/workline_runtime/test_celery_task_entrypoints.py \
  tests/workline_runtime/test_celery_internal_signals.py \
  tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py \
  tests/integration/workline_runtime/test_real_mock_driven_sandbox_e2e.py -q
```

期望：format/check 无错误；focused tests 全部通过或 PostgreSQL-only plan gate 明确 skip。

`<changed-python-files-only>` 必须来自实施后 `git diff --name-only -- '*.py'` 的 Python 文件清单；不要对整个 `src tests` 目录运行自动格式化，避免带入无关格式 churn。

PostgreSQL-backed claim/EXPLAIN 子集必须按 Task 5 的 `RUN_WORKLINE_INTEGRATION=1` / `INTEGRATION_DATABASE_URL` 命令留下通过记录；SQLite skip 只能作为本地开发降级说明，不能作为最终通过依据。

- [ ] **Step 5: 运行全量测试**

```bash
uv run pytest tests/
```

期望：全量通过。

- [ ] **Step 6: GitNexus detect changes**

在 GitNexus MCP 中运行：

```text
detect_changes(repo="wes_backend", scope="all")
```

通过标准：

- changed symbols 与本计划文件列表一致。
- 影响流程集中在 WorkLine runtime、Inbox worker、diagnostics、SMT sorting resource wait。
- 没有意外 API 层直连数据库或跨层调用。

- [ ] **Step 7: 单次最终提交**

先运行：

```bash
git status --short
```

只暂存本计划实际修改/新增的文件、Alembic 生成的迁移文件、对应测试文件和本次文档/TODO 更新。实施者必须先按 `git status --short` 逐项核对，再用精确文件路径暂存；禁止使用 `git add -A`、`git add src/`、`git add tests/workline_runtime` 这类目录级暂存。

预期变更类别：

- WorkLine runtime 合同：constants、RuntimeIntent、effect/write-back、SessionResolver、Inbox repository/service/processor、diagnostic、SMT sorting plugin。
- 数据库变更：Alembic generator 生成的 `workline_inbox.claim_bucket_key` migration。
- 测试变更：本计划列出的 focused runtime tests、PostgreSQL-backed claim/EXPLAIN tests、MOCK/E2E tests。
- 文档变更：粗分机联调手册、诊断 quickstart、runtime workflow guide、SPEC、PLAN、必要 TODO 更新。

提交信息：

```bash
git commit -m "feat(workline): 实现资源约束并发"
```

## 自检结果

- SPEC 覆盖：入口 blocker 删除、RESOURCE_WAIT、Inbox claim、parallelism 删除、诊断幂等、测试/E2E、文档更新均有任务覆盖。
- 计划可执行性：已补齐 `What already exists`、`NOT in scope`、`Failure modes`、`Planned Test Coverage Diagram`、`Implementation Tasks` 和 `Worktree parallelization strategy`，SPEC 只保留合同和验收，避免执行清单重复维护。
- 终审补强：`WriteBackDisposition` 中立合同模块 `src/workline_runtime/effect_result.py`、`RESOURCE_RETRY` 的 `processed + resource_wait` 统计语义、claim 后冻结 `claim_bucket_key`、direct Inbox writer 矩阵、重复 `DataTableMixin` 清理、PostgreSQL-backed claim/EXPLAIN 强制门禁、统一 `WorklineInbox` 测试构造 helper、连续 `RESOURCE_WAIT` 旧 ACTIVE 诊断关闭规则均已写入任务与验收。
- 占位符扫描：只保留 Alembic generator 生成迁移文件名的 `<alembic_generated>` 占位；其它任务都有文件、测试、命令和验收。
- 类型一致性：统一使用 `RuntimeIntentKind.RESOURCE_WAIT`、`RuntimeIntent.resource_wait()`、`WORKLINE_RESOURCE_WAIT_RETRY_SECONDS`、`current_wait_type="RESOURCE_WAIT"`、`resource_kind`、`resource_key`。
- 项目规则：计划文档没有粘贴完整类、完整函数或大段测试代码；实现阶段仍按 TDD 先写失败测试。
- 变更卫生：最终质量门禁按变更 Python 文件精确 format/check，提交前按 `git status --short` 精确暂存，禁止目录级 `git add` 带入无关 churn；计划文档只保留暂存规则，不维护易过期的逐文件 `git add` 脚本。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES_OPEN | 近期 dashboard 记录来自 2026-06-03：1 个未决，0 个 critical gap。 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | NOT RUN | 本计划没有近期 Codex plan review。 |
| Eng Review | `/plan-eng-review` + `$systematic-debugging` | Architecture & tests (required) | 9 | CLEAR (DOCS) | 本轮文档复审修正已落地：direct writer 覆盖矩阵、重复 `DataTableMixin` 清理、PostgreSQL-backed claim/EXPLAIN 强制命令、用户文档旧术语门禁和 `GSTACK REVIEW REPORT` 更新均已写入计划；0 个未决，0 个 critical gap。 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | NOT REQUIRED | 后端 runtime/worker 计划，无 UI 变更。 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | ISSUES_OPEN | 近期 dashboard 记录来自 2026-06-03：评分 6/10 -> 7/10，3 个未决。 |

- **UNRESOLVED:** 当前 Eng Review 0；近期非必需 CEO/DX review 共 4 个旧未决。
- **VERDICT:** ENG DOCS CLEARED，可以进入代码实现；本次文档修正已应用到 PLAN/SPEC，TODOS.md 仅保留后续监控与 benchmark，不新增当前实现项。
