# 工作线运行时工作流指南

**最后更新**: 2026-06-09

本文档定义 WES 工作线运行时的标准数据链路，适用于真实设备、SANDBOX 调试和插件开发。本文只描述主干职责和状态边界，具体插件可在此基础上扩展业务决策。

## 1. 第一性原则

工作线运行时不是“设备直接调用设备”的链路，而是 WES 对物理世界事件、`RuntimeIntent` 决策和副作用派发的统一编排。

核心原则：

- `RuntimeInbox` 是编排唯一入口。
- `SystemOutbox` 是副作用统一出口，Workline 只负责写入可派发事实。
- `DeviceCommand` 是设备命令的业务主键载体。
- `WorklineSession` 是一条业务链路的 Runtime-owned lifecycle 实例。
- `WorklineTimeline` 是运行时决策和状态迁移账本。
- 设备 ACK 只表示“收到命令”，不表示“命令完成”。
- Command Result 才是推动 Runtime decision 继续前进的业务完成信号。
- 同一 WorkLine 可以同时存在多个 open Session；业务并发容量来自设备、Station、rack/bin/cell 和外部任务状态，不来自 worker 环境变量。

## 2. 标准链路

```text
Device Submit Event
  -> WES API 接收请求并返回 HTTP 成功
  -> 写入 DEVICE_EVENT Inbox
  -> Worker 消费 Inbox
  -> SessionResolver 解析或创建 WorklineSession
  -> Plugin 返回 RuntimeIntent
  -> 写入 Timeline 决策记录
  -> 创建 DeviceCommand
  -> 创建 SystemOutbox
  -> Session 进入 WAITING_DEVICE_RESULT
  -> SystemOutboxEngine 调度 Workline 域派发
  -> OutboxDispatchService 派发 Outbox 到目标设备
  -> Outbox 进入 SENT
  -> Device ACK 写入 DeviceCommand
  -> ACK 激活 Session 执行等待 deadline
  -> Device 执行命令
  -> Device Submit Result Callback
  -> 写入 COMMAND_RESULT Inbox
  -> Worker 消费 Result Inbox
  -> Plugin 返回 RuntimeIntent 判断下一步
       -> 有下一条命令：继续创建 DeviceCommand + SystemOutbox
       -> 无下一条命令：Session COMPLETED
       -> 异常或超时：Session FAILED
```

压缩表达：

```text
Device Event -> Inbox -> Runtime Decision
  -> [Command + Outbox -> Dispatch -> ACK -> Execute -> Result Callback -> Result Inbox -> Runtime Decision]*
  -> COMPLETED / FAILED / WAITING_*
```

## 3. 各阶段职责

| 阶段 | 责任方 | 主要写入 | 说明 |
| --- | --- | --- | --- |
| Submit Event | 设备 | `callback_logs`, `wes_runtime.runtime_inbox` | 设备上报物理事件，如扫码完成。 |
| Inbox Processing | Worker | `workline_sessions`, `workline_timelines` | 恢复上下文，调用插件取得 `RuntimeIntent`。 |
| Command Decision | Worker | `device_commands`, `system_outbox`, `workline_timelines` | WES 决定要对哪个目标设备下发什么命令。 |
| Dispatch Command | `SystemOutboxEngine` / `OutboxDispatchService` | `system_outbox`, `workline_dispatch_attempts` | Outbox 是待派发记录，派发服务才是真正发命令的组件。 |
| Device ACK | 设备 | `system_outbox`, `device_commands` | ACK 表示设备收到命令，不表示执行完成。 |
| Execute Command | 设备 | 设备侧状态 | WES 不直接认为命令已完成。 |
| Submit Result | 设备 | `callback_logs`, `wes_runtime.runtime_inbox` | Result Callback 写入 `COMMAND_RESULT` Inbox。 |
| Result Processing | Worker | `workline_sessions`, `workline_timelines`, `device_commands` | Runtime 根据插件返回的 `RuntimeIntent` 决定继续、完成或失败。 |

## 4. 关键对象语义

### 4.1 RuntimeInbox

`RuntimeInbox` 是运行时输入队列。所有会推动 Runtime decision 的输入都必须进入 Inbox。

常见 `kind`：

- `DEVICE_EVENT`：设备主动上报事件。
- `COMMAND_RESULT`：设备命令执行结果。
- `TIMER_TIMEOUT`：等待超时事件。
- `MANUAL_HOLD / MANUAL_RESUME / MANUAL_CANCEL`：人工操作。
- `REPLAY_REQUEST`：重放请求。

规则：

- API 层接收成功不等于业务完成，只表示输入已被 WES 接收。
- Worker 消费 Inbox 后，才会真正推动 `WorklineSession`。
- 任何 Result 都必须能关联到 `command_code` 和当前等待的 `awaiting_command_id`。

### 4.2 SystemOutbox

`SystemOutbox` 是运行时副作用出口。WES 不直接在插件里调用设备或外部系统，而是写 Outbox，由 `SystemOutboxEngine` 统一调度；Workline 域副作用再交给 `OutboxDispatchService` 处理。

常见 `dispatch_type`：

- `DEVICE_COMMAND`：派发设备命令。
- `EXTERNAL_HTTP`：调用外部系统。
- `INTERNAL_SIGNAL`：内部信号。

状态语义：

| 状态 | 含义 |
| --- | --- |
| `NEW` | 已创建，等待系统级派发任务拉取。 |
| `DISPATCHING` | 派发服务正在派发。 |
| `SENT` | 已发送到目标；设备 ACK 与 Result 事实记录在 DeviceCommand，不复制到 Outbox。 |
| `BLOCKED_RESOURCE` | 目标设备或派发资源暂忙，等待下一轮 ECS 实时 `IDLE` probe 放行。 |
| `FAILED` | 派发失败。 |
| `CANCELLED` | 已取消。 |

规则：

- `target_code` 是目标设备编码或目标地址。
- `payload_json.device_code` 在设备命令里也是目标设备，不是来源设备。
- Pending Outbox 展示的是 WES 等待外部处理的副作用，不代表设备之间互相发消息。
- `SENT` 且 Session 仍为 `WAITING_DEVICE_RESULT` 时，仍属于 Pending，因为业务还在等待 Result。
- 终态 Session 的 Outbox 不再属于 Pending。
- 设备 command terminal、本地 `DeviceStatus=IDLE` 和 `current_command_id=null` 只是诊断投影；blocked outbox 的重新派发必须由下一轮真实 ECS status probe 返回 `IDLE` 后放行。

### 4.3 DeviceCommand

`DeviceCommand` 是设备命令控制流的主记录。

关键字段：

- `command_code`：设备回调 Result 必须原样带回。
- `device_id`：目标设备 ID。
- `task_type`：命令类型，如 `PICK_AND_PUT`、`MOVE_FORWARD`、`PUT_TO_BIN`。
- `params`：业务参数。
- `session_id_int`：关联 `WorklineSession.id`。
- `trace_id`：端到端链路 ID。

规则：

- `command_code` 是 Result 归属的核心键。
- 一个 Result 只能作用于当前 Session 正在等待的 Command。
- 重复旧 Result 或过期 Result 必须在进入 Inbox 前被拒绝。

### 4.4 WorklineSession

`WorklineSession` 是一条业务链路的 Runtime-owned lifecycle 实例。

关键等待字段：

- `status`
- `current_wait_type`
- `awaiting_command_id`
- `deadline_at`

常见状态：

| 状态 | 含义 |
| --- | --- |
| `NEW` | 新建。 |
| `RUNNING` | Runtime 正在处理。 |
| `WAITING_DEVICE_RESULT` | 已下发命令，等待设备结果。 |
| `WAITING_EXTERNAL` | 等待外部系统。 |
| `MANUAL_HOLD` | 人工暂停。 |
| `COMPLETED` | 链路完成。 |
| `FAILED` | 链路失败。 |
| `CANCELLED` | 链路取消。 |

规则：

- ACK 和 Result 都必须校验 Session 仍在等待对应 Command。
- 超过 `deadline_at` 后，Timeout Scanner 会创建 `TIMER_TIMEOUT` Inbox。
- 终态 Session 不再接受旧 ACK 或旧 Result 推动。
- `current_wait_type=RESOURCE_WAIT` 表示 Runtime 已识别下一步资源暂不可用；它是自动等待，不是人工 Hold。

## 5. ACK 与 Result 的边界

### 5.1 Device ACK

ACK 的语义：

```text
设备已收到 WES 下发的 command
```

ACK 不代表：

- 命令执行成功。
- 命令执行失败。
- 业务状态可以进入下一步。
- Session 可以完成。

ACK 必须满足：

- Outbox 状态是 `SENT`。
- Outbox 是 `DEVICE_COMMAND`。
- Outbox 所属 Session 是 `WAITING_DEVICE_RESULT`。
- Session 的 `awaiting_command_id` 等于该 Outbox 对应的 Command ID。

### 5.2 Command Result

Result 的语义：

```text
设备已经执行完 command，并返回业务结果
```

Result 必须满足：

- `command_code` 存在且能找到 `DeviceCommand`。
- `device_code` 对应的设备就是该 Command 的目标设备。
- Command 所属 Session 是 `WAITING_DEVICE_RESULT`。
- Session 的 `awaiting_command_id` 等于该 Command ID。
- Result 的业务字段放在 `data` 中。

Result 被 Worker 消费后，插件返回 `RuntimeIntent`，Runtime 决定：

- 创建下一条 Command。
- 等待外部系统。
- 进入资源等待并自动重试。
- 进入人工暂停。
- 标记完成。
- 标记失败。

## 6. SANDBOX 链路

SANDBOX 不走真实设备，但必须复用同一条运行时链路。

```text
Sandbox Event
  -> DEVICE_EVENT Inbox
  -> Worker
  -> DeviceCommand + SystemOutbox
  -> Pending Outbox 展示
  -> Sandbox ACK
  -> DeviceCommand ACK_RECEIVED
  -> Sandbox Result
  -> COMMAND_RESULT Inbox
  -> Worker
  -> 下一步或完成
```

SANDBOX 特殊点：

- 工作线必须是 `run_mode=SIMULATION`。
- Pending Outbox 来源显示为 `系统`，目标显示为 `target_code`。
- `payload_json.device_code` 是目标设备，不是来源设备。
- ACK 后如果 Session 仍在 `WAITING_DEVICE_RESULT`，`SENT` Outbox 仍应展示，等待 Result。
- 如果 Session 已超时或失败，旧 ACK / 旧 Result 应返回业务错误。

## 7. 常见误解

### 7.1 “MES ACK” 是不是流程的一部分？

不是固定主链路术语。

设备提交 Event 后，WES API 会返回 HTTP 成功，这只是 WES 接收确认。MES 只有在插件决策需要调用外部系统时，才会通过 `EXTERNAL_HTTP` Outbox 进入链路。

### 7.2 Outbox 是不是已经发给设备？

不是。

Outbox 是待派发副作用记录。派发服务成功发送后，Outbox 才进入 `SENT`。

### 7.3 Device ACK 后为什么 Outbox 还在 Pending？

因为 ACK 只代表设备收到了命令。只要 Session 仍在等待 Result，`SENT` Outbox 仍是待处理项；ACK 事实以 DeviceCommand 为准。

### 7.4 Pending Outbox 为什么显示“系统 -> 设备”？

因为 Outbox 表达的是 WES 发起的副作用，不是设备之间互相发送。目标设备来自 `target_code`。

### 7.5 为什么旧 Result 会被拒绝？

Runtime 必须只接受当前等待点的 Result。旧 Result 如果在 Session 已进入下一步或终态后继续生效，会破坏因果链。

### 7.6 `RESOURCE_WAIT` 和 `BLOCKED_RESOURCE` 有什么区别？

`RESOURCE_WAIT` 属于 Inbox / Runtime decision 边界，表示插件或资源调度已经知道下一步 Station、rack/bin/cell 等资源暂不可用。它会写入 Session 等待态、诊断 evidence，并按重试间隔重新处理同一 Inbox。

`BLOCKED_RESOURCE` 属于 Outbox / dispatch 边界，表示设备命令副作用已经创建，但派发前实时 ECS 状态显示目标设备暂忙。它只能由后续 ECS `IDLE` probe 放行，不能由 WES 本地设备投影直接改回可派发。

两者都可以在运维视角展示为资源等待，但不能混用写入路径。

## 8. 判断一条链路是否健康

健康链路必须满足：

- 每个 accepted Event 都有 `trace_id`。
- 每个 Runtime decision 输入都进入 `RuntimeInbox`。
- 同一 WorkLine 可有多个 open Session，后续推进由真实资源状态约束。
- 每个设备副作用都先写 `DeviceCommand` 和 `SystemOutbox`。
- Outbox 派发有明确目标 `target_code`。
- ACK 不推动业务状态，只闭环派发接收。
- Result 通过 `COMMAND_RESULT` Inbox 推动 Runtime decision。
- Session 的 `awaiting_command_id` 与当前 Result 的 Command 一致。
- Timeline 能复盘每次决策、派发、等待、完成或失败。
- `RESOURCE_WAIT` evidence 能说明等待的 `resource_kind`、`resource_key`、首次等待、最近等待和等待次数。

## 9. 最小排障顺序

排查工作线链路时按以下顺序，不要跳层：

1. 查 `callback_logs`：设备请求是否到达 WES。
2. 查 `wes_runtime.runtime_inbox`：请求是否被接受为编排输入。
3. 查 `workline_sessions`：Session 当前状态、等待字段、失败字段。
4. 查 `workline_timelines`：Runtime 做了什么决策。
5. 查 `device_commands`：是否创建了目标命令。
6. 查 `system_outbox`：副作用是否创建、派发、ACK。
7. 查 `workline_dispatch_attempts`：真实派发是否成功。
8. 查 Result Inbox：设备执行结果是否回到 WES。

任何局部修复都必须回到这条链路验证，不能只修 UI 显示或单个状态字段。
