# Rack Domain 与 System Outbox Engine 架构规格

日期：2026-05-25

## 背景

当前代码已经开始把“料箱搬运域”和“货架操作域”拆开，但仍有两个关键偏离：

1. 货架操作仍落在 `workline` 模块下，核心模型叫 `WorklineRackTask`，并强制依赖 `workline_id`。
2. 出站消息底座分裂为 `WorklineOutbox` 和 Handling 内的 `SystemOutbox`，外部硬件系统调用没有统一引擎。

理想架构中，WES 中台是独立节点，应当提供单一、高内聚的出站消息底座，统一处理所有面向外部硬件系统（WMS/RCS/AGV/CTU/设备）的异步调用、指数退避重试和状态对账。

## 目标

1. 将 Rack 操作提升为系统级 `src/app/rack` 域，允许不带 Workline 上下文的全局货架搬运。
2. 建立唯一的 `SystemOutboxEngine`，统一承载 Workline、Rack、Handling 的外部派发。
3. 用显式 `OperationCompletionPolicy` 建模“回调即成功”和“资源投影确认后成功”的差异。
4. 删除未发布系统中的旧兼容层：不保留 `WorklineRackTask*`、`WorklineOutbox`、Handling 专属 `SystemOutbox`。
5. 保持 API 层不直接访问数据库，所有变更仍遵守 API → Service → Repository → Database 分层。

## 非目标

1. 不实现通用库位容量主数据模型。非 Workline Rack operation 本轮只具备基础搬运和投影确认能力。
2. 不实现 `CALLBACK_PLUS_RECONCILIATION` 的后台巡检任务，只建模字段和状态入口。
3. 不重构 Workline session、runtime hold、device command 的业务语义，只迁移其出站消息底座。
4. 不保留旧表名、旧 Celery 任务名、旧 import path。

## 架构决策

### 1. Rack 是系统级操作域

新增 `src/app/rack/`，它描述货架物理操作事实，不属于某条工作线。

核心模型：

- `RackOperation`：一次货架操作意图。
- `RackTask`：一次对外部系统的低级派发请求。
- `RackTaskSpec`：服务入口接收的内部低级任务描述。

`workline_id`、`workline_code`、`material_session_id` 是可选上下文，用于 Workline 场景中的等待恢复、追踪和清理，不是 Rack operation 成立的前提。

Workline runtime 调用 Rack 域时传入 Workline 上下文；WMS 或库存整理直接调用 Rack 域时可以不传 Workline。

### 2. System Outbox Engine 是唯一出站底座

新增或收敛到 `src/app/sys/`：

- `SystemOutbox`
- `SystemOutboxRepository`
- `SystemOutboxEngine`
- `SystemOutboxDispatchType`
- `SystemOutboxTargetType`
- `SystemOutboxStatus`

所有对外部硬件系统的异步调用都写入 `system_outbox`：

- Workline 设备命令
- Workline 外部 HTTP 请求
- Rack WMS/RCS/AGV 请求
- Handling WMS/RCS/CTU 请求

`SystemOutboxEngine` 负责：

- 候选消息查询
- 派发租约领取
- 外部 HTTP / 设备命令 / 内部信号派发
- 指数退避重试
- `BLOCKED_RESOURCE` 阻塞和释放
- 同一物理设备 FIFO
- 状态对账入口

### 3. 完成策略显式建模

新增 `OperationCompletionPolicy`：

| 策略 | 语义 | 默认使用方 |
| --- | --- | --- |
| `CALLBACK_TRUSTED` | Required task/step 全部回调成功即 operation 成功 | Handling |
| `RESOURCE_PROJECTION_REQUIRED` | Required task/step 全部回调成功后，还必须通过资源投影核对 | Rack |
| `CALLBACK_PLUS_RECONCILIATION` | 回调成功先推进，后续对账异常再进入 RECONCILING 或 runtime hold | 预留 |

Rack 和 Handling 可以保留不同容错倾向，但差异必须写入 operation 记录，并由统一策略服务解释。

### 4. Outbox 归属字段

`system_outbox` 不再使用 Handling 专属 `operation_id` 外键。统一使用：

- `operation_domain`: `WORKLINE`、`RACK`、`HANDLING`
- `operation_key`
- `workline_id`
- `session_id`
- `device_id`
- `trace_id`

其中 `workline_id/session_id/device_id` 都是可选上下文。系统级 Rack 或 Handling 操作可以没有 Workline。

### 5. 破坏性迁移策略

系统未发布，因此采用直接收敛：

- 删除 `workline_outbox` 表定义，改为 `system_outbox`。
- 删除 `workline_rack_tasks` 表定义，改为 `rack_operations` 和 `rack_tasks`。
- 更新未发布 Alembic revisions，而不是新增兼容迁移。
- 不提供旧 import alias。

## 数据模型约定

### `system_outbox`

核心字段：

- `dispatch_key`: 全局唯一派发幂等键。
- `dispatch_type`: `DEVICE_COMMAND`、`EXTERNAL_HTTP`、`INTERNAL_SIGNAL`。
- `target_type`: `DEVICE`、`HTTP_ENDPOINT`、`INTERNAL_SERVICE`。
- `target_code`: 设备编码、HTTP endpoint 或内部服务名。
- `payload_json`: 出站负载。
- `status`: `NEW`、`DISPATCHING`、`SENT`、`BLOCKED_RESOURCE`、`FAILED`、`CANCELLED`。
- `attempt_count`、`next_retry_at`、`last_error`、`sent_at`、`finished_at`。
- `operation_domain`、`operation_key`、`trace_id`。
- `workline_id`、`session_id`、`device_id`。
- `blocked_reason`、`blocked_device_id`、`blocked_workline_id`、`blocked_by_runtime_hold_id`、`blocked_by_reconciliation_session_id`。

### `rack_operations`

核心字段：

- `operation_key`
- `operation_type`
- `operation_status`
- `completion_policy`
- `workline_id`
- `workline_code`
- `material_session_id`
- `trace_id`
- `request_json`
- `result_json`
- `error_code`
- `error_message`
- `requested_at`
- `started_at`
- `completed_at`

### `rack_tasks`

核心字段：

- `operation_id`
- `operation_key`
- `sequence_no`
- `task_key`
- `task_type`
- `task_status`
- `rack_kind`
- `rack_code`
- `source_position_code`
- `target_position_code`
- `target_position_role`
- `dispatch_key`
- `outbox_id`
- `target_code`
- `source_system`
- `trace_id`
- `request_json`
- `actions_json`
- `callback_json`
- `result_json`
- `error_code`
- `error_message`
- `requested_at`
- `started_at`
- `completed_at`

## 运行时流程

### Rack operation 创建

1. 调用方提交内部 `RackTaskSpec`。
2. `RackOperationService` 创建或复用 `RackOperation`。
3. `RackOperationService` 校验任务合同、容量或 claim。
4. `RackGateway` 生成外部 payload。
5. `SystemOutboxRepository` 创建 `operation_domain="RACK"` 的 outbox。
6. `RackTaskLifecycleService` 创建 `RackTask`。

### Rack 回调完成

1. Callback orchestration 根据 `dispatch_key` 找到 `RackTask`。
2. 更新 task 状态和 callback 证据。
3. 根据 `RackOperation.completion_policy` 派生 operation 状态。
4. 若 operation 带 Workline session 且状态可恢复，则同步 session。

### Handling 回调完成

1. Callback orchestration 根据 `dispatch_key` 找到 Handling step。
2. 更新 step/move/operation。
3. `completion_policy=CALLBACK_TRUSTED` 时，所有 required step 成功即可成功。
4. 满箱交换缺少 `post_exchange_relations` 的既有对账逻辑保留。

## 验收标准

1. `src` 和 `tests` 中不再出现 `WorklineRackTask`、`WorklineOutbox`、`workline_rack_tasks`、`workline_outbox` 源码引用。
2. Rack operation 可以在没有 Workline 上下文时创建 task 和 outbox。
3. Workline 场景仍能通过 Rack operation 等待并恢复 session。
4. Workline、Rack、Handling 的出站消息都写入 `system_outbox`。
5. 只有一个 Celery outbox dispatch 任务。
6. Rack 与 Handling 的完成策略由持久化字段驱动，不再由服务内硬编码差异驱动。
