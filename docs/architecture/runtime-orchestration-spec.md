# Runtime/Orchestration 域最小骨架 SPEC

> 当前 Runtime/Orchestration 实体、服务与边界合同
> 父设计: `docs/architecture/workline-and-plugin-restructuring.md` §9.2

---

## 1. 概述

`runtime/orchestration` 是 WORKLINE + PLUGIN 体系重构后的**执行域**。它拥有 ExecutionSession 的会话状态所有权，通过 ExecutionCorrelation 提供跨域关联键，并通过 RuntimeIntentLog 记录出站副作用意图。

### 1.1 域定位

| 维度 | 说明 |
| ------ | ------ |
| 路径 | `src/app/runtime/orchestration/` |
| 数据库 schema | `wes_runtime` |
| 角色 | 唯一 session PK 拥有者；跨域 correlation key 提供者 |
| 不拥有 | WorkLine 配置（归 workline 域）、WMS 主数据（归 wms_integration）、设备物理状态（归 device 域） |

### 1.2 设计原则

- **CORRELATION OVER FK**: 跨域通过 `correlation_id` 关联，不扩散 `execution_session.id` 强 FK
- **SESSION AS AGGREGATE ROOT**: ExecutionSession 是聚合根，但不持有工作状态（工作状态归 ExecutionWorkItem）
- **INTENT LOG ≠ STATE SOURCE**: RuntimeIntentLog 是 effect proposal / outbox log，不是下游状态源
- **TYPED OVER UNTYPED**: 所有跨域交互使用 typed Pydantic 模型
- **BOUND GENERATED EXECUTION**: WorklineSession、ExecutionSession 与 ExecutionWorkItem 创建时固定完整 Plugin Binding pins；RuntimeInbox 只进入 generated dispatcher

---

## 2. 核心实体

### 2.1 实体关系图

```text
ExecutionSession (聚合根)
  ├── 1:N → ExecutionCorrelation (跨域关联键)
  ├── 1:N → ExecutionWorkItem (对象级执行令牌)
  ├── 1:N → RuntimeInbox (入站消息)
  ├── 1:N → RuntimeTimeline (时间线)
  ├── 1:N → RuntimeHold (暂停/冻结)
  └── 1:N → RuntimeIntentLog (出站意图记录)

IdempotencyKey (独立表)
  └── → ExecutionCorrelation (通过 execution_correlation_id 引用)

ConveyorQueueMembership (独立 active 投影)
  └── → ExecutionCorrelation (通过 correlation_id 引用)
```

### 2.2 ExecutionSession — 会话聚合根

**文件**: `src/app/runtime/orchestration/execution_session.py`
**表**: `wes_runtime.execution_sessions`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `workline_id` | int | WorkLine 逻辑归属与查询索引；不建立跨 schema WorkLine FK |
| `manifest_version` | str(60) | RUNNING session 固定 manifest 版本（CEO-011） |
| `plugin_key` | str(100) | generated plugin 固定 identity |
| `plugin_binding_id` / `plugin_binding_version` | int | immutable binding 主键与版本；数据库 `NOT NULL`，binding ID 受 FK 保护 |
| `plugin_config_hash` / `plugin_index_digest` | str(64) | 激活配置与 generated index 摘要快照 |
| `state` | str(20) | 生命周期: `CREATED` / `RUNNING` / `HOLD` / `CLOSED` / `RECONCILING` |
| `created_at` | datetime | 创建时间 (naive UTC) |
| `updated_at` | datetime | 更新时间 (naive UTC) |

**生命周期**:

```text
CREATED → RUNNING → CLOSED
              ↓
            HOLD → RUNNING (恢复)
              ↓
         RECONCILING → CLOSED (决议后)
```

**不变量**:
- Session 不持有工作状态（work item 是 ExecutionWorkItem 的责任）
- `manifest_version` 在 RUNNING 期间不可变更
- binding pins 从创建起完整且不可回退到未绑定执行
- 跨域只持 `correlation_id`，不持强 session FK

### 2.3 ExecutionCorrelation — 跨域关联键

**文件**: `src/app/runtime/orchestration/execution_correlation.py`
**表**: `wes_runtime.execution_correlations`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `correlation_id` | str(120) UNIQUE | 跨域稳定 correlation key |
| `execution_session_id` | int? (FK) | runtime 域内强 FK；NULL 允许 inbound callback 未解析前 ACK |
| `trace_id` | str(120) | 跨域 trace 时间线 |
| `source_event_id` | str(160)? | 外部事件归因 (request_id / event_id / command_code) |
| `business_owner_key` | str(160)? | 业务 owner 审计、查询和冲突定位 |
| `created_at` / `updated_at` | datetime | 时间戳 |

**索引**:
- `correlation_id` UNIQUE — 跨域查询主入口
- `(execution_session_id, created_at)` — runtime 域内回放
- `(trace_id)` — 跨域 trace 时间线

**约束**:
- 跨域读写都通过 `correlation_id`，不通过 `execution_session.id`
- runtime 域内才使用 `execution_session_id` 强 FK
- 其他域只持 `correlation_id` 引用

### 2.4 ExecutionWorkItem — 对象级执行令牌

**文件**: `src/app/runtime/orchestration/execution_work_item.py`
**表**: `wes_runtime.execution_work_items`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `execution_session_id` | int (FK) | runtime 域内强 FK |
| `correlation_id` | str(120) (FK, UNIQUE) | 跨域关联键 |
| `object_type` | str(60) | 对象类型: `bin` / `material` / `pkg` / `rack` |
| `object_key` | str(160) | 对象标识 |
| `current_step` | str(120) | 当前步骤 |
| `step_status` | str(20) | `PENDING` / `IN_PROGRESS` / `COMPLETED` / `FAILED` / `SKIPPED` |
| `parent_correlation_id` | str(120)? (FK) | 父 work item（批次追溯） |
| `concurrency_scope` | str(60)? | 并发域 |
| `deadline_at` | datetime? | 截止时间 |
| `lease_expires_at` | datetime? | 租约过期时间 |
| `idempotency_key` | str(160)? | 幂等键 |
| `plugin_key` / `manifest_version` | str | 与所属 Session 相同的 generated plugin identity |
| `plugin_binding_id` / `plugin_binding_version` | int | immutable binding pins |
| `plugin_config_hash` / `plugin_index_digest` | str(64) | 配置与 generated index 摘要 |

**并发契约**:
- ExecutionSession 不是整条 WorkLine 的串行锁
- WorkItem 独立推进，设备串行只按 DeviceDispatchPolicy
- 父子 work item 只用于追溯和批次收敛，子项失败不污染父项

### 2.5 RuntimeInbox — 入站消息

**文件**: `src/app/runtime/orchestration/runtime_inbox.py`（模型）+ `repositories/runtime_inbox_repository.py`（唯一仓储）+ `services/runtime_inbox/runtime_inbox_service.py`（接收/状态机服务）
**表**: `wes_runtime.runtime_inbox`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `workline_session_id` / `execution_session_id` | int? (FK) | WorklineSession 与 ExecutionSession 独立命名空间，不互相回退 |
| `kind` | str? | `DEVICE_EVENT` / `COMMAND_RESULT` / `EXTERNAL_HTTP` / `INTERNAL_EVENT` / `TIMER_TIMEOUT` / `REPLAY_REQUEST` |
| `status` | str | `RECEIVED` → `PROCESSING` → `PROCESSED` / `FAILED` / `DEAD_LETTER` 五态 |
| `source_event_id` | str? | 来源事件 ID |
| `provider_code` / `event_type` | str | source-event 幂等身份；二者与 `source_event_id` 组成唯一键 |
| `payload_json` | JSON | canonical 消息体，由 `RUNTIME_INBOX_PAYLOAD_MAX_BYTES` 控制，默认限制 1 MiB |
| `payload_hash` | str? | payload 哈希（幂等校验） |
| `claim_bucket_key` / `processor_token` | str? | 同桶 FIFO 与终态 fencing owner |
| `attempt_count` / `max_retries` | int | 重试预算 |
| `next_retry_at` / `lease_until` | bigint? | Unix 毫秒时间；到期 FAILED 与 stale PROCESSING 可重新 claim |
| `processed_at` / `failed_at` | bigint? | Unix 毫秒终态时间 |

**人工重放合同**：`POST /replay/inboxes/{inbox_id}` 必须提交去空白后非空、最长 100 字符的稳定
`request_id`；操作者只来自认证上下文，不接受客户端 `operator_id`。仅 `DEAD_LETTER` 且非
`PRE_CUTOVER_AUDIT_ONLY` 的记录可重放。新记录固定为 `provider_code=RUNTIME`、
`event_type/kind=REPLAY_REQUEST`，以 `replay:{source_inbox_id}:{request_id}` 作为幂等身份；相同身份和
canonical hash 返回既有 ACK，不重复写行或审计，内容变化则返回冲突。payload 使用单层 envelope，显式保存
`request_id`、`actor`、`reason`、直接/根 source inbox、五种原业务 kind、原 payload 与原 source/业务证据；
replay-of-replay 复用根业务语义，不递归嵌套。Processor 在 validation/context/generated dispatch 前只解包这一层，
继续使用原 claim/FIFO/token fencing/effect 幂等通道。

**状态机**:

```text
RECEIVED ──claim(new token)──> PROCESSING ──fenced write──> PROCESSED
                                  │
                                  ├──retryable──> FAILED ──到期 claim──> PROCESSING
                                  └──exhausted──> DEAD_LETTER

PROCESSING + expired lease ──claim(new token)──> PROCESSING
```

**ACK-before-processing**: 入站消息先持久化并返回 ACK，再异步处理。处理失败可重试、死信和人工重放。
标记为 `PRE_CUTOVER_AUDIT_ONLY` 的历史审计行使用 `DEAD_LETTER` 终态加稳定错误码表达，但不可 claim、retry 或 replay，
也不计入可行动 dead-letter。

### 2.6 RuntimeTimeline — ExecutionSession 执行轨迹

**文件**: `src/app/runtime/orchestration/runtime_timeline.py`
**表**: `wes_runtime.runtime_timelines`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `execution_session_id` | int (FK) | 关联 ExecutionSession |
| `trace_id` | str | 跨域 trace 标识 |
| `correlation_id` | str? (FK) | 关联 ExecutionCorrelation |
| `event_type` | str | append-only 事件类型 |
| `occurred_at` | bigint | Unix 毫秒时间 |

**用途**: ExecutionSession 的 append-only 执行轨迹，记录 runtime inbox、intent、hold、projection 与
device 事件，不作为业务 owner 状态源。

### 2.7 RuntimeHold — ExecutionSession effect 闸门

**文件**: `src/app/runtime/orchestration/runtime_hold.py`
**表**: `wes_runtime.runtime_holds`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `execution_session_id` | int (FK) | 关联 ExecutionSession |
| `correlation_id` | str? (FK) | 关联 ExecutionCorrelation |
| `reason` | str | 原因描述 |
| `hold_type` | str | `RESOURCE_WAIT` / `SAFETY` / `RECONCILING` / `MANUAL` / `TIMEOUT` |
| `scope_type` / `scope_key` | str | object/device/resource/queue/session/workline scope |
| `resolved_at` | bigint? | Unix 毫秒解除时间 |
| `allowed_next_effect_scope` | str? | 解除时明确允许的下一步 effect 范围 |

**用途**: ExecutionSession 内按 object/device/resource scope 暂停新 effect 的运行时闸门；只有影响整线安全时
才使用 SESSION/WORKLINE scope。

### 2.8 WorklineTimeline — WorklineSession 业务时间线

**文件**: `src/app/runtime/orchestration/models/timeline.py`
**表**: `wes_biz.workline_timelines`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `session_id` / `workline_id` | int (FK) | 关联 WorklineSession 与 WorkLine |
| `seq_no` | int | 同一 session 内单调递增，与 `session_id` 组成唯一约束 |
| `stage` / `action_type` / `status` | enum | 业务阶段、动作与结果 |
| `actor_type` / `actor_code` | enum / str? | 业务参与者 |
| `related_inbox_id` / `related_command_id` | int? | 关联入站记录与设备命令 |
| `payload_json` | JSON? | 业务排障证据 |

**用途**: WorklineSession 的业务排障与审计时间线，不替代 `wes_runtime.runtime_timelines` 的
ExecutionSession append-only 轨迹。

### 2.9 Business RuntimeHold — WorkLine 异常恢复事实

**文件**: `src/app/runtime/orchestration/models/runtime_hold.py`
**表**: `wes_biz.runtime_holds`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `workline_id` / `session_id` | int (FK) | 关联 WorkLine 与可选 WorklineSession |
| `hold_type` / `status` / `blocking` | enum / bool | 业务 hold 类型、生命周期与阻断状态 |
| `source_*` | str / int? | inbox/outbox/command/device 来源引用与幂等身份 |
| `evidence_snapshot_json` / `release_evidence_json` | JSON | 触发与解除证据 |
| `material_disposition` / `ng_reason_*` | enum / str? | 人工处置与 NG 归因 |

**用途**: WorkLine 业务异常恢复与人工处置的权威事实，不替代 `wes_runtime.runtime_holds` 的
ExecutionSession effect 闸门。

### 2.10 RuntimeIntentLog — 出站意图记录

**文件**: `src/app/runtime/orchestration/runtime_intent_log.py`
**表**: `wes_runtime.runtime_intent_logs`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `execution_session_id` | int (FK) | runtime 域内强 FK |
| `correlation_id` | str(120) (FK) | 跨域关联键 |
| `provider_code` | str(60) | provider 标识 |
| `target_domain` | str(60) | 目标域: `handling` / `device` / `wms_integration` |
| `target_action` | str(120) | 目标动作 |
| `idempotency_key` | str(160) | 幂等键 |
| `request_hash` | str(128) | immutable payload hash |
| `dispatch_status` | str(20) | `PENDING` → `DISPATCHING` → `DISPATCHED` / `ACKED` / `FAILED` |
| `attempt_count` | int | 尝试次数 |

**关键约束**:
- RuntimeIntentLog 是 effect proposal / outbox log，**不是状态源**
- 不可被下游反查为"意图对应的状态"
- 崩溃重放只重放 PENDING 或过期 DISPATCHING 且 request_hash 一致的记录
- 同 key 不同 hash 拒绝（不双发）

---

## 3. 支撑实体

### 3.1 IdempotencyKey — 幂等键

**文件**: `src/app/runtime/orchestration/idempotency_key.py`
**表**: `wes_runtime.idempotency_keys`

复合主键 `(provider_code, operation_kind, idempotency_key)`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `provider_code` | str(60) (PK) | `WES` / `WMS` / `ECS` / `RCS` / `AGV` / `CTU` |
| `operation_kind` | str(80) (PK) | `callback` / `fulfillment` / `device_command` / `device_event` / `reconciliation` |
| `idempotency_key` | str(160) (PK) | 调用方提供的业务键 |
| `execution_correlation_id` | str(120) (FK) | 关联 correlation |
| `request_hash` | str(128) | immutable payload hash |
| `business_owner_key` | str(160)? | 业务 owner |
| `created_at` | datetime | TTL 30 天 |

**行为**:
- 同 key 不同 `request_hash` → `409 Conflict` + 安全审计
- 同 key 同 `request_hash` → 直接返回旧 record
- WES 内部 key 格式: `WES-{OPERATION_KIND}-{HASH(source_id, source_event_id, correlation_id)}`

### 3.2 ConveyorQueueMembership — 输送线队列投影

**文件**: `src/app/runtime/orchestration/conveyor_queue_membership.py`
**表**: `wes_runtime.conveyor_queue_memberships`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `bin_code` | str(100)? | 料箱编码 |
| `placeholder_key` | str(240)? | 占位符键（扫码前） |
| `workline_id` | int | 关联 WorkLine |
| `conveyor_code` | str? | 输送线编码 |
| `queue_code` | str | manifest 队列 code |
| `queue_role` | str? | 进入队列时的 role 快照 |
| `membership_status` | str | `ACTIVE` / `LEFT` / `RECONCILING` |
| `entered_at` / `left_at` | datetime | 时间戳 |
| `correlation_id` | str? | 关联 correlation |
| `handling_operation_id` | int? | 搬运操作 ID |
| `evidence_json` | JSON? | evidence |

**约束**:
- `queue_code` 必须来自当前 WorkLine manifest 的 `pipeline_queues.code`
- 同一 `bin_code` 或 `placeholder_key` 在同一 WorkLine 下最多一个 ACTIVE membership
- 不定义系统级队列常量；队列只作为 manifest 配置实例存在

---

## 4. 数据库 Schema

### 4.1 Schema 命名空间

| Schema | 用途 | 包含表 |
|--------|------|--------|
| `wes_runtime` | runtime/orchestration 域 schema | `runtime_inbox`, `execution_sessions`, `execution_correlations`, `execution_work_items`, `runtime_timelines`, `runtime_holds`, `runtime_intent_logs`, `idempotency_keys`, `conveyor_queue_memberships`, `device_runtime_projections` |
| `wes_biz` | WorkLine 业务与投影数据 | `workline_sessions`, `workline_timelines`, `runtime_holds` |

### 4.2 迁移文件

| 迁移 | 内容 |
|------|------|
| `20260626_1140_c0bccb9de6f3` | ExecutionSession + ExecutionCorrelation 建表；`workline_id` 仅建查询索引，不建立跨 schema WorkLine FK |
| `20260626_1200_0e9de1e6c7e3` | Device FK ring dissolve |
| `20260626_1719_f04718a3f04f` | 新增 ExecutionWorkItem、RuntimeInbox、RuntimeIntentLog、RuntimeTimeline、RuntimeHold、ConveyorQueueMembership、IdempotencyKey |
| `20260702_1913_f88092809f4b` | DeviceRuntimeProjection 建表 |
| `20260711_1815_b8a28e1bfec8` | RuntimeInbox canonical envelope、五态 claim/fencing 字段与约束 |
| `20260711_1819_ec426c628516` | 增加显式 WorklineSession FK，迁移依赖后删除旧 `wes_biz.workline_inbox` |
| `20260714_1103_e0d58415afc9` | 在 Alembic autocommit block 中并发创建 RuntimeInbox hot indexes |
| `20260717_0739_fa15ba0aef65` | WorklinePluginBinding 建表，并为三类运行态记录增加 Binding FK 与可空 snapshot pins |
| `20260727_1742_be496b91f3e3` | 三类运行态记录的既有 binding snapshot pins 改为 NOT NULL；ExecutionWorkItem 新增 mandatory `manifest_version` |

---

## 5. 关键设计决策

### 5.1 为什么 ExecutionSession 和 WorklineSession 并存？

`WorklineSession`（`wes_biz.workline_sessions`）承载 WorkLine 业务会话与设备等待字段；`ExecutionSession`（`wes_runtime.execution_sessions`）承载跨域执行生命周期和 manifest version。两者是并存的显式业务边界，不是主从或备用执行路径；两者及 ExecutionWorkItem 都必须固定同一完整 binding pins。RuntimeInbox 分别使用 `workline_session_id` 与 `execution_session_id`，相同数值也不得跨命名空间推导。

### 5.2 为什么 ExecutionCorrelation 允许 NULL execution_session_id？

Inbound callback 可能在 session 解析之前到达。此时先创建 ExecutionCorrelation（`execution_session_id=NULL`）并 ACK callback，后续解析 session 后再回填 `execution_session_id`。

### 5.3 为什么 RuntimeIntentLog 不是状态源？

Runtime 只记录"曾尝试发出什么意图"。下游域（handling/device/resource/material/wms_integration）各自拥有自己的状态。这避免了跨域状态双写和冲突。

### 5.4 为什么 ConveyorQueueMembership 在 runtime 域而非 handling 域？

队列 membership 是 active projection（当前状态投影），不是搬运意图。Handling 只负责搬运请求生命周期；队列"谁在哪个队列"是 runtime 的当前状态投影。

---

## 6. 域内 API 边界

### 6.1 域内调用规则

- 域内 Service 通过 repository 访问数据库
- 域内 repository 不跨域访问
- 域间通过 port 接口调用，不直接 import 对方模型类
- 域间返回值通过 typed Pydantic 模型

### 6.2 关键 Service

| Service | 文件 | 职责 |
|---------|------|------|
| `RuntimeInboxService` | `services/runtime_inbox/runtime_inbox_service.py` | ACK-before-processing、重试、死信、人工重放 |
| `RuntimeInboxProcessorBridge` | `services/runtime_inbox/runtime_inbox_orchestrator_bridge.py` | binding/context validation → generated dispatch → typed effect + fenced write-back |
| `DeviceCommandGateway` | `services/device_command_gateway.py` | 设备命令下发网关 |
| `DeviceDispatchPolicy` | `services/device_dispatch_policy.py` | 设备调度策略（能力选择、优先级、deadline、限流） |
| `ConveyorQueueMembershipWriterService` | `services/conveyor_queue_membership_writer_service.py` | 队列 membership 写入 |
| `DeviceRuntimeProjectionWriterService` | `services/device_runtime_projection_writer_service.py` | DeviceRuntime 投影同步 |
| `RuntimeReconciliationServiceImpl` | `services/reconciliation/runtime_reconciliation_service_impl.py` | 运行时对账 |
| `IdempotencyGuard` | `services/idempotency_guard.py` | 幂等守卫 |

---

## 7. 测试

### 7.1 契约测试

- `tests/contracts/workline/` — WorkLine 行为契约
- `tests/contracts/wms_integration/` — WMS 集成契约
- `tests/contracts/device/` — 设备命令契约

### 7.2 单元测试

- `tests/runtime/orchestration/` — RuntimeInbox 轻量合同、运行态实体与 service contract 测试

### 7.3 性能测试

- `tests/load/runtime_benchmark_scenarios.py` — benchmark 场景
- `tests/load/test_plane_snapshot_benchmark.py` — plane snapshot benchmark
- `tests/load/test_runtime_inbox_claim_benchmark.py` — inbox claim benchmark

### 7.4 RuntimeInbox 核心 HEAVY 与 PostgreSQL 严格验收

- `tests/integration/test_device_runtime_projection_writer_service.py` — DeviceRuntime 投影持久 upsert、唯一冲突重读与 DeviceService 同事务同步
- `tests/integration/test_command_result_correlation_authority.py` — 命令结果服从命令创建时固定 ExecutionCorrelation
- `tests/integration/test_runtime_inbox_consumer_service.py` — 数据库幂等接收、唯一冲突重读与人工重放审计
- `tests/integration/test_runtime_inbox_service_internal_events.py` — 内部/设备事件与命令结果持久化和关联校验
- `tests/integration/test_runtime_inbox_claim_repository.py` — FIFO claim、lease、fencing 与 SLI snapshot
- `tests/integration/test_runtime_inbox_repository_consumers.py` — repository 消费者的数据库读取合同
- `tests/resilience/test_runtime_inbox_failure_state_machine.py` — 重试预算、死信、lease fencing 与故障恢复
- `tests/integration/test_runtime_inbox_migration_postgresql.py` — Revision A/B 与 audit-only migration matrix
- `tests/integration/test_runtime_inbox_processing_postgresql.py` — producer 到 fenced terminal 的生产处理链路
- `tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py` — 两个 crash window
- `scripts/run_runtime_inbox_postgresql_acceptance.py` — 按固定顺序执行 heavy suites 与 evidence validator
- `scripts/run_runtime_inbox_postgresql_acceptance_ci.sh` / `Jenkinsfile.backend-ci` — 隔离 PG17、清理与 artifact 归档

---

## 8. 相关文档

| 文档 | 说明 |
|------|------|
| `docs/architecture/workline-and-plugin-restructuring.md` | 父设计 §9.2 |
| `docs/architecture/target-state-contract.md` | Runtime 边界契约 |
| `docs/architecture/session-correlation-matrix.md` | Session/correlation 关联矩阵 |
| `docs/architecture/authority-matrix.md` | 外部事实权威来源 |
| `docs/architecture/device-command-contract.md` | DeviceCommand ECS 合同 |
| `docs/contracts/external-contract-profile.md` | ExternalContractProfile 合同 |
| `docs/contracts/evidence-catalog.md` | Evidence schema 变更日志 |
| `docs/contracts/observability-contract.md` | 可观测性合同 |
| `docs/contracts/runtime-toggle-governance.md` | Toggle 治理合同 |
| `docs/architecture/adr/workline-restructuring/0007-execution-correlation-key.md` | ADR-0007: ExecutionCorrelation |
| `docs/architecture/adr/workline-restructuring/0005-idempotency-composite-key.md` | ADR-0005: 幂等复合主键 |
