# WorkLine 资源约束并发 SPEC

> 状态：已实现 - PostgreSQL-backed claim/EXPLAIN 集成门禁已确认
> 日期：2026-06-09
> 兼容策略：当前系统未发布，按破坏性优化处理，不保留旧入口准入兼容合同。
> 关联背景：
>
> - `docs/hardware/粗分机硬件供应商联调操作手册.md`
> - `docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`
> - `src/workline_runtime/session_resolver.py`
> - `src/app/workline/services/station_lease_service.py`
> - `src/app/workline/services/inbox_batch_processor.py`
> 文档职责：本文是目标、架构决策、业务约束和验收标准的合同真源；执行任务、失败模式清单和评审状态以
> `docs/superpowers/plans/2026-06-09-workline-resource-constrained-parallelism.md` 为准，避免两份文档重复维护后漂移。

## 1. 背景

粗分机 MOCK 联调时，连续压入多个 `SCAN_COMPLETED` 后观察到两个问题：

- 同一条工作线单位时间只能推进一个 `SESSION`。
- 后续物料被记录为 `WORKLINE_ENTRY_ADMISSION_BLOCKED`，等待前一个不同物料 `SESSION` 结束后重试。

代码调查确认，当前限制不是设备硬件能力或 MOCK 延迟造成的，而是运行时入口准入模型造成的：

- `SessionResolver` 在创建新物料 `SESSION` 前，对 `workline-entry-admission:{workline_id}` 加工作线级事务锁。
- `WorklineSessionRepository.get_open_entry_blocker_for_workline()` 会查询同一 `workline_id` 下其它未结束 `SESSION`。
- 只要存在不同 `business_key` 的 open session，就抛出 `WorklineEntryAdmissionBlocked`。

这与物理流水线模型不一致。真实工作线的并发能力不应该由“同一工作线只能有一个 open session”决定，而应由设备、工位、货架、料箱和物理缓存位的占用状态决定。

## 2. 目标

本 SPEC 的目标是将 WorkLine runtime 从“工作线级 SESSION 串行”改为“设备/工位资源约束并发”。

目标行为：

- 同一条 `workline` 可以同时存在多个 open `SESSION`。
- `SCAN_COMPLETED` 仍按进入 Inbox 的顺序进入工作线。
- Device fence 与 Station lease 是分层资源边界：设备命令和 Outbox `BLOCKED_RESOURCE` 负责设备互斥，Station lease 只负责物理操作站位互斥。
- 本地命令完成只更新诊断投影，真实 dispatch 放行必须由下一轮 ECS 实时 `IDLE` probe 决定。
- 同一物理设备、同一 Station、同一 active rack 相关操作仍必须互斥。
- 物料在同一物理资源上的等待顺序必须保持 FIFO。
- 工作线业务容量不通过环境变量配置，而由真实资源状态自然决定。
- 资源忙时进入结构化 `RESOURCE_WAIT`，而不是转入人工 `MANUAL_HOLD`。

## 3. 非目标

本阶段不做以下事项：

- 不新增 `entry_sequence` 字段；入口 FIFO 使用现有 `received_at ASC, id ASC`，并在 Inbox claim 返回结果中显式排序。
- 不新增通用 `resource_lease` 数据表；第一版复用现有设备命令、Outbox、Station lease、rack/bin reservation 机制。
- 不把 WES 改造成全局 WMS/RCS 资源调度器。
- 不新增 WES API。
- 不做前端大改；只要求运行诊断不再产生新的入口准入阻塞污染。
- 不解决所有复杂路径上的超车策略；线性粗分机路径默认保持物料 FIFO。
- 不把设备 dispatch 阶段的 busy 状态改回 Inbox retry；设备命令副作用仍由 Outbox 围栏托管。
- 不保留旧 `WorklineEntryAdmissionBlocked`、pending entry-admission debug 路径或 Celery 历史 alias。

## 4. 核心决策

### 4.0 终审确认

2026-06-09 Eng Review 后锁定以下实现决策，后续实施不得再隐式改回旧模型：

- `claim_bucket_key` 使用应用层普通列物化，并作为 `WorklineInboxRepository` 持久化兜底不变量：`InboxService` create/timeout、`OperationService` replay/manual/sandbox event/sandbox external callback/sandbox command result、`RuntimeHoldReleaseService` continue-result 等直接或间接写入路径都不能漏写；不使用数据库生成列，也不继续在 claim 热路径从 `payload_json` 动态推导；migration backfill 后必须设置 `NOT NULL`；消息进入 `PROCESSING` 或写入 `processor_token` 后该 key 冻结。
- `WriteBackDisposition` 必须定义在 runtime 中立合同模块（例如 `src/workline_runtime/effect_result.py`）：至少区分 `PROCESSED` 与 `RESOURCE_RETRY`；`RESOURCE_WAIT` 是正常业务等待，不通过异常、写后读状态或可变 `ctx` 标记隐式表达。
- `RESOURCE_RETRY` 的 batch result 统计必须表达“已处理但等待资源”：计入 `processed` 与显式 `resource_wait` 计数，不计入 `success`、`failed` 或 `skipped`。
- `RESOURCE_WAIT` 是等待型终止 intent；同一轮 intent list 中不能再继续执行后续 command-producing intent，也不能跟在 command-producing intent 之后。
- 设备 dispatch 放行事实源是 ECS 实时 `IDLE` probe；WES 本地 `DeviceStatus`、`current_command_id` 和 command terminal 状态只作为诊断/业务投影，不得释放 `BLOCKED_RESOURCE` outbox。
- 单个 `InboxBatchProcessor` 顺序 claim 和处理；多 worker 并发只能由数据库 token claim、`claim_bucket_key` 和同 bucket 队首围栏承载，不恢复 batch 内 `asyncio.gather`、Redis bucket lock 或 wave 调度。
- 用户可观察并发来自多 open session、多 worker PostgreSQL claim、Outbox/device callback 和真实资源释放；不是来自单个 batch 内的并发调度。
- `WorklineInbox` 影响面为 GitNexus CRITICAL；实施时必须覆盖 migration/backfill、测试 fixture、claim 查询、integration tests 和最终 `detect_changes`。
- PostgreSQL-backed 集成测试是并发语义验收门槛；SQLite 只能覆盖分支逻辑，不能证明 `SKIP LOCKED`、队首围栏和 partial index 行为。实现者本地没有 PostgreSQL 时可以 skip 并记录原因，但最终交付或 CI 必须通过现有 integration fixture（`RUN_WORKLINE_INTEGRATION=1` + `INTEGRATION_DATABASE_URL`）跑 PostgreSQL claim 并发与 `EXPLAIN` 门禁，不能把 SQLite-only 结果视为通过。

### 4.1 移除工作线级入口 blocker

`SessionResolver` 不再因为同一 `workline_id` 下存在其它 open `SESSION` 而阻塞新物料。

保留的规则：

- 同一 `business_key` 的重复入口、迟到入口、重放入口仍复用已有或最新 `SESSION`。
- 缺少稳定业务键的入口事件仍应失败，不允许随机建单。
- 同一 `workline_id + business_key` 的查建窗口必须保留或复用事务/advisory lock，避免重复建同一物料 `SESSION`。

移除的规则：

- 不再使用 `get_open_entry_blocker_for_workline()` 阻止不同物料建 `SESSION`。
- 不再为新物料入口创建 `WORKLINE_ENTRY_ADMISSION_BLOCKED` 诊断。
- 不再把后续不同物料 Inbox park 到 `RETRY` 等前一个物料完成。
- 删除 `WorklineEntryAdmissionBlocked` 新路径、pending entry-admission debug 路径和相关历史 alias；历史数据只作为普通历史记录自然展示，不保留专用兼容逻辑。

### 4.2 并发边界下沉到资源

工作线并发应由真实物理资源决定。

设备不是 Station lease 的别名。设备级互斥继续复用 device command / Outbox governance；Station lease 只覆盖 Station scope 的外部派发和单层货架相关站位占用。实现时不得为了概念统一给每个设备套 Station lease，也不得把设备 busy 从 Outbox `BLOCKED_RESOURCE` 回退成 Inbox `RESOURCE_WAIT`。

资源边界：

| 资源 | 并发规则 | 现有机制 |
| --- | --- | --- |
| 设备 | 同一设备同一时刻最多一个 active command | device command / outbox dispatch governance |
| Station | 同一 Station 同一时刻最多一个 active dispatch lease | `WorklineStationLeaseService` |
| 单层 active rack | 同一 active rack 操作按 rack/station 约束互斥 | single-layer rack orchestration |
| 料箱/格位容量 | 按容量、批次、料盘占用判断是否可放置 | rack/bin scheduling 与 reservation |
| WMS/RCS 外部任务 | 同一 operation/station 不重复派发 | EXTERNAL_HTTP outbox + dispatch key |

如果某个 `SESSION` 的下一步资源不可用，该 `SESSION` 必须进入结构化资源等待；不能阻止其它 `SESSION` 在其它空闲资源上推进。

设备资源释放必须区分两层事实：

- WES 本地 command terminal、`DeviceStatus=IDLE` 或 `current_command_id=None` 只说明本地投影已经更新，可用于 trace、monitor 和诊断展示。
- `BLOCKED_RESOURCE` outbox 的重新派发必须由 Outbox dispatch 下一轮 ECS status probe 返回 `IDLE` 后放行；本地投影不得直接把 blocked outbox 改回可派发状态。

### 4.3 资源等待语义

资源等待是自动等待态，不是人工异常态。

要求：

- 新增 `RuntimeIntentKind.RESOURCE_WAIT`，用于表达编排阶段已经知道的 Station、rack/bin/cell 等真实资源暂不可用。
- 插件、Station lease、rack/bin 调度只产生 `RESOURCE_WAIT` intent，不直接修改 Inbox 状态。
- 设备命令创建阶段继续允许 busy 设备进入 Outbox；设备 dispatch/precheck 忙时由 Outbox `BLOCKED_RESOURCE` 围栏处理，不转成 Inbox `RESOURCE_WAIT` retry。
- UI/trace 可以把 Inbox `RESOURCE_WAIT` 与 Outbox `BLOCKED_RESOURCE` 都展示为资源等待，但运行时写入边界必须分层。
- effect 层处理 `RESOURCE_WAIT` 时只写 Session 等待态、wait context 和诊断证据，并沿 `RuntimeIntentEffectApplier.apply()` → `OrchestratorWriteBackService.write_back()` → `InboxBatchProcessor` 返回 runtime 中立 `WriteBackDisposition`；最终由 `InboxBatchProcessor` 统一决定 `park_for_retry()` 或 `mark_as_processed()`。
- effect 层统一处理 `RESOURCE_WAIT`：
  - Session 状态复用 `WAITING_EXTERNAL`。
  - `current_wait_type` 写入 `RESOURCE_WAIT`。
  - wait context 写入 `resource_kind`、`resource_key`、阻塞原因和可见证据。
  - 返回 `RESOURCE_RETRY` disposition，由 `InboxBatchProcessor` 将当前 Inbox message park 到 `RETRY`，并在 batch result 中计为 `processed + resource_wait`。
- 新增 `WORKLINE_RESOURCE_WAIT_RETRY_SECONDS`，默认 `10` 秒；该间隔可配置，用于后续按现场节奏调优，但不表达业务容量。
- 资源等待重试不增加 `attempt_count`，避免把“资源暂忙”误判为消息处理失败。
- `RESOURCE_WAIT` 不进入 `MANUAL_HOLD`，也不创建 Runtime Hold；只有真实异常或人工介入场景才进入 Hold。
- 缺少 `resource_key` 或关键资源证据时视为实现错误并明确失败，不降级成泛化“工作线忙”。
- `RESOURCE_WAIT` evidence 必须由 `src/workline_runtime/resource_wait_evidence.py` 中的唯一轻量 helper/model 统一生成和合并，记录 `inbox_id`、`resource_kind`、`resource_key`、`reason_code`、首次等待时间、最近等待时间和等待次数；diagnostic key、session context 和 diagnostic evidence 都必须来自该 helper/model。
- 同一 Inbox 的 RESOURCE_WAIT retry 允许绕过重复入口归档门禁，但仅限 `current_wait_type=RESOURCE_WAIT` 且 wait context 中的 `inbox_id` 等于当前 Inbox。
- 资源恢复并成功推进后，必须将同一 `inbox_id + resource_key` 的 ACTIVE RESOURCE_WAIT 诊断置为已解决。
- 同一 Inbox 从资源 A 恢复后又等待资源 B 时，必须先将资源 A 的 ACTIVE RESOURCE_WAIT 诊断置为已解决，再记录资源 B 的 ACTIVE 等待诊断。

诊断证据按 `inbox_id + resource_key` 幂等更新，记录首次等待时间、最近等待时间和重试次数，避免每次重试重复追加相同诊断。

### 4.4 FIFO 使用 Inbox 顺序

入口顺序来自 Inbox 本身：

```text
received_at ASC, id ASC
```

因此不需要新增显式 `entry_sequence`。

要求：

- `claim_pending_messages()` 是唯一 Inbox 消费入口；旧 `get_new_messages` 消费路径删除。
- `claim_pending_messages()` 返回或处理时必须保持入口 FIFO，并使用 token claim 防止重复处理。
- 当前 processor 顺序处理模式每次只 claim 1 条消息；repository 可保留 `claim_pending_messages(limit=N)` 能力用于数据库围栏、多 worker 和后续扩展。
- processor 不得在顺序模式下预先 claim `limit` 条消息后再逐条处理，避免 worker 崩溃后 stale reclaim 尚未真正开始处理的消息。
- 保留数据库级同 bucket 队首围栏，防止同一物理资源后序消息越过前序消息。
- bucket 只作为数据库 claim 围栏键，不代表业务并发容量；不得恢复 Redis bucket lock 或 wave 调度。
- 在线性粗分机路径中，后序物料不能越过前序物料占用同一设备或同一工位。
- 当前序物料失败、取消或进入人工 HOLD 时，后续物料是否继续推进由运行态策略决定，并必须产生明确诊断。

### 4.5 并发层级边界

本 SPEC 不再把 worker batch 内并发当作业务并发能力。并发边界分三层：

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

要求：

- 业务容量只来自真实资源状态，不来自 Celery task 参数或环境变量。
- 多 worker 并发由 PostgreSQL `SKIP LOCKED`、`processor_token` 和 `claim_bucket_key` 队首围栏承载。
- 单个 `InboxBatchProcessor` 不做 batch 内 `asyncio.gather`，不恢复 Redis bucket lock，不恢复 bucket wave 调度。
- `limit` 只控制单轮最多处理多少条 Inbox，不表达业务并发和资源容量。

### 4.6 删除 `WORKLINE_INBOX_BATCH_PARALLELISM`

`WORKLINE_INBOX_BATCH_PARALLELISM` 不再保留。

原因：

- 它表达的是 Inbox worker 消费并发，不是工作线业务容量。
- 当前讨论中的工作线并发应来自设备/工位资源，而不是消费者参数。
- 保留该参数会继续误导联调人员，把“工作线能跑几个物料”理解成环境变量配置。

调整要求：

- 删除 `WORKLINE_INBOX_BATCH_PARALLELISM` 和 `WORKLINE_INBOX_BATCH_MAX_PARALLELISM`。
- `process_inbox_batch` 不再接收 `parallelism` 参数。
- `InboxBatchProcessor` 不再做 bucket 并发 wave 调度；只保留按 FIFO claim 和处理。
- `limit` 只表示单轮最多处理多少条 Inbox，不表达并发。
- 删除 `INBOX_BUCKET_LOCK_TTL_SECONDS` 以及依赖 Redis bucket lock 的 stale 时间约束；`WORKLINE_INBOX_PROCESSING_STALE_SECONDS` 的下限只需要覆盖单消息处理超时和安全余量。

### 4.7 `claim_bucket_key` 物化策略

`claim_bucket_key` 是 Inbox claim 的数据库冲突域键，只服务于 worker claim 围栏，不表达业务容量。

落地要求：

- 字段形态：`workline_inbox.claim_bucket_key` 普通列，由应用层统一 helper 生成。
- 生成优先级：`session_id` > `device_id` > `device_code/location` > `workline_id` > `serial:unknown`，保持与当前 bucket 语义一致。
- 持久化边界：`WorklineInboxRepository` 是最终 guard，`create()`、`create_idempotent()` 和 claim 前归属字段纠偏路径必须在入库前补齐或重算 `claim_bucket_key`；`InboxService` 可以传值但不复制生成规则。
- 生命周期：一旦 Inbox message 进入 `PROCESSING` 或写入 `processor_token`，`claim_bucket_key` 不得再自动重算；`RETRY/PROCESSED` 状态更新只改变处理状态和 retry 元数据，不移动 bucket。
- 直接写入路径：所有绕过 `InboxService` 或在 service 内直接调用 repository 的写入形态，也必须由 repository 兜底写入 `claim_bucket_key`；现有矩阵至少包括 `InboxService` create/timeout、`OperationService` replay/manual/sandbox event/sandbox external callback/sandbox command result、`RuntimeHoldReleaseService` continue-result。
- migration：先以 nullable 增加字段并 backfill 全量历史消息，验证不存在 `claim_bucket_key IS NULL` 后设置 `NOT NULL`，最后建立 hot queue partial index；backfill 表达式与应用层 helper 必须通过同一 case matrix 验证优先级一致；模型修改时同步清理 `WorklineInbox` 重复 `DataTableMixin`。
- 查询：`claim_pending_messages()` 只能使用 `claim_bucket_key` 列参与同 bucket 队首围栏；不得在热路径重复写 JSON 表达式。
- 索引验收：PostgreSQL `EXPLAIN` 必须按访问路径证明 claimable 队列排序、同 bucket 队首围栏、到期 `RETRY` 和 stale `PROCESSING` 回收使用匹配的 hot queue partial index；不使用“小样本无 Seq Scan”作为误报式断言。
- 测试：必须覆盖创建路径、backfill 后无 NULL、backfill/helper 优先级一致、模型字段非空、重复 mixin 清理、direct writer matrix、同 bucket 前序 `PROCESSING` 阻塞、不同 bucket 多 worker claim，以及 PostgreSQL `EXPLAIN` 计划。

### 4.8 Inbox disposition 合同

`RESOURCE_WAIT` 的 effect 层只负责写 Session 等待态、wait context 和诊断证据，不直接写 Inbox 终态。

落地要求：

- `RuntimeIntentEffectApplier.apply()` 返回 runtime 中立小型结果对象或 enum，至少包含 `PROCESSED` 与 `RESOURCE_RETRY`；该合同不得定义在 app service 层。
- `OrchestratorWriteBackService.write_back()` 必须直接返回 effect applier 的 disposition，不得把结果藏在 `ctx` 或 `orch_result`。
- `InboxBatchProcessor` 是唯一写入当前 Inbox 终态的组件：`PROCESSED` 调 `mark_as_processed()`，`RESOURCE_RETRY` 调 `park_for_retry()`。
- `RESOURCE_RETRY` 不增加 `attempt_count`，不进入 `DEAD_LETTER`，不被后续 `mark_as_processed()` 覆盖，batch result 计入 `processed + resource_wait`。
- 不把正常资源等待建模成异常；异常只保留给真实实现错误、数据错误或外部调用失败。

## 5. 运行时数据流

目标数据流：

```text
SCAN_COMPLETED callback
        |
        v
WorklineInbox(按 received_at/id 排队)
        |
        v
SessionResolver
  - 同 business_key 复用 session
  - 新 business_key 创建新 session
  - 不检查同 workline 其它 open session
        |
        v
Orchestrator
  - 判断当前 session 下一步
  - 生成设备命令或外部任务
        |
        v
Resource fence
        |
        +--> 资源可用
        |       |
        |       v
        |   Outbox / Device command / External callback
        |       |
        |       v
        |   Session 继续推进或终态
        |
        +--> 资源忙
                |
                v
            RESOURCE_WAIT effect
              - Session: WAITING_EXTERNAL + current_wait_type=RESOURCE_WAIT
              - Diagnostic: 按 inbox_id + resource_key 幂等更新
              - Disposition: RESOURCE_RETRY
                |
                v
            InboxBatchProcessor
              - park Inbox RETRY
              - next_retry_at = now + WORKLINE_RESOURCE_WAIT_RETRY_SECONDS
                |
                v
            Inbox retry resume
```

用户可观察结果：

- 连续压入多个物料后，运行态中可以看到多个 open `SESSION`。
- 每个设备仍只处理一个当前命令。
- 当上游设备完成当前命令后，下一个物料可以进入该设备。
- 不同设备可以同时处理不同 `SESSION` 的不同步骤。

## 6. 诊断语义

删除新产生的入口准入阻塞诊断：

- `WORKLINE_ENTRY_ADMISSION_BLOCKED` 不再作为正常并发控制诊断产生。
- 不保留 pending entry-admission 专用 debug/diagnostic 兼容路径；旧历史记录只通过通用历史查询自然存在。

新增或复用资源等待诊断：

- 设备忙：dispatch 阶段诊断继续指向 Outbox `BLOCKED_RESOURCE`，包含具体 `device_id/device_code`、active command、active session。
- Station 忙：诊断应指向 `position_code`、active dispatch key、active session。
- Rack/bin/cell 容量不足：诊断应指向具体 rack/bin/cell 和容量证据。
- FIFO 等待：诊断应说明等待的前序资源请求，而不是泛化为工作线忙。

诊断原则：

> 诊断必须说明“哪个物理资源阻塞了哪个 SESSION 的哪一步”，不能只说“工作线忙”。

诊断写入要求：

- 设备忙：Outbox `BLOCKED_RESOURCE` 证据至少包含 `resource_kind=DEVICE`、`device_id/device_code`、active command、active session。
- Station 忙：至少包含 `resource_kind=STATION`、`position_code`、active dispatch key、active session。
- Rack/bin/cell 容量不足：至少包含 `resource_kind`、`resource_key` 和容量证据。
- 同一 `inbox_id + resource_key` 的重复等待只能更新同一条证据，不能刷屏式追加。

## 7. 方案比较

### 方案 A：复用资源围栏

移除工作线级 blocker，复用现有设备命令治理、Station lease、rack/bin scheduling 与 reservation。

优点：

- 改动范围最小。
- 符合现有单层货架编排边界。
- 能最快修正粗分机联调中“只能一个 SESSION”的问题。

缺点：

- 资源等待 FIFO 需要在现有 outbox/command 队列中补足测试。
- 长期通用性依赖现有资源上下文是否足够结构化。

结论：采用本方案。

### 方案 B：新增通用资源租约表

新增 `resource_lease` 模型，统一表达设备、工位、货架、缓存位租约。

优点：

- 长期模型更统一。
- 可清晰表达资源等待队列和释放顺序。

缺点：

- 需要新表、新迁移、新服务和大量回归。
- 当前问题不需要这么大的架构改造。
- 容易与已有 Station lease 和 outbox governance 重叠。

结论：暂不采用。

### 方案 C：虚拟子工作线

把一条物理工作线拆成多个逻辑 workline，以绕开当前全局 blocker。

优点：

- 可短期规避单 workline blocker。

缺点：

- 与真实物理线建模相反。
- 会破坏 trace、诊断、设备归属和联调文档语义。
- 后续仍要回到资源级并发模型。

结论：不采用。

## 8. 验收标准

### 8.1 单元测试

必须覆盖：

- 多个不同 `business_key` 的 `SCAN_COMPLETED` 可在同一 `workline` 创建多个 open `SESSION`。
- 同一 `business_key` 仍复用已有 open session。
- 同一 `business_key` 已终态后，迟到/重复入口仍按现有归档规则处理，不误建新单。
- 不再调用 `get_open_entry_blocker_for_workline()` 阻塞不同物料。
- 不再抛出 `WorklineEntryAdmissionBlocked` 作为正常入口等待。
- `process_inbox_batch` 不再接收或传递 `parallelism`。
- `RuntimeIntent.resource_wait()` / `RuntimeIntentKind.RESOURCE_WAIT` 校验必填资源字段。
- `RESOURCE_WAIT` 必须是本轮最后一个 intent，`[RESOURCE_WAIT, COMMAND]` 和 `[COMMAND, RESOURCE_WAIT]` 都必须被合同校验拒绝。
- `RESOURCE_WAIT` effect 写入 `WAITING_EXTERNAL`、`current_wait_type=RESOURCE_WAIT`，不进入 `MANUAL_HOLD`。
- `RESOURCE_WAIT` effect 返回 `RESOURCE_RETRY` disposition；`RuntimeIntentEffectApplier.apply()`、`OrchestratorWriteBackService.write_back()` 和 `InboxBatchProcessor` 三层都必须显式传递/消费该 disposition；Inbox `RETRY` 状态只由 `InboxBatchProcessor` 写入，避免后续 `mark_as_processed()` 覆盖。
- `RESOURCE_RETRY` 在 batch result 中计入 `processed + resource_wait`，不计入 `success`、`failed` 或 `skipped`。
- 普通成功 write-back 仍由 `InboxBatchProcessor` 写入 `PROCESSED`。
- 同一 `inbox_id + resource_key` 重复等待只更新一条诊断证据，并递增等待次数。
- 同一 Inbox 从资源 A 转为等待资源 B 时，资源 A 的 ACTIVE 诊断必须被置为已解决，资源 B 记录为当前 ACTIVE 等待。
- `RESOURCE_WAIT` evidence helper/model 覆盖必填字段、diagnostic key、`first_seen_at` 保留、`last_seen_at` 更新、`wait_count` 递增和 session/diagnostic 输出一致性；diagnostic service 不得手写第二套 key 或 evidence 拼接规则。
- `WORKLINE_RESOURCE_WAIT_RETRY_SECONDS` 默认 10 秒，资源等待 park 到 `RETRY` 时使用该配置；env 覆盖必须生效。
- 同一 Inbox 的 RESOURCE_WAIT retry 可绕过重复入口归档门禁，非 RESOURCE_WAIT 的重复入口仍按原归档规则处理。
- RESOURCE_WAIT retry 成功推进后，同一 `inbox_id + resource_key` 的 ACTIVE 诊断被置为已解决。
- 同一设备 active command 未完成时，后续命令不下发；该等待停在 Outbox `BLOCKED_RESOURCE`，不 park 当前 Inbox。
- 本地 `DeviceStatus=IDLE`、`current_command_id=None` 或 command terminal 不释放 `BLOCKED_RESOURCE` outbox；只有 ECS status probe 返回 `IDLE` 才允许重新 claim/dispatch。
- 同一 Station active dispatch lease 未完成时，后续 station dispatch claim 返回等待。
- 同 bucket `PROCESSING` 时，后序 Inbox 不可被 claim。
- processor 顺序处理模式只 claim 1 条消息，不预先 claim `limit` 条再逐条处理。
- `claim_pending_messages(limit=N)` 即使数据库 `RETURNING` 乱序，也必须按 `received_at, id` 返回 claim 结果。
- Inbox 增加物化 `claim_bucket_key`，入队/更新时统一生成；claim 查询使用该字段和索引，不在热路径重复从 JSON payload 推导 bucket。
- `claim_bucket_key` migration backfill 后必须设置 `NOT NULL`，测试验证历史数据和新写入路径均无 NULL。
- `claim_bucket_key` migration backfill 必须和应用层 helper 通过同一 case matrix 验证优先级一致，覆盖 `session_id > device_id > device_code/location > workline_id > serial:unknown`。
- `claim_bucket_key` 在入库和 claim 前纠偏时补齐/重算；消息进入 `PROCESSING` 或写入 `processor_token` 后冻结。
- direct writer matrix 中的所有写入路径都必须自动获得 `claim_bucket_key`，包括 `InboxService` create/timeout、`OperationService` replay/manual/sandbox event/sandbox external callback/sandbox command result、`RuntimeHoldReleaseService` continue-result。
- 测试侧直接构造 `WorklineInbox` 的路径必须通过统一 helper/factory 默认生成 `claim_bucket_key`；只有验证异常数据时才允许显式覆盖为 NULL 或特殊 key。
- claim 查询增加 PostgreSQL `EXPLAIN ANALYZE` 稳定性门禁，使用足够种子数据断言各 hot queue access path 的 partial index 生效且锁范围不扩大；SQLite 明确跳过该门禁。SQLite skip 只允许作为本地开发降级，最终交付或 CI 必须提供 `RUN_WORKLINE_INTEGRATION=1` + `INTEGRATION_DATABASE_URL` 的 PostgreSQL-backed 结果。

### 8.2 集成测试

必须覆盖：

- 连续压入 5 个不同物料，系统生成多个 open session。
- MOCK 设备随机 2-8 秒回调期间，不同设备可同时处于 `RUNNING`。
- 同一设备不会同时出现两个 active command；设备忙保持 Outbox `BLOCKED_RESOURCE`。
- 第一个物料的入口设备在 ECS status probe 返回 `IDLE` 后，第二个物料可进入入口设备；仅本地投影 IDLE 不得触发派发。
- 全流程完成后，没有残留 `ACTIVE` 的入口准入阻塞诊断。
- 当资源真的不可用时，诊断指向具体设备、Station 或容量证据。
- 资源忙期间，重复重试不会产生多条重复诊断，也不会递增消息失败次数。
- 资源先 busy 后释放时，同一 Inbox 从 `RETRY` 重新推进，成功后对应 RESOURCE_WAIT 诊断置为已解决。

### 8.3 curl + MOCK 联调

按 `docs/hardware/粗分机硬件供应商联调操作手册.md` 执行：

1. 清空测试数据并重置 MOCK。
2. 连续发送多个不同物料 `SCAN_COMPLETED`。
3. 观察 runtime monitor 或 trace：
   - 多个 session 并存。
   - 设备状态按命令运行、回调投影和 ECS status probe 分层展示。
   - 上游设备经 ECS `IDLE` probe 放行后可接收后续物料。
   - 不出现新的 `WORKLINE_ENTRY_ADMISSION_BLOCKED`。
4. 验证物料最终进入正确 rack/bin/cell。

## 9. 文档更新要求

实现完成后同步更新：

- 粗分机硬件供应商联调操作手册：
  - 删除“工作线一次只能跑一个 SESSION”的隐含说明。
  - 明确连续 `SCAN_COMPLETED` 会形成多个 session。
  - 明确并发由设备/工位状态决定。
- 内部 MOCK 与 Sandbox 调试手册：
  - 删除或改写 `WORKLINE_INBOX_BATCH_PARALLELISM` 相关说明。
  - 明确 worker batch `limit` 不是业务并发容量，单 processor 不做 batch 内并发。
  - 增加多物料并行观察步骤。
- 运行诊断说明：
  - 将入口阻塞诊断从正常路径中移除。
  - 强调资源级阻塞诊断。
  - 说明 `RESOURCE_WAIT`、`resource_key`、首次等待/最近等待/等待次数的含义。

## 10. 风险与约束

- 移除工作线级 blocker 后，设备/Station/resource fence 必须可靠，否则可能产生真实资源冲突。
- FIFO 不再由全局 blocker 简单保证，需要由 Inbox 顺序、数据库 claim 围栏和 RESOURCE_WAIT retry 共同保证。
- 如果某些插件在上下文中没有写入目标设备或 Station，资源等待诊断会变弱；实现时必须补齐关键上下文。
- 历史测试中显式断言 `WorklineEntryAdmissionBlocked` 的用例需要改写为多 session 准入成功或资源级等待。
- claim 查询如果 `claim_bucket_key` 索引失效或扩大锁范围，会把资源等待并发变成数据库瓶颈；必须由 `EXPLAIN` 门禁兜住。
- `claim_bucket_key` 如果允许 NULL，会让队首围栏和 partial index 退化成实现约定；migration 必须在 backfill 验证后设置 `NOT NULL`。
- `claim_bucket_key` backfill 如果和应用层 helper 规则漂移，会让历史消息和新消息落入不同冲突域；必须用 migration/helper 一致性测试兜住。
- `claim_bucket_key` 如果在 `PROCESSING` 后重算，会把已 claim 消息移动到新冲突域并破坏队首围栏；必须在 repository 合同和测试中冻结 claim 后 key。
- 资源等待如果立即重试，会形成热循环；默认 10 秒重试对齐 Inbox Beat，是本 SPEC 的行为合同。
- `RESOURCE_WAIT` 与 Inbox 状态如果由 effect 和 processor 双写，可能出现 `RETRY` 被 `PROCESSED` 覆盖；必须由 processor 单一写入 Inbox 终态，并由显式 `WriteBackDisposition` 控制。
- `RESOURCE_WAIT` 如果复用 `skipped` 统计，会把正常资源等待混入锁竞争/无效消息，导致 worker 指标不可解释；必须使用显式 `resource_wait` 计数。
- `RESOURCE_WAIT` 如果没有作为终止 intent 校验，资源忙后仍可能继续执行后续命令型 intent；必须用合同测试阻断。
- `RESOURCE_WAIT` evidence 或 diagnostic key 如果在 effect 和 diagnostic service 各自拼接，会造成字段漂移和诊断不一致；必须复用单一 helper/model。
- 设备 busy 如果被错误改为 Inbox `RESOURCE_WAIT`，可能重复创建命令或破坏 Outbox 副作用幂等边界；必须保留 Outbox 围栏测试。
- 本地设备投影如果被误当作 dispatch 放行事实源，可能在 ECS 仍 busy 时提前发送后续命令；必须保留 ECS probe 放行测试。
- 同一 Inbox 的 RESOURCE_WAIT retry 必须白名单穿过重复入口门禁，否则资源恢复后会被误归档为重复消息。
- RESOURCE_WAIT 成功恢复后必须关闭 ACTIVE 诊断，否则运行监控会保留已经失效的资源等待告警。

## 11. 默认假设

- `received_at, id` 是入口 FIFO 的可信顺序源。
- 每个命令型动作都能解析到明确目标设备。
- 设备 command terminal 和本地 `DeviceStatus` 不是 dispatch admission source；ECS 实时 `IDLE` 是 blocked outbox 放行事实源。
- 每个 Station/WMS 外部操作都能解析到明确 `position_code` 或 `operation_key`。
- 第一版不需要新建通用资源租约表。
- 第一版不需要新增 API。
- 第一版以粗分机 MOCK 联调作为通用运行时并发模型的首个验收场景。
- 现有 `WAITING_EXTERNAL` 足以承载 RESOURCE_WAIT；本阶段不新增 `WAITING_RESOURCE` 枚举和迁移。
