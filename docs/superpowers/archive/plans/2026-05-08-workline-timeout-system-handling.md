<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-autoplan-restore-20260508-135929.md -->

> Legacy notes: 本计划记录旧 wait-token/builder 阶段的 timeout 设计；当前实现以 `awaiting_command_id`、`deadline_at` 和 Runtime reconciliation 为准。

# WorkLine Timeout 系统级处理计划

> 本计划承接 `2026-05-06-workline-emergency-stop.md`：急停已从插件事件流提取为系统级安全事件，下一步将 timeout 从插件 `on_timeout()` 提取为系统级运行时治理。
> 本计划只处理 WorkLine runtime timeout 语义、对账隔离、重试边界、审计和测试；不改变第三方设备白皮书的接入协议。

## 目标

将 `TIMER_TIMEOUT` 从插件 `on_timeout()` 业务分支中移出，作为平台保留的运行时事实处理。

Timeout 发生后，WES 必须：

- 区分 **派发 ACK 超时** 和 **执行 Callback 超时**，不得把两者混成一个重试策略。
- 保留白皮书约定的派发 ACK 网络超时重试能力。
- 对执行 Callback 超时进入系统级“物理状态未知”对账流，不再调用插件 `on_timeout()`。
- 阻止执行超时后的自动物理重做，避免重复抓取、重复搬运动作。
- 隔离受影响 Device 和 WorkLine 新派发；`ACK_RECEIVED` 的上游动作允许闭环到安全停靠点，等待 runtime reconciliation 完成后自动恢复。
- 记录 timeout timeline、diagnostic、对账证据和迟到 callback 事实。
- 插件只声明等待条件和等待时长，不决定 timeout 默认结果。

## 非目标

- 不实现业务自定义 timeout handler。
- 不保留 `@on_timeout()` / `plugin.on_timeout()` 兼容路径。
- 不为执行 Callback 超时自动生成新 command 或重发旧 command。
- 不引入补偿命令或自动 cancel 设备 API。
- 不实现完整运营看板；v1 只提供最小对账/解除隔离契约。
- 不改变白皮书中的 HTTP 包络、`command_code` 幂等、Callback 规范。
- 不处理 WorkLine 急停；急停仍由独立 safety 计划负责。

## 架构决策

### D1. Timeout 必须拆成两类

根据 `backend/docs/integration/third_party_integration_whitepaper.md`：

- WES 与设备采用 `Command -> Ack -> Callback` 异步机制。
- `command_code` 是全局唯一指令编码，设备必须用它做幂等去重。
- 指令包络中的 `timeout` 是期望完成时间。
- 设备同步响应只代表“收到并接受任务”，不代表任务完成。
- WES 发出请求后 10 秒内未收到 HTTP 200，应视为网络超时并指数退避重试，最多 3 次。

因此 runtime 必须明确区分：

| 类型 | 触发条件 | 是否自动重试 | 处理边界 |
|---|---|---:|---|
| Dispatch timeout / no ACK | WES 下发 HTTP 请求后未在 10 秒内拿到 200 | 是 | Outbox dispatcher 层重试同一个 `command_code`；重试耗尽后进入通信 ACK 对账隔离 |
| Execution timeout / no Callback | `DeviceCommand.status = ACK_RECEIVED`，但 session `deadline_at` 到期仍无业务 Callback | 否 | `TIMER_TIMEOUT` 进入系统级对账隔离 |

### D2. ACK 网络超时保留自动重试

Outbox dispatcher 仍应支持白皮书约定：

- HTTP 请求超时阈值固定为设备通信 ACK 超时，不等同于业务完成 `timeout`。
- 超时后最多重试 3 次。
- 重试间隔使用指数退避 `1s, 2s, 4s`。
- 重试必须复用同一个 `command_code`、同一个 command/outbox 语义。
- 不得因为重试创建新 command，不得改变物理动作幂等键。

该能力属于设备通信可靠性，不属于 `TIMER_TIMEOUT`。

### D2a. no-ACK retry exhausted 进入通信 ACK 对账隔离

HTTP no-ACK retry exhausted 表示：WES 无法确认设备是否接受了同一个 `command_code`。它不是 execution Callback timeout，但也不能被当成普通业务失败后继续自动派发。

重试耗尽后的系统处理：

- `DeviceCommand.status = FAILED`，`failure_code = "COMMAND_ACK_EXHAUSTED"` 或等效枚举，保留 `command_code` 与全部 dispatch attempt evidence。
- 对应 outbox 标记 `FAILED`，`last_error = "OUTBOX_DISPATCH_FAILED"`，不再自动 retry。
- session 进入 `MANUAL_HOLD`，写入 runtime reconciliation 一等字段，`reconciliation_source_kind = "DISPATCH_ACK_EXHAUSTED"`，reason 必须为 `OUTBOX_DISPATCH_FAILED` / `COMMAND_ACK_EXHAUSTED`，不得写成 `CALLBACK_DEADLINE_EXPIRED`。
- WorkLine 进入 `RECONCILING`，阻断新 work admission / 新 HTTP dispatch。
- device 进入 `ERROR/OUTBOX_DISPATCH_FAILED` 或 `ERROR/COMMAND_ACK_EXHAUSTED`，阻断普通新命令。
- 不调用插件、不创建 `TIMER_TIMEOUT` inbox、不自动重发新 command。
- resolve 使用同一个显式人工对账权限和审计框架，但 checklist 文案必须区分“未确认设备是否接受命令”和“`ACK_RECEIVED` 但未收到 Callback”。

`WorklineRuntimeReconciliationService` 可以复用 hold/release 机制处理该路径，但 public method 和 diagnostic reason 必须区分，例如 `handle_dispatch_ack_exhausted(outbox, command)`。

### D2b. 执行等待 deadline 必须锚定设备 ACK

`PluginResultBuilder.wait(... timeout_seconds=...)` 声明的是设备接受任务后的业务完成窗口，不是 HTTP 派发请求发出后的墙钟时间。

因此执行 Callback timeout 必须满足 ACK 前置条件：

- `DeviceCommand.status == ACK_RECEIVED` 且 `ack_received_at` 非空，这是执行 timeout 的唯一 ACK truth source。
- `WorklineOutbox.status` 只表达 dispatch queue 状态，不作为 execution deadline / timeout scanner 的 ACK 判断来源。
- 删除 `OutboxStatus.ACKED` 以及 `mark_as_acked_by_dispatch_key()` 这类 outbox ACK 语义；outbox 不再复制设备 ACK 或业务 result 闭环事实。
- no-ACK retry exhausted 只能进入 dispatch ACK failure hold，不能创建 `TIMER_TIMEOUT` 对账。
- scanner 创建 `TIMER_TIMEOUT` 前必须校验 awaiting command 的 ACK 事实。
- timeout inbox payload 必须带上 ACK 事实快照，至少包括 `command_status`、`ack_received_at`。

实现必须采用 ACK 激活模型：

- `WorklineSession` 增加一等字段 `current_wait_timeout_seconds`，保存当前 wait 声明的业务完成窗口。
- `COMMAND_RESULT` wait 创建时只写入 `current_wait_type`、`current_wait_token`、`waiting_since`、`awaiting_command_id`、`current_wait_timeout_seconds`；`deadline_at` 暂不激活，保持 `NULL`。
- dispatcher 收到 HTTP 200 并完成 `DeviceCommand.status = ACK_RECEIVED` / `ack_received_at` 写入后，原子设置仍在等待该 command 的 session：`deadline_at = ack_received_at + current_wait_timeout_seconds`。
- 非 command-result wait 如果不依赖设备 ACK，可继续在 wait 创建时按原语义写 `deadline_at`。
- `TimeoutScanner` 只扫描 `deadline_at` 非空且 awaiting command 具有 ACK 事实的 session，避免 dispatch no-ACK 被误判为 execution no-Callback。

不得把 `timeout_seconds` 只塞进 `context_json`，也不得通过旧 `deadline_at` 反推等待窗口。

### D3. Callback 执行超时是物理状态未知，不是设备失败事实

`TimeoutScanner` 扫描到 `deadline_at < now` 且 ACK 前置条件成立后，幂等创建 `InboxKind.TIMER_TIMEOUT`。

`TIMER_TIMEOUT` 表示：WES 已经接受设备 ACK 并进入等待态，但设备未在约定完成窗口内回传 Callback。

系统处理固定为“进入对账隔离”：

- 将当前 session 退出等待态并置为 `MANUAL_HOLD`。
- 在 `WorklineSession` 一等字段写入 runtime reconciliation 主事实：
  - `reconciliation_state = "PENDING"`
  - `reconciliation_reason = "CALLBACK_DEADLINE_EXPIRED"`
  - `reconciliation_source_kind = "TIMER_TIMEOUT"`
  - `reconciliation_source_inbox_id`
  - `reconciliation_command_id`
  - `reconciliation_device_id`
  - `reconciliation_wait_token`
  - `reconciliation_ack_received_at`
  - `reconciliation_deadline_at`
  - `reconciliation_occurred_at`
  - `reconciliation_late_evidence_received = false`
- 清空 session 等待字段，避免 scanner 重复命中。
- 将 WorkLine `runtime_status` 置为 `RECONCILING`，`stopped_reason = "CALLBACK_DEADLINE_EXPIRED"`。
- 将受影响 device 置为 `ERROR`，`error_code = "CALLBACK_DEADLINE_EXPIRED"`，阻断后续自动派发。
- 将该 session 下 active outbox 本地取消或标记 blocked，reason 使用 `CALLBACK_DEADLINE_EXPIRED`。
- 写入 timeout timeline、diagnostic 和对账 evidence。

`session.context_json` 不承载 runtime reconciliation 的控制状态。command_code、device_code、command_status、原始 payload、迟到 callback payload 等详细证据写入 timeline/diagnostic/audit；所有 guard、查询、CAS、resolve、blocked outbox release 只读取 session 一等字段。

这不是业务完成、失败或取消结论。它只表达：WES 不知道物理动作是否完成，必须先对账。

人工对账后才允许把 session 决议为：

- `COMPLETED`：现场确认动作已完成，必要时补录业务结果。
- `FAILED`：现场确认动作失败或不可用。
- `CANCELLED`：现场确认任务作废且不会继续。

不得自动重发旧 command，也不得生成新 command。原因是设备可能已经在执行或已经完成但 Callback 丢失；自动重做会突破白皮书对重复物理动作的安全边界。

### D3b. 对账隔离必须阻断后续派发

`RECONCILING` 与急停不同：它不是安全急停，也不宣称物理设备已停止。它表达的是 WorkLine 存在未闭环资源，需要进入“软停线/安全停靠”模式。

v1 阻断边界：

- WorkLine：`runtime_status = RECONCILING` 时不得接收新 session / 新 work admission。
- Device：`device_status = ERROR` 且 `error_code = CALLBACK_DEADLINE_EXPIRED` 时不得向该设备派发新 HTTP command；已有 session 流转到该设备时只能创建 parked outbox，不得发送给设备。
- Session：`MANUAL_HOLD` 且 `reconciliation_state = PENDING` 时不得 replay、resume 或继续插件编排。

对同一 WorkLine 上其他已经在运行的 session，采用安全停靠规则：

- 已经 HTTP ACK 的上游 device command 允许自然完成；其 Callback 正常进入 runtime，不能因为后段 timeout 被误判为迟到 callback。
- 这些 session 可以推进到下一个安全边界，但不得再发起新的物理派发。
- 若插件编排产生新的 outbox，dispatcher 不发送 HTTP 请求，而是将 outbox 标记为 `BLOCKED_RESOURCE` 或等效 blocked 状态。
- 触发 reconciliation 的 session 本身是本次 hold 的 owner，不新增独立 reconciliation 表。
- blocked 记录必须包含 `blocked_by_reconciliation_session_id`、`blocked_device_id`、`blocked_workline_id`、`reason = CALLBACK_DEADLINE_EXPIRED`。
- blocked outbox 不启动 execution deadline；它还没有 ACK，不应被 `TimeoutScanner` 当成执行超时。

解除 runtime reconciliation 后，系统自动释放该 owner session 关联的 blocked outbox / blocked session：

- 不需要人工逐个 resume。
- 不调用插件 timeout handler。
- 不重放已处理 inbox。
- 只通过 repository/service 单一方法 `release_blocked_by_reconciliation_session(owner_session_id)` 把匹配的 blocked outbox 重新放回可派发队列，由 dispatcher 按正常 ACK retry 语义继续。
- release 只处理 `status = BLOCKED_RESOURCE` 且 `blocked_by_reconciliation_session_id = owner_session_id` 的记录；`FAILED`、`CANCELLED`、`SENT` 记录不得被释放。
- release 必须原子设置：`status = NEW`、`attempt_count = 0`、`next_retry_at = NULL`、`last_error = NULL`、`finished_at = NULL`，并清空 `blocked_by_reconciliation_session_id`、`blocked_device_id`、`blocked_workline_id`、`blocked_reason`。
- release 必须保留同一个 outbox、command、`command_code` / `dispatch_key`，不得创建新 command 或新 outbox。

若同一 WorkLine 存在多个 `reconciliation_state = PENDING` 的 hold owner session，单个 resolve 只能释放对应资源；WorkLine 只有在所有 pending hold 都解除后才能回到 `READY`。

### D4. `TIMER_TIMEOUT` 是平台保留 runtime event

插件不得声明或处理 timeout 默认对账策略：

- 删除 `@on_timeout()` decorator。
- 删除 `WorklinePlugin.on_timeout()` 默认方法。
- 删除 `_timeout_handler` 注册逻辑。
- 删除业务插件中的 timeout handler。
- 删除状态机中仅服务插件 timeout transition 的分支。

插件保留的唯一 timeout 相关能力是：

```python
PluginResultBuilder(ctx).wait(event_type="PICK_AND_PUT", timeout_seconds=300)
```

该声明只表达“等待什么”和“最多等多久”，不表达“到期后怎么处理物理状态未知”。

### D5. `ProcessInboxMessages` 必须在插件编排前短路 timeout

`ProcessInboxMessages._process_batch()` 加载关联实体后识别 `InboxKind.TIMER_TIMEOUT`。

该分支直接调用 `WorklineRuntimeReconciliationService.handle_timer_timeout()`，不得触发：

- plugin `on_timeout()`。
- `OrchestratorService.process_inbox()`。
- plugin state machine transition。
- command intent / outbox 创建。

### D6. `WorklineRuntimeReconciliationService` 必须防迟到竞争

系统处理 `TIMER_TIMEOUT` 前必须用原子条件校验当前 session 仍匹配 timeout inbox。

允许实现方式：

- 在同一 session/workline runtime lock 内锁定 session 后比较状态。
- 或 repository 层 compare-and-set update，条件至少包含：
  - `status in (WAITING_DEVICE_RESULT, WAITING_EXTERNAL)`
  - `deadline_at = payload.deadline_at`
  - `current_wait_token = payload.wait_token`（payload 能提供时）
  - `awaiting_command_id = payload.command_id`（payload 能提供时）
  - awaiting command 仍有 `ACK_RECEIVED` / `ack_received_at` 事实，且未被 callback terminal 推进

普通读后判断不够；timeout 与 callback 可能并发提交。

校验内容：

- session 存在。
- session 状态仍为 `WAITING_DEVICE_RESULT` 或 `WAITING_EXTERNAL`。
- session `deadline_at` 仍非空且已过期。
- inbox payload 中的 `deadline_at` 与 session 当前 `deadline_at` 匹配。
- 能定位时，`current_wait_token` / `awaiting_command_id` 不应与当前 session 事实冲突。
- 能定位时，awaiting command 状态必须仍与执行等待兼容，不得是 callback 已经闭环后的 terminal 状态。

如果 session 已被正常 callback 推进、人工取消、急停终止或其他路径 terminal：

- 不覆盖 session 状态。
- timeout inbox 进入明确终态，避免重试风暴。
- 记录 skipped/late timeout 证据，便于审计。

### D7. 迟到 Callback 不得自动解除 runtime reconciliation

设备可能在 runtime reconciliation 隔离后继续回调。

Callback ingress 或 command-result 处理必须在创建 command-result inbox、更新 command terminal、更新 device/outbox 之前先做 runtime reconciliation guard：

- 调用 `WorklineRuntimeReconciliationService.record_late_callback_if_pending()` 检测 command/session 是否处于 `CALLBACK_DEADLINE_EXPIRED` 对账状态。
- 记录迟到远端结果证据，并将 `reconciliation_late_evidence_received` 置为 true。
- 不自动覆盖 session `MANUAL_HOLD`。
- 不重新推进插件业务流程。
- 不创建会自动恢复旧 session 的 command-result 效果。
- 不把 device 从 `ERROR/CALLBACK_DEADLINE_EXPIRED` 自动恢复为可派发状态。
- 对账 resolve 时可以引用迟到 callback 作为证据，人工决定完成/失败/取消。

该规则与急停迟到 callback 处理一致：后到的远端事实只能补充证据，不能自动复活旧工作。

### D8. Timeline 与诊断要区分两种 timeout

用户和排障视图必须能看出 timeout 来源：

- Dispatch timeout：网络/HTTP ACK 超时，属于 outbox dispatch attempt；retry exhausted 后进入通信 ACK 对账隔离。
- Execution timeout：Callback 等待超时，属于 session wait deadline，语义是物理状态未知。

建议映射：

| 场景 | timeline | diagnostic |
|---|---|---|
| HTTP no ACK retry | `COMMAND_DISPATCH_RETRY` 或现有 dispatch attempt 失败记录 | `OUTBOX_ACK_TIMEOUT` / 网络类错误 |
| retry exhausted | `COMMAND_DISPATCH_EXHAUSTED` + `SESSION_MANUAL_HOLD` | `OUTBOX_DISPATCH_FAILED` / `COMMAND_ACK_EXHAUSTED` |
| Callback deadline expired | `WAIT_TIMEOUT` + `SESSION_MANUAL_HOLD` | `CALLBACK_DEADLINE_EXPIRED` |
| Late callback after timeout | `LATE_CALLBACK_RECORDED` 或等效证据事件 | 不自动清除对账 |
| inbox worker processing timeout | inbox failed/retry | `INBOX_PROCESSING_TIMEOUT` |

v1 如没有 `LATE_CALLBACK_RECORDED` 枚举，可先复用现有 diagnostic/evidence 结构，但文案必须说明“迟到回调，不自动解除对账隔离”。

### D9. v1 需要最小人工对账契约

新增或扩展 operation API，提供最小 resolve 动作：

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `POST` | `/workline/operations/reconciliations/sessions/{session_id}/resolve` | `biz:workline:resolve-reconciliation` | 人工对账后解除 runtime reconciliation 隔离 |

请求字段：

| Field | Required | Description |
|---|---|---|
| `resolution` | yes | `COMPLETED` / `FAILED` / `CANCELLED` |
| `checks` | yes | 按当前 `reconciliation_reason` 校验的 checklist |
| `operator_note` | yes | 现场确认说明 |
| `result_payload` | no | resolution 为 `COMPLETED` 时可补录业务结果摘要 |
| `confirmed_at` | yes | 现场确认时间 |

`checks` 最小集合按 reason 区分：

| Reason | Required checks |
|---|---|
| `CALLBACK_DEADLINE_EXPIRED` | `device_inspected`、`physical_state_confirmed`、`inventory_or_position_reconciled`、`late_callback_reviewed` |
| `COMMAND_ACK_EXHAUSTED` / `OUTBOX_DISPATCH_FAILED` | `device_reachable_checked`、`command_code_checked`、`physical_state_confirmed`、`safe_to_release_blocked_work` |

resolve 成功必须：

- 将 `reconciliation_state` 更新为 `RESOLVED`。
- 写入 `reconciliation_resolution`、`reconciliation_resolved_at`，operator 身份、checks、operator_note、result_payload 进入 audit/timeline。
- 根据 `resolution` 更新 session 终态。
- 根据同一个 `resolution` 终态化 `reconciliation_command_id` 指向的 `DeviceCommand`：
  - `COMPLETED`：`DeviceCommand.status = COMPLETED`，`completed_at = confirmed_at`，`result_data/result_payload` 写入人工补录摘要。
  - `FAILED`：`DeviceCommand.status = FAILED`，写入 `failure_code = reconciliation_reason` 或 operator 指定 failure code。
  - `CANCELLED`：`DeviceCommand.status = CANCELLED`，写入 operator cancelled 证据。
  - 对 `COMMAND_ACK_EXHAUSTED` / `OUTBOX_DISPATCH_FAILED`，operator 现场确认后也必须把 command resolve 到 completed/failed/cancelled 之一，不保留 active/intermediate 状态。
- 清除当前 reconciliation 对应的 WorkLine hold；如果没有其他 pending hold，WorkLine 恢复 `READY`。
- 清除受影响 device 的当前 reconciliation reason 对应 error，恢复为 `IDLE` 或人工指定的安全状态。
- 自动释放 `blocked_by_reconciliation_session_id` 指向当前 owner session 的 blocked outbox / blocked session，使其回到可派发队列。
- 不重新调用插件，不重新发送 command。

普通 `biz:workline:update` 权限不足以执行 reconciliation resolve。该动作会解除资源隔离并改变物理状态未知任务的最终结论，必须是显式权限、带 operator 身份、写审计记录。

### D10. 保留平台策略矩阵，不恢复插件 handler

本计划不恢复业务插件自定义 timeout handler。若后续业务确实需要不同 timeout 结果，只能通过平台拥有的策略矩阵扩展。

v1 策略矩阵固定为：

| Runtime fact | Retry policy | Session effect | Resource effect | Late evidence | Operator action |
|---|---|---|---|---|---|
| Dispatch no ACK | retry same `command_code` 3 times | exhausted 后 session `MANUAL_HOLD` | WorkLine `RECONCILING`, Device `ERROR/OUTBOX_DISPATCH_FAILED`, no new command id | dispatch evidence only | resolve 通信 ACK 对账后释放 parked outbox |
| Execution no Callback | no physical retry | owner session `MANUAL_HOLD`；其他 session 安全停靠 | WorkLine `RECONCILING`, Device `ERROR`, new outbox `BLOCKED_RESOURCE` | record only | resolve completed/failed/cancelled 后自动释放 blocked outbox |
| ESTOP pressed | no retry | fail/cancel old work per ESTOP plan | WorkLine `ESTOPPED` | record only | clear-estop |

## Backend Implementation Changes

### Runtime core

- 新增 `WorklineRuntimeReconciliationService`，作为 runtime reconciliation lifecycle 的唯一领域协调者。worker、operation API、callback ingress、dispatcher 只能调用该 service，不得各自实现 pending 判断、late callback evidence、resolve 或 blocked outbox release。
- `WorklineRuntimeReconciliationService` 负责：
  - `activate_execution_deadline_after_ack(command_id, ack_received_at)`：ACK 后激活等待 deadline。
  - `handle_dispatch_ack_exhausted(outbox, command)`：通信 ACK retry exhausted 后进入 `MANUAL_HOLD` / `RECONCILING` / device error，不创建 `TIMER_TIMEOUT`。
  - `handle_timer_timeout(inbox)`：session 进入 `MANUAL_HOLD`、wait 清理、runtime reconciliation 一等字段、WorkLine `RECONCILING`、device `ERROR`、active outbox cancel/block、timeline、diagnostic、inbox terminal。
  - `record_late_callback_if_pending(command, callback_payload)`：在 callback 改写 command/device/outbox 前记录迟到证据并短路自动推进。
  - `resolve_runtime_reconciliation(session_id, request, operator)`：人工对账终态、关联 command 终态、device/workline release、blocked outbox 自动释放、audit。
  - `has_pending_hold(workline_id)` / `assert_not_pending_reconciliation(session_id)`：供 admission/manual/replay guard 使用。
- `WorkLineRuntimeStatus` 新增 `RECONCILING`；`assert_accepting_work()` 对新 work admission 只接受 `READY`，但 outbox dispatcher 需要区分“新 HTTP 派发”和“释放已 parked outbox”，不能把 `RECONCILING` 实现成失败所有上游 session 的硬停线。
- `WorklineSession` 增加 `current_wait_timeout_seconds`，作为 ACK 后激活 execution deadline 的唯一等待窗口来源；`COMMAND_RESULT` wait 创建时不得提前写入 `deadline_at`。
- `WorklineSession` 增加 runtime reconciliation 一等字段：`reconciliation_state`、`reconciliation_reason`、`reconciliation_source_kind`、`reconciliation_source_inbox_id`、`reconciliation_source_outbox_id`、`reconciliation_command_id`、`reconciliation_device_id`、`reconciliation_wait_token`、`reconciliation_ack_received_at`、`reconciliation_deadline_at`、`reconciliation_occurred_at`、`reconciliation_late_evidence_received`、`reconciliation_resolution`、`reconciliation_resolved_at`。这些字段是 guard/CAS/查询/resolve 的唯一事实源。
- `DeviceRuntimeStatePolicy` 增加 `callback_deadline_expired` 与 `dispatch_ack_exhausted` 投影，分别输出 `device_status=ERROR`、`error_code=CALLBACK_DEADLINE_EXPIRED` / `OUTBOX_DISPATCH_FAILED`。
- `OutboxStatus` 删除 `ACKED`，新增 `BLOCKED_RESOURCE`；收敛为 `NEW` / `DISPATCHING` / `SENT` / `BLOCKED_RESOURCE` / `FAILED` / `CANCELLED`。`WorklineOutbox` 只表达派发队列状态，不表达设备 ACK 或 callback result。
- `WorklineOutbox` 增加结构化字段 `blocked_by_reconciliation_session_id`、`blocked_device_id`、`blocked_workline_id`、`blocked_reason`，不要把 release owner 只塞进 `payload_json`。
- 新增或收敛资源 hold / blocked outbox 投影：`BLOCKED_RESOURCE` 必须可追踪到具体 reconciliation owner session，resolve 后通过 `release_blocked_by_reconciliation_session(owner_session_id)` 原子恢复为 dispatchable。
- command governance 对普通新命令仍应拒绝 `ERROR/CALLBACK_DEADLINE_EXPIRED` 或 `ERROR/OUTBOX_DISPATCH_FAILED` device；对已有 session 的下一步命令可落库为 parked outbox，但必须禁止 HTTP dispatch。
- repository 层新增原子 reconciliation claim/transition 方法；只由 `WorklineRuntimeReconciliationService` 调用，禁止 worker/API/callback 直接普通读取后无条件写 session。
- `_apply_orchestrator_effects()` 保留对普通 plugin failure 的处理，不承载 `TIMER_TIMEOUT` 专属入口。
- `TimeoutScanner` 保留，但注释改为“创建系统 timeout inbox”，不再描述为触发插件编排。
- `TimeoutScanner` 必须只扫描/创建 ACK 后执行等待 timeout；查询或创建前需要关联 `awaiting_command_id` 与 `DeviceCommand.status/ack_received_at`，且 `deadline_at` 必须已经由 ACK handler 激活。
- `TimeoutScanner` 创建 timeout inbox 必须 get-or-create/idempotent，重复扫描同一 session + deadline + wait token + awaiting command 不得触发唯一键异常或错误告警。
- timeout inbox payload 必须快照 `session_id`、`workline_id`、`deadline_at`、`wait_token`、`awaiting_command_id`、`command_code`、`device_id`、`device_code`、`command_status`、`ack_received_at`。
- active outbox 阻断方法不要复用只表达急停的 `mark_as_blocked_by_workline_estop()` 命名；改成通用 `mark_as_blocked_by_workline_state()` 或新增 timeout 专用方法，reason 使用 `CALLBACK_DEADLINE_EXPIRED`。
- dispatcher 对 `RECONCILING` WorkLine 的行为：
  - 不向设备发起新的 HTTP dispatch。
  - `ACK_RECEIVED` command 的 Callback 继续被处理，让运行中 session 停到下一个安全边界。
  - 对已有 session 产生的新 outbox 标记 `BLOCKED_RESOURCE`，设置 `blocked_by_reconciliation_session_id`，不标记 failed，不计入 ACK retry exhausted。
  - resolve 后调用 `release_blocked_by_reconciliation_session(owner_session_id)` 批量释放同一 owner 的 outbox，重置 retry/error/blocked 字段并设置为 `NEW`。

### Runtime reconciliation API

- 在 operation model 增加 `ResolveRuntimeReconciliationRequest`。
- 在 operation service 增加 `resolve_runtime_reconciliation()`，只做权限/API 编排并委托 `WorklineRuntimeReconciliationService.resolve_runtime_reconciliation()`。
- 在 operation API 增加 `POST /reconciliations/sessions/{session_id}/resolve`。
- route 权限使用 `biz:workline:resolve-reconciliation`，并补充权限种子/同步逻辑。
- resolve 方法必须校验：
  - session 当前为 `MANUAL_HOLD`。
  - `reconciliation_state == "PENDING"`。
  - WorkLine 当前为 `RECONCILING`。
  - checklist 全部通过。
- resolve 方法直接更新 owner session/workline/device 状态，并释放对应 blocked outbox；不创建会进入插件编排的新 inbox。
- `create_manual_operation()`、`replay_inbox()`、manual resume/cancel 入口必须拒绝 `reconciliation_state == "PENDING"` 的 session；pending runtime reconciliation 的唯一恢复入口是 resolve API。

### Plugin SDK

- 删除 `on_timeout()` decorator。
- 删除 `WorklinePlugin.on_timeout()`。
- 删除 `_timeout_handler`。
- 更新 `WorklinePlugin` 文档：插件只处理 device event、command result、external HTTP、manual operation；timeout 由平台处理。
- 清理 `NullPlugin` 和业务插件中的 `@on_timeout()`。

### SMT classifier plugin

- 删除 `SmtClassifierPlugin.handle_timeout()`。
- 删除 `SmtClassifierStateMachine` 中 `timeout -> ERROR` transition。
- 保留各业务步骤中的 `.wait(... timeout_seconds=300)`。
- 原集成测试中直接调用 `plugin.on_timeout()` 的用例改为验证系统级 `TIMER_TIMEOUT` 处理。

### Dispatcher retry

- 将 outbox dispatcher HTTP no-ACK retry 从“审核”提升为本计划必做。
- 固定通信 ACK timeout 为 10 秒；不得使用业务完成 `timeout_ms` 作为 HTTP ACK timeout。
- 明确 `MAX_RETRIES = 3` 表示最多 3 次重试，不含首次尝试；总尝试次数最多 4 次。
- retry delay 固定为 `1s, 2s, 4s`。
- 每次 retry 必须复用同一个 outbox、command 和 `command_code`。
- retry exhausted 后标记 outbox/command dispatch failure，并调用 `WorklineRuntimeReconciliationService.handle_dispatch_ack_exhausted()` 进入通信 ACK 对账隔离；不进入 `TIMER_TIMEOUT` 对账路径。
- retry exhausted 不创建新 command、不调用插件、不自动继续同线新 HTTP dispatch。
- dispatcher 收到 HTTP 200 后才允许把 command 标记为 ACK，并调用 `WorklineRuntimeReconciliationService.activate_execution_deadline_after_ack()` 用 `current_wait_timeout_seconds` 激活对应 execution wait deadline。
- command 是执行 ACK 的唯一真相源：dispatcher 收到 HTTP 200 后必须写 `DeviceCommand.status = ACK_RECEIVED` 与 `ack_received_at`；outbox 保持 `SENT` 并只用于 dispatch 追踪，不得出现 outbox ACK 状态或作为 timeout scanner 的 ACK 证据。
- `submit_sandbox_ack()` 只允许模拟 `DeviceCommand.status = ACK_RECEIVED` / `ack_received_at`，并触发 ACK deadline 激活；不得再把 outbox 标记为 `ACKED`。
- 诊断码必须拆分：HTTP no-ACK 使用 `OUTBOX_ACK_TIMEOUT` / `OUTBOX_DISPATCH_FAILED`；inbox worker 自身 `asyncio.wait_for` 使用 `INBOX_PROCESSING_TIMEOUT`；Callback deadline 使用 `CALLBACK_DEADLINE_EXPIRED`。不要再用 `DEVICE_TIMEOUT` 混写这些路径。

### Callback ingress and late evidence

- 在 `CallbackOrchestrationService.process_result()` 进入 command-result inbox 前调用 `WorklineRuntimeReconciliationService.record_late_callback_if_pending()`。
- 在 `DeviceCommandService.handle_callback_result()` 更新 command terminal 前调用同一 service guard，避免绕过 ingress 的内部调用改写 terminal 状态。
- callback/result 回到 WES 后只更新 command/session/timeline/diagnostic，不调用 `mark_as_acked_by_dispatch_key()`，不把 outbox 改成业务结果闭环事实。
- 迟到 callback 只更新 reconciliation evidence，不自动推进插件、不自动完成 session。
- callback 处理必须验证 `command.device_id`、`workline_id` 与 payload `device_code` / API app scope 一致；不允许用可信度不足的 callback 解除或改写 runtime reconciliation isolation。
- Trace/diagnostic response builder 展示 late callback evidence 和当前 required operator action。

## Frontend / Docs Follow-up

前端不需要完整运营看板，但需要能呈现 runtime reconciliation 状态和最小人工解除入口。

需要保持：

- `TIMER_TIMEOUT` 显示为“等待超时”。
- `WAIT_TIMEOUT` 显示为“等待超时”。
- `CALLBACK_DEADLINE_EXPIRED` 显示为“设备结果未按时回传，需人工对账”。
- `BLOCKED_RESOURCE` 或等效 parked 状态显示为“等待资源恢复”。
- `RECONCILING` WorkLine 显示为阻断态，高于普通风险/失败标签。
- 人工对账 resolve 入口必须说明“不会重发设备命令，不会恢复 owner session 的旧插件流程；会自动释放因本次 reconciliation 安全停靠的后续任务”。

需要清理：

- 文档和调试说明中“插件 `on_timeout()` 处理超时”的描述。
- 任何暗示 Callback timeout 会自动重试物理动作的文案。
- `docs/workline_flow_diagram.md`、`docs/system_vs_plugin_capabilities.md`、`docs/business/workline_runtime_workflow_guide.md`、`docs/business/workline_plugin_architecture_design.md` 中关于 `on_timeout()` / `TIMER_TIMEOUT -> plugin` 的图示和说明。
- `docs/workline_diagnostics_quickstart.md`、`docs/integration/workline_device_error_code_standardization.md` 中把执行超时写成 `DEVICE_TIMEOUT` 的说明。

### Developer Experience / Migration

这是破坏性 SDK 变更，但 WES 尚未发布，不保留兼容路径。DX 目标不是兼容旧代码，而是让插件作者和后端实现者在第一次失败时马上知道该怎么改。

插件作者迁移体验必须满足：

- 导入 `on_timeout` 或使用 `@on_timeout()` 时，启动/注册阶段给出明确错误：`on_timeout is removed; use PluginResultBuilder.wait(... timeout_seconds=...) and platform runtime reconciliation`。
- 插件模板、示例和业务插件文档只展示 `.wait(... timeout_seconds=...)`，不再展示 timeout handler。
- migration note 给出 before/after：
  - before：`@on_timeout()` 返回 `.failure(code="DEVICE_TIMEOUT")`。
  - after：业务步骤声明 wait；系统在 Callback deadline expired 后进入对账隔离。
- Debug 文档明确三类排障入口：
  - no ACK：看 outbox attempts / `OUTBOX_ACK_TIMEOUT` / `OUTBOX_DISPATCH_FAILED`。
  - no Callback：看 session `reconciliation_*` 字段 / `CALLBACK_DEADLINE_EXPIRED`。
  - late Callback：看 evidence，不期待插件被重新调用。

后端实现者体验必须满足：

- 新增的 diagnostic code、runtime status、permission、API schema 都要有单一枚举/模型来源，避免字符串散落。
- `TimeoutScanner`、dispatcher retry、callback guard、resolve API 的测试名要直接表达业务事实，例如 `test_no_ack_retry_exhausted_does_not_create_timer_timeout`。
- resolve API 的 OpenAPI summary 必须写明“不重发设备命令、不调用 timeout 插件处理、解除人工对账隔离并释放安全停靠队列”。
- trace response 中必须显式返回 `required_operator_action = "RESOLVE_RUNTIME_RECONCILIATION"`、`blocked_outbox_count` 或等效字段，避免前端靠文案推断。

## Test Plan

### Backend unit tests

- `TIMER_TIMEOUT` inbox 不调用 `plugin.on_timeout()`。
- `WorkLineRuntimeStatus` 枚举测试更新为包含 `RECONCILING`，并验证 `assert_accepting_work()` 对新 work admission 只接受 `READY`。
- waiting session 执行超时后：
  - session 进入 `MANUAL_HOLD`。
  - `reconciliation_state == "PENDING"`。
  - `reconciliation_reason == "CALLBACK_DEADLINE_EXPIRED"`。
  - `reconciliation_source_kind == "TIMER_TIMEOUT"`。
  - `reconciliation_command_id`、`reconciliation_device_id`、`reconciliation_deadline_at`、`reconciliation_occurred_at` 被写入。
  - wait 字段清空。
  - WorkLine 进入 `RECONCILING`。
  - device 进入 `ERROR/CALLBACK_DEADLINE_EXPIRED`。
  - active outbox 被取消或 blocked。
  - timeout inbox 标记为 processed。
- `COMMAND_RESULT` wait 创建时：
  - 保存 `current_wait_timeout_seconds`。
  - `deadline_at` 保持 `NULL`，scanner 不创建 `TIMER_TIMEOUT`。
  - HTTP 200 ACK 后用 `DeviceCommand.ack_received_at + current_wait_timeout_seconds` 激活 `deadline_at`。
- outbox 状态不再包含 `ACKED`：
  - HTTP 200 ACK 只更新 `DeviceCommand.status = ACK_RECEIVED` / `ack_received_at`。
  - sandbox ACK 只模拟 command ACK，不写 outbox `ACKED`。
  - callback/result 不调用 `mark_as_acked_by_dispatch_key()`。
- 最后一个设备 timeout 时，前序设备已经 ACK 的其他 session：
  - Callback 可以正常处理。
  - 不被标记为 timeout/failure/manual hold。
  - 新产生的下游 outbox 标记为 `BLOCKED_RESOURCE`，并写入 `blocked_by_reconciliation_session_id`。
  - blocked outbox 不启动 execution deadline。
- no-ACK command/outbox 即使等待很久也不得创建或处理为 `TIMER_TIMEOUT`；retry exhausted 后必须是 dispatch ACK failure hold：session `MANUAL_HOLD`、WorkLine `RECONCILING`、device `ERROR/OUTBOX_DISPATCH_FAILED`。
- timeout inbox payload 缺少 `wait_token`、`awaiting_command_id` 或 command ACK 快照时，handler 必须跳过或失败为明确 diagnostic，不能无条件修改 session。
- 重复扫描同一个过期等待只得到一条有效 `TIMER_TIMEOUT` inbox 或同一条幂等记录，不能产生唯一键异常。
- runtime reconciliation resolve 后：
  - checklist 必须全 true。
  - session 按 `COMPLETED` / `FAILED` / `CANCELLED` 决议进入终态。
  - `reconciliation_command_id` 指向的 `DeviceCommand` 按同一 `resolution` 进入 `COMPLETED` / `FAILED` / `CANCELLED` 终态。
  - resolve 后不得残留 `ACK_RECEIVED`、`SENT`、`PENDING` 或 `TIMEOUT` active command。
  - 如果没有其他 pending hold，WorkLine 恢复 `READY`。
  - device 解除 `CALLBACK_DEADLINE_EXPIRED` 阻断。
  - 同一 owner session 关联的 `BLOCKED_RESOURCE` outbox 自动恢复为可派发：`status=NEW`、`attempt_count=0`、`next_retry_at/last_error/finished_at=NULL`、blocked 字段清空。
  - release 不创建新 outbox/command，`command_code` / `dispatch_key` 保持不变。
  - audit 记录包含 operator、resolution、checks、operator_note、confirmed_at。
- dispatch ACK exhausted resolve 后：
  - 使用 `OUTBOX_DISPATCH_FAILED` / `COMMAND_ACK_EXHAUSTED` reason。
  - operator checklist 覆盖设备是否收到该 `command_code`、是否执行、是否需要现场清理。
  - operator 现场确认完成时允许把 command 从 dispatch failed evidence resolve 为 `COMPLETED`，但必须保留原 retry exhausted evidence。
  - 不自动重发旧 command，不创建新 command。
  - 只释放本次 hold 关联的 blocked outbox。
- 已被 callback 推进的 session 收到迟到 timeout inbox 时：
  - 不覆盖当前 session 状态。
  - timeout inbox 有明确终态。
  - 记录 skipped/late timeout 证据。
- 已 terminal session 收到 timeout inbox 时不复活。
- timeout inbox 缺 session/workline 时写 diagnostic，不无限重试。
- `create_manual_operation()`、`replay_inbox()`、manual resume/cancel 对 pending runtime reconciliation 返回明确拒绝错误。
- resolve API 权限测试覆盖：`biz:workline:update` 不足，`biz:workline:resolve-reconciliation` 才允许。

### Backend integration tests

- `TimeoutScanner` 只为 ACK 后过期等待 session 创建幂等 `TIMER_TIMEOUT` inbox。
- 未 ACK 的 command-result wait 即使等待很久也不创建 `TIMER_TIMEOUT`；通信失败只能走 outbox retry，重试耗尽后进入 dispatch ACK failure hold。
- 真实 `ProcessInboxMessages` 路径处理 `TIMER_TIMEOUT`，不进入 `OrchestratorService` 插件分发。
- 派发 HTTP no ACK 走 outbox retry，断言 10 秒 timeout、`1s/2s/4s` retry、同一 `command_code`。
- retry exhausted 进入 dispatch ACK failure hold，且与 execution timeout 的 diagnostic / reason 不混淆。
- runtime reconciliation 后迟到 command-result callback 不恢复旧 session，只进入 evidence。
- WorkLine `RECONCILING` 时新 work admission 被拒绝，新 HTTP dispatch 被 parked 为 `BLOCKED_RESOURCE`，但 `ACK_RECEIVED` 上游 command 的 callback 仍可让 session 安全停靠。
- Device `ERROR/CALLBACK_DEADLINE_EXPIRED` 时 command governance 拒绝普通新命令；已有 session 的下一步只能 parked，不能 HTTP dispatch。
- timeout 与 callback 并发时只允许一个路径获得原子状态转换；另一方进入 evidence/skipped terminal。
- callback payload 中的 `device_code` / API app scope 与 command 不一致时拒绝处理，不得写 late evidence 或恢复设备。
- HTTP 200 ACK 后 execution wait deadline 以 `DeviceCommand.ack_received_at` 为锚点，避免 dispatch 延迟吃掉业务执行窗口。
- sandbox ACK 走同一 `DeviceCommand.ACK_RECEIVED` 与 deadline 激活路径，不依赖 outbox `ACKED`。
- resolve 最后设备 runtime reconciliation 后，前序 session 的 blocked outbox 自动重新进入 dispatch，不需要 manual resume/replay。
- 多个 pending runtime reconciliation 时，只释放当前 owner session 对应的 blocked outbox；WorkLine 仍保持 `RECONCILING` 直到所有 hold 清除。
- release blocked outbox 只影响 `BLOCKED_RESOURCE` + owner 匹配记录，并重置 retry/error/blocked 字段；`FAILED`、`CANCELLED`、`SENT` 不被误释放。

### Frontend tests

- 现有 runtime label 测试继续覆盖：
  - `TIMER_TIMEOUT`
  - `WAIT_TIMEOUT`
  - `TIMEOUT`
  - `CALLBACK_DEADLINE_EXPIRED`
  - `BLOCKED_RESOURCE`
  - `RECONCILING`
- 搜索确认前端源码没有“插件 on_timeout 处理超时”的用户可见文案。

## Verification Commands

Backend:

```bash
rtk uv run pytest tests/workline_runtime/test_timeout_scanner.py
rtk uv run pytest tests/workline_runtime/test_inbox_consumer.py
rtk uv run pytest tests/integration/workline_runtime/test_timeout_inbox_real_path_integration.py
rtk uv run pytest tests/workline_runtime/test_workline_operation_service.py
rtk uv run pytest tests/workline_runtime/test_plugin_base.py
rtk uv run pytest tests/workline_runtime/test_outbox_dispatcher.py
rtk uv run pytest tests/workline_runtime/test_workline_safety_service.py
rtk rg -n "on_timeout|@on_timeout|_timeout_handler" src tests docs --glob '!docs/superpowers/plans/*'
rtk rg -n "OutboxStatus\\.ACKED|mark_as_acked_by_dispatch_key|outbox.*ACKED|ACKED.*outbox" src tests docs --glob '!docs/superpowers/plans/*'
```

Frontend:

```bash
rtk pnpm test tests/unit/utils/runtime-labels.test.ts
rtk pnpm type:check
rtk rg -n "on_timeout|@on_timeout|插件.*超时|超时.*插件" src tests docs
rtk rg -n "CALLBACK_DEADLINE_EXPIRED|RECONCILING" src tests docs
```

## Acceptance Criteria

- 白皮书的 ACK 网络超时自动重试能力被保留，并明确不属于 `TIMER_TIMEOUT`。
- execution deadline 以 ACK 后事实为前置；no-ACK / retry exhausted 绝不进入 `TIMER_TIMEOUT`。
- no-ACK retry exhausted 进入 `OUTBOX_DISPATCH_FAILED` / `COMMAND_ACK_EXHAUSTED` 通信 ACK 对账隔离，不只是 outbox failed。
- `DeviceCommand.status/ack_received_at` 是 execution timeout 的唯一 ACK truth source；outbox status 不参与 ACK 判断。
- runtime reconciliation resolve 必须同步终态化 owner session 和 `reconciliation_command_id` 指向的 `DeviceCommand`，不得残留 active command。
- `OutboxStatus.ACKED` / `mark_as_acked_by_dispatch_key()` 被删除；sandbox ACK、真实 HTTP ACK、callback/result 都不再把 outbox 当 ACK 或 result 事实源。
- blocked outbox release 必须通过 `release_blocked_by_reconciliation_session(owner_session_id)` 原子完成，只释放 owner 匹配的 `BLOCKED_RESOURCE`，重置 retry/error/blocked 字段，并保留原 outbox/command/`command_code`。
- `TIMER_TIMEOUT` 由系统级 handler 处理，不调用插件。
- 插件 SDK 不再暴露 `@on_timeout()` / `on_timeout()`。
- 业务插件不再包含 timeout handler。
- Callback 执行超时进入对账隔离，不自动重发物理命令。
- WorkLine / Device 新派发被阻断，`ACK_RECEIVED` 上游动作可闭环到安全停靠点。
- 最后设备 runtime reconciliation resolve 后，因该 reconciliation parked 的后续 session 自动恢复派发，不需要人工逐个 resume。
- 迟到 callback 只记录证据，不自动恢复 runtime reconciliation owner session。
- pending runtime reconciliation 阻断 replay/manual resume/manual cancel 等旁路操作，唯一恢复入口是 resolve API。
- resolve API 使用 `biz:workline:resolve-reconciliation` 显式权限，并写 operator audit。
- `TimeoutScanner` 幂等创建带 wait/command/device/ACK 快照的 timeout inbox。
- Timeline/diagnostic 能区分 dispatch no ACK 与 execution no Callback。
- 文档中不再把 timeout 默认 failure 描述为插件职责。

## Assumptions

- `CALLBACK_DEADLINE_EXPIRED` 作为执行 Callback 超时的默认 runtime diagnostic code。
- 派发 ACK 超时重试以设备端 `command_code` 幂等为安全前提。
- 执行 Callback deadline 以 `DeviceCommand.ack_received_at` 为锚点；no-ACK 不进入 `TIMER_TIMEOUT`。
- v1 不支持业务插件自定义 timeout 分支；后续若需要可设计平台策略表，而不是恢复插件 handler。
- 本计划主要落在 backend；frontend 只做状态呈现和最小人工对账入口。

---

## GSTACK AUTOPLAN REVIEW

### Phase 1: CEO Review

**Plan summary:** 将 `TIMER_TIMEOUT` 从插件 `on_timeout()` 提取为平台保留 runtime fact，同时按白皮书区分 dispatch no-ACK retry 与 execution no-Callback。评审后核心语义已从“默认失败”升级为“物理状态未知，对账隔离，人工闭环”。

#### 0A. Premise Challenge

| Premise | Verdict | Decision |
|---|---|---|
| `on_timeout()` 不应由插件处理 | Confirmed | 插件只声明 wait，平台处理 timeout fact，符合系统/插件边界。 |
| Dispatch timeout 与 execution timeout 是两类问题 | Confirmed | 白皮书明确 10 秒 no-ACK retry 与业务完成 `timeout` 不同。 |
| Callback 超时可以直接写成 session failure | Rejected | 双声音一致认为 timeout 只证明 WES 未收到结果，不证明设备失败。 |
| v1 可以不做完整运营看板 | Confirmed with guardrail | 不做看板，但必须有最小人工对账 resolve 契约。 |
| 保留 ACK retry 是安全的 | Conditional | 只有同一 `command_code`、固定 retry 语义、供应商幂等验证成立时才安全。 |

#### 0B. Existing Code Leverage

| Sub-problem | Existing leverage | Plan action |
|---|---|---|
| 等待与 deadline | `WorklineSession.current_wait_*`, `deadline_at`, `TimeoutScanner` | 新增 `current_wait_timeout_seconds`，ACK 后激活 `deadline_at`，scanner 改为创建系统对账入口。 |
| 人工停顿 | `SessionStatus.MANUAL_HOLD`, manual operation API | 复用 `MANUAL_HOLD` 表达等待人工对账。 |
| 设备阻断 | `DeviceStatus.ERROR`, `error_code`, command governance | 用 `CALLBACK_DEADLINE_EXPIRED` / `OUTBOX_DISPATCH_FAILED` 阻断新命令。 |
| WorkLine 阻断 | `WorkLineRuntimeStatus`, safety assert/dispatcher gate | 新增 `RECONCILING`：阻断新 work admission 和新 HTTP dispatch，但允许 `ACK_RECEIVED` 上游动作闭环到安全停靠点。 |
| 迟到 callback | `CallbackOrchestrationService`, `DeviceCommandService`, trace diagnostics | 加 guard 与 evidence，不自动恢复业务流。 |
| 派发重试 | `OutboxDispatcher.MAX_RETRIES`, outbox `attempt_count/next_retry_at` | 收敛为白皮书 10s + `1s/2s/4s` + same `command_code`；耗尽后进入通信 ACK 对账隔离。 |

#### 0C. Dream State Delta

```text
CURRENT
  plugin on_timeout() can decide timeout semantics
  timeout may become DEVICE_TIMEOUT failure too early
  physical state unknown has no explicit reconciliation state

THIS PLAN
  platform owns TIMER_TIMEOUT
  dispatch no-ACK retry remains transport-level until exhausted, then enters communication ACK reconciliation
  execution no-Callback enters RECONCILING + MANUAL_HOLD + Device ERROR
  upstream ACK_RECEIVED work drains to safe parked outbox
  late callback becomes evidence, not automatic recovery
  operator resolves completed / failed / cancelled and parked work resumes

12-MONTH IDEAL
  one Runtime Terminal Policy Matrix owns ESTOP, timeout, dispatch exhausted,
  manual cancel, late callback, resource quarantine, and recovery evidence
```

#### 0C-bis. Implementation Alternatives

| Approach | Effort | Risk | Decision |
|---|---:|---|---|
| A. Remove `on_timeout()`, default fail session | S | Hides physical unknown state and can allow unsafe continuation | Rejected |
| B. Platform timeout -> `MANUAL_HOLD` + resource isolation + resolve API | M | More scope, but closes operational risk | Accepted |
| C. Generic Runtime Terminal Policy engine now | L | Cleaner long term, too broad for this PR | Defer to matrix as documented policy, not full engine |

#### 0D. Mode-Specific Analysis

Mode: **Selective Expansion**. The accepted expansion is in blast radius and necessary for safety: execution timeout must isolate affected resources and provide a minimal resolve path. Full dashboard, PLC integration, and generic policy engine are deferred.

#### 0E. Temporal Interrogation

| Time | Expected behavior |
|---|---|
| Hour 1 | `TIMER_TIMEOUT` no longer calls plugin. Session leaves wait state and enters `MANUAL_HOLD` with reconciliation evidence. |
| Hour 2 | HTTP no-ACK retry exhausted does not create `TIMER_TIMEOUT`; it enters `OUTBOX_DISPATCH_FAILED` / `COMMAND_ACK_EXHAUSTED` communication ACK reconciliation. |
| Hour 6 | WorkLine rejects new admission/new HTTP dispatch; upstream commands already in `ACK_RECEIVED` can finish and park their next outbox as `BLOCKED_RESOURCE`. |
| Day 2 | Late callback updates evidence only. Operator can resolve as completed/failed/cancelled, then parked outbox resumes automatically. |
| Month 6 | Timeout, ESTOP, dispatch exhausted, manual cancel all fit the same terminal policy vocabulary. |

#### CEO Dual Voices

**CLAUDE SUBAGENT (CEO):** Critical concern: execution timeout after ACK means physical state is unknown; failing session without resource inhibit can allow the next command into a device that is still executing. Recommended resource-level inhibit, neutral timeout code, atomic timeout/callback race protection, and named late callback guard owners.

**CODEX SAYS (CEO):** Critical concern: the plan solved plugin boundaries but not the physical-world reconciliation problem. Recommended reframing as execution uncertainty governance with resource isolation, operator reconciliation, late-result accounting, and vendor idempotency validation.

```text
CEO DUAL VOICES — CONSENSUS TABLE
═══════════════════════════════════════════════════════════════
  Dimension                            Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ────── ─────────
  1. Premises valid?                   partial partial DISAGREE
  2. Right problem to solve?           partial partial USER CHALLENGE
  3. Scope calibration correct?        no      no     USER CHALLENGE
  4. Alternatives sufficiently explored?no      no     CONFIRMED GAP
  5. Operational risks covered?        no      no     CONFIRMED GAP
  6. 6-month trajectory sound?         partial partial CONFIRMED WITH FIX
═══════════════════════════════════════════════════════════════
```

#### User Challenge Resolution

**Challenge:** Replace “execution timeout default failure” with “physical state unknown, resource isolation, operator reconciliation.”
**User decision:** Accepted recommended direction.
**Applied change:** The plan now uses `RECONCILING`, session `MANUAL_HOLD`, device `ERROR/CALLBACK_DEADLINE_EXPIRED` or `ERROR/OUTBOX_DISPATCH_FAILED`, safe parked outbox, late callback/dispatch evidence, and a minimal resolve API.

#### CEO Review Sections

| Section | Result |
|---|---|
| Architecture | 1 critical gap found: timeout needed resource isolation, not only session terminal write. Fixed in D3/D3b/D9. |
| Error & Rescue | 5 rescue paths mapped: no ACK retry, no-ACK exhausted communication reconciliation, no Callback reconciliation, late callback evidence, operator resolve. |
| Security & Threat Model | Resolve API 必须使用显式 `biz:workline:resolve-reconciliation` 权限；callback 需要校验 command/device/workline 与 API app scope。 |
| Data Flow & Edge Cases | Main edge case is timeout/callback race; fixed by atomic compare-and-set requirement. |
| Code Quality | DRY risk: repeated ESTOP/timeout terminal logic. Fixed by adding strategy matrix as required vocabulary. |
| Tests | Existing test plan was too failure-centric. Updated to reconciliation, blocking, resolve, late evidence. |
| Performance | No broad perf risk; dispatcher retry schedule must not create retry storms. |
| Observability | Timeline/diagnostic was observation only. Updated to required operator action and evidence. |
| Deployment | Breaking SDK removal is acceptable; project has no release compatibility requirement. |
| Long-Term | Generic policy engine deferred; matrix keeps the v1 implementation from becoming a one-off. |
| Design/UX | Skipped in CEO phase; no UI scope detected for full design review. |

#### Error & Rescue Registry

| Error path | User/system impact | Rescue path |
|---|---|---|
| HTTP no ACK | Device may not have accepted command | Retry same `command_code` with `1s/2s/4s`; exhausted -> `MANUAL_HOLD` / `RECONCILING` communication ACK reconciliation. |
| Callback deadline expired | Physical state unknown | WorkLine `RECONCILING`, device `ERROR`, session `MANUAL_HOLD`, operator resolve. |
| Late success callback | Real result arrives after timeout | Record evidence, do not auto-complete; operator may resolve as completed. |
| Late failed callback | Device reports failure after timeout | Record evidence, operator may resolve as failed/cancelled. |
| Timeout/callback race | Double state transition risk | Atomic claim/compare-and-set; loser becomes evidence/skipped terminal. |

#### Failure Modes Registry

| Failure mode | Severity | Mitigation |
|---|---|---|
| WES fails session while device still moving | Critical | Replaced default failure with resource isolation and reconciliation. |
| Retry creates duplicate physical command | High | Same `command_code`, no new command, vendor idempotency validation. |
| Late callback overwrites terminal state | High | Named callback ingress and device command guards. |
| WorkLine starts new physical work during unknown state | High | `RECONCILING` blocks new admission/new HTTP dispatch while allowing upstream `ACK_RECEIVED` work to park safely. |
| Operator has no recovery action | High | Minimal resolve endpoint with checklist. |

#### NOT In Scope

- Full timeout operations dashboard and MTTR reporting.
- Automatic device cancel command.
- PLC/hardware status reconciliation.
- Generic Runtime Terminal Policy engine implementation.
- Business-specific plugin timeout handlers.

#### What Already Exists

- `TimeoutScanner` and `TIMER_TIMEOUT` inbox generation.
- Session wait fields and `MANUAL_HOLD`.
- Device `ERROR` / `maintenance_mode` governance.
- WorkLine runtime status and dispatcher safety gate.
- Manual operation API patterns.
- Callback log and trace diagnostic infrastructure.

#### Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | CEO | Upgrade execution timeout to reconciliation + isolation | User Challenge accepted | Completeness | Both independent voices identified direct failure as unsafe because physical state is unknown. | Default session failure |
| 2 | CEO | Keep ACK retry but scope it to no-ACK transport retry | Mechanical | Explicit over clever | Whitepaper states 10s no-ACK retry; execution timeout must not reuse that policy. | Retrying physical command after Callback timeout |
| 3 | CEO | Defer generic Runtime Terminal Policy engine | Taste | Pragmatic | Matrix vocabulary gives long-term shape without making this PR a framework rewrite. | Building full policy engine now |
| 4 | Eng | Anchor execution timeout to ACK | Mechanical | Correct boundary | `COMMAND_RESULT` wait must save `current_wait_timeout_seconds` and activate `deadline_at` only after command ACK; scanner must not treat no-ACK as execution timeout. | Deadline from dispatch attempt time |
| 5 | Eng | Make timeout scanner get-or-create and payload-rich | Mechanical | Idempotence | Repeated scans and race retries need stable idempotency key plus wait/command/device/ACK snapshot. | Direct insert with sparse payload |
| 6 | Eng | Guard callback before command/device/outbox mutation | Mechanical | Race safety | Late callback must become evidence before it can clear device error or advance command terminal. | Update command then detect reconciliation |
| 7 | Eng | Add explicit resolve permission | Mechanical | Least privilege | Resolving physical unknown state is broader than ordinary workline update. | Reuse `biz:workline:update` |
| 8 | Eng | Use triggering session as hold owner | Taste | KISS/YAGNI | The triggering session already has identity, workline, command, device, and lifecycle; a separate reconciliation table adds coordination without a v1 need. | New reconciliation table |
| 9 | Eng | Promote runtime reconciliation state to session fields | Mechanical | Single fact source | Pending/resolve/late-callback guards need indexed CAS/query fields, not hidden JSON control state. | `context_json` as control state |
| 10 | Eng | Centralize reconciliation lifecycle in `WorklineRuntimeReconciliationService` | Mechanical | DRY/SOLID | Worker/API/callback/dispatcher share one state machine and race guard. | Each entrypoint implements its own checks |
| 11 | Eng | Remove outbox ACK state | Mechanical | Single fact source | `DeviceCommand.status/ack_received_at` is the only ACK truth source; outbox remains dispatch queue state only. | `OutboxStatus.ACKED` / callback-marked outbox ACK |
| 12 | Eng | Isolate no-ACK retry exhausted | Mechanical | Physical uncertainty | Exhausted HTTP no-ACK cannot prove the device ignored the command; it must stop automatic dispatch and require operator reconciliation without becoming `TIMER_TIMEOUT`. | Plain outbox failed with session dangling/failed |
| 13 | Eng | Resolve command and session together | Mechanical | Consistent terminal state | `reconciliation_command_id` must not remain active after owner session is resolved. | Session-only resolve |
| 14 | Eng | Release parked outbox atomically | Mechanical | Idempotent recovery | Parked outbox should re-enter dispatch as the same not-yet-ACKed work item with clean retry/error state. | Status-only release or new command/outbox |

### Phase 2: Engineering Review

**Scope result:** The plan is implementable, but only if the execution timeout boundary is narrowed to “`DeviceCommand.ACK_RECEIVED` command missing Callback.” Existing code has enough primitives, yet several current defaults point the wrong way: wait deadline can be written before dispatch ACK, WorkLine safety only blocks `ESTOPPED`, outbox retry uses different backoff semantics, and callback handling can mutate command/device state before runtime reconciliation guards run.

#### Eng Architecture Graph

```text
Plugin wait declaration
  -> session WAITING_DEVICE_RESULT + wait token + awaiting_command_id
  -> outbox dispatcher
      -> no HTTP 200 in 10s: retry same command_code, 1s/2s/4s
      -> retry exhausted: OUTBOX_DISPATCH_FAILED / COMMAND_ACK_EXHAUSTED hold, no TIMER_TIMEOUT
      -> HTTP 200 ACK: command ACK_RECEIVED, execution deadline anchored
  -> TimeoutScanner
      -> only ACK_RECEIVED waits with expired deadline
      -> get-or-create TIMER_TIMEOUT inbox with wait/command/device/ACK snapshot
  -> ProcessInboxMessages
      -> system timeout handler, not plugin orchestrator
      -> MANUAL_HOLD + RECONCILING + Device ERROR
  -> Other sessions on same WorkLine
      -> ACK_RECEIVED upstream commands finish normally
      -> next outbox parked as BLOCKED_RESOURCE
  -> Callback ingress
      -> reconciliation guard first
      -> late callback evidence only
  -> Resolve API
      -> explicit permission + checklist + audit
      -> owner session terminal + resource release + parked outbox resume
```

#### Eng Findings

| Severity | Finding | Plan change |
|---|---|---|
| Critical | ACK boundary was underspecified. A session can start waiting before HTTP ACK, so scanner could misclassify dispatch no-ACK as execution timeout or shorten the execution window. | Added D2b, `current_wait_timeout_seconds`, ACK-activated `deadline_at`, and scanner/handler ACK gate. |
| Critical | Timeout/callback race crosses transactions. Callback can update command/device/outbox before worker orchestration sees reconciliation. | D7 now requires guard before inbox creation and before command/device/outbox mutation. |
| High | Timeout scanner idempotency was too weak. Direct creation can collide or generate noisy errors on repeated scans. | Runtime core now requires get-or-create and composite idempotency key. |
| High | Timeout payload lacked enough facts for CAS and diagnostics. | Payload now includes wait token, command, device, command status, ACK snapshot. |
| High | replay/manual operation could bypass pending reconciliation. | Operation service/API now rejects replay/manual resume/manual cancel while pending runtime reconciliation. |
| High | Resolve permission was too broad. | D9 and API implementation now use `biz:workline:resolve-reconciliation`. |
| High | Runtime reconciliation control state in JSON would make guard/CAS/query logic duplicate parsers. | Promoted pending/resolved/core reconciliation facts to `WorklineSession` fields; JSON is evidence only. |
| High | Runtime reconciliation lifecycle was spread across worker/API/callback/dispatcher entrypoints. | Added `WorklineRuntimeReconciliationService` as the single domain coordinator. |
| High | Outbox `ACKED` duplicated and conflicted with `DeviceCommand.ACK_RECEIVED`. | Plan now removes `OutboxStatus.ACKED` and `mark_as_acked_by_dispatch_key()`; sandbox ACK and real ACK write command only. |
| High | Retry exhausted as plain outbox failure would leave session hanging or allow unsafe continuation. | Added `handle_dispatch_ack_exhausted()` and communication ACK reconciliation with `OUTBOX_DISPATCH_FAILED` / `COMMAND_ACK_EXHAUSTED`. |
| High | Resolve could leave `DeviceCommand` active while session is terminal. | Resolve now terminalizes both owner session and `reconciliation_command_id` command. |
| High | Parked outbox release semantics were underspecified. | Added `release_blocked_by_reconciliation_session()` with owner matching, retry/error reset, blocked field cleanup, and no new command/outbox. |
| Medium | Dispatcher retry constants diverged from whitepaper. | Dispatcher retry section now fixes 10s ACK timeout, 3 retries excluding first attempt, `1s/2s/4s`. |
| Medium | Diagnostic codes were overloaded. | D8 and dispatcher section split `OUTBOX_ACK_TIMEOUT`, `OUTBOX_DISPATCH_FAILED`, `INBOX_PROCESSING_TIMEOUT`, `CALLBACK_DEADLINE_EXPIRED`. |

#### Eng Dual Voices

**CLAUDE SUBAGENT (ENG):** Critical concerns were ACK boundary, timeout/callback race, scanner idempotency, sparse payload, manual/replay bypass, permission granularity, and callback trust boundary. Recommended adding ACK anchoring, first-class reconciliation guards, get-or-create scanner semantics, and explicit tests for each race.

**CODEX LOCAL INSPECTION (ENG):** The repo confirms the same risks: `_apply_wait_transition()` writes `deadline_at` during wait setup, `TimeoutScanner` calls `create_timeout_inbox()`, `assert_accepting_work()` only checks `ESTOPPED`, dispatcher retry/backoff differs from the whitepaper, and operation routes currently rely on `biz:workline:update` for broad manual actions.

```text
ENG DUAL VOICES — CONSENSUS TABLE
═══════════════════════════════════════════════════════════════
  Dimension                            Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ────── ─────────
  ACK boundary safe?                   no      no     MUST FIX
  Timeout/callback race covered?       no      no     MUST FIX
  Recovery contract implementable?     yes     yes    CONFIRMED
  Existing primitives sufficient?      yes     yes    CONFIRMED
  Test plan strong enough?             partial partial STRENGTHENED
═══════════════════════════════════════════════════════════════
```

#### Eng Test Flow

```text
no-ACK path
  dispatch attempt -> 10s timeout -> retry same command_code
  -> exhausted -> OUTBOX_DISPATCH_FAILED / COMMAND_ACK_EXHAUSTED hold

ACK_RECEIVED execution timeout path
  ACK_RECEIVED command -> deadline expired -> TIMER_TIMEOUT inbox -> MANUAL_HOLD/RECONCILING/ERROR
  -> upstream sessions finish ACK_RECEIVED commands -> next outbox parked -> resolve -> parked outbox dispatchable

race path
  timeout handler and callback arrive together -> one CAS wins -> loser records skipped/evidence

late callback path
  pending reconciliation -> callback guard -> evidence only -> operator resolve decides terminal state
```

#### Eng Failure Modes Registry

| Failure mode | Mitigation added |
|---|---|
| no-ACK incorrectly becomes Callback timeout reconciliation | ACK-activated `deadline_at` plus ACK gate before scanner/handler creates or processes timeout. |
| no-ACK retry exhausted becomes plain outbox failure | `handle_dispatch_ack_exhausted()` moves session/workline/device into communication ACK reconciliation. |
| duplicate timeout inbox storm | Composite idempotency key and get-or-create scanner behavior. |
| late callback clears device error | Callback guard before command/device/outbox mutation. |
| manual/replay resumes unknown physical task | Operation service rejects all non-resolve recovery paths while reconciliation pending. |
| overly broad permission resolves reconciliation | Explicit `biz:workline:resolve-reconciliation` permission and audit. |

### Phase 3: DX Review

**Mode:** DX POLISH. This is not a public SDK compatibility promise, but it is a developer-facing runtime boundary change. The plan must make the new mental model obvious to plugin authors, backend implementers, and operators.

#### Developer Persona Card

| Persona | Goal | Friction to remove |
|---|---|---|
| Plugin author | Declare waits and device result handling without owning platform safety policy | They must not search for a replacement `on_timeout()` hook. |
| Runtime backend engineer | Implement scanner/dispatcher/callback/resolve paths consistently | They need exact state names, permissions, and tests for race cases. |
| Operator/debugger | Understand why a WorkLine is blocked and what action clears it | UI/API must say “manual reconciliation required,” not just “timeout.” |

#### DX Journey Map

| Stage | Desired experience | Plan requirement |
|---|---|---|
| Discover | Reads plugin docs and sees timeout is platform-owned | Remove `on_timeout()` from templates, diagrams, architecture docs. |
| Migrate | Old `@on_timeout()` code fails loudly at startup | Add explicit registry/import error message with migration hint. |
| Implement | Backend engineer can follow one state table | Keep policy matrix, diagnostic table, and test names aligned. |
| Debug | A timeout tells the user which clock expired | Trace distinguishes no-ACK, inbox processing timeout, callback deadline. |
| Recover | Operator sees one safe action | Resolve API and UI copy say it will not resend command or run plugin. |

#### DX Findings

| Severity | Finding | Plan change |
|---|---|---|
| High | Removing `on_timeout()` without a migration error would create confusing import/registration failures. | Added explicit startup/registry error and before/after migration note. |
| High | Existing docs contain `TIMER_TIMEOUT -> on_timeout()` diagrams and `DEVICE_TIMEOUT` timeout advice. | Added concrete doc cleanup targets and verification searches. |
| High | Operators could misread `WAIT_TIMEOUT` as ordinary failure. | Frontend/docs now require `CALLBACK_DEADLINE_EXPIRED` explanation and resolve copy. |
| Medium | Implementers need a single source for new strings. | DX section requires enum/model source for diagnostics, status, permission, schema. |
| Medium | Trace output could force frontend to infer required action from text. | Plan requires `required_operator_action` or equivalent structured field. |

#### DX Scorecard

```text
DX PLAN REVIEW — SCORECARD
═══════════════════════════════════════════════════════════════
  Dimension                 Score  Notes
  ───────────────────────── ────── ───────────────────────────
  Getting Started           8/10   Clear migration, still needs implementation docs update.
  Conceptual Model          9/10   ACK retry vs Callback reconciliation now explicit.
  Error Messages            8/10   Startup error and diagnostic split specified.
  Debugging                 9/10   Trace/evidence/operator action requirements added.
  Migration                 8/10   Breaking removal accepted; before/after note required.
  Long-Term Maintainability 8/10   Policy matrix helps; full policy engine deferred.
  DX Measurement            7/10   Verification searches exist; no telemetry metric yet.
  Overall DX                8/10   Solid for an internal pre-release system.
═══════════════════════════════════════════════════════════════
```

#### DX Implementation Checklist

- Remove `on_timeout()` from SDK docs, plugin templates, diagrams, and null/sample plugins.
- Add one migration note in backend docs explaining ACK retry vs Callback reconciliation.
- Add explicit error text for removed decorator/import/registration path.
- Document new resolve API permission, safe parked outbox release behavior, and response examples.
- Show `reconciliation_*` fields and late callback evidence in trace examples.
- Add tests whose names encode the main user-facing distinctions.

#### Decision Audit Trail Addendum

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 8 | DX | Fail loudly for removed `on_timeout()` usage | Mechanical | Zero friction at failure | Plugin authors should get the migration answer at startup, not after reading source. | Silent AttributeError/import error |
| 9 | DX | Require structured operator action in trace | Taste | Make states inspectable | Frontend and operators should not infer recovery action from localized strings. | UI-only text mapping |
| 10 | DX | Update concrete docs, not only implementation code | Mechanical | Journey wholeness | Existing diagrams and diagnostics docs currently teach the old model. | Leaving stale docs for later |

## GSTACK REVIEW REPORT

| Review | Status | Findings | Resolution |
|---|---|---:|---|
| CEO | Completed | 1 critical scope challenge | Accepted: execution timeout is physical-state-unknown reconciliation, not default failure. |
| Design | Skipped | 0 | No full UI design scope; frontend follow-up limited to labels and minimal resolve entry. |
| Engineering | Completed | 14 | Added ACK anchoring, runtime reconciliation owner/state/service, dispatch ACK exhausted hold, command/outbox single truth sources, atomic parked outbox release, race guards, permission boundary, tests. |
| DX | Completed | 5 | Added migration errors, doc cleanup targets, trace/operator-action requirements. |
| Autoplan voices | Completed with fallback | 1 unavailable process | CEO used dual voices; Eng used subagent plus local repo inspection after one Codex shell session disappeared. |
