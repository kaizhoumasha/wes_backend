# Command 下发后 Session 等待态设计

## 背景

`docs/integration/third_party_integration_whitepaper.md` 将第三方设备接入定义为标准异步链路：

`Command -> Ack -> Callback`

其中 ACK 只表示设备同步收到并接受任务，不表示任务完成；任务完成必须由设备调用 WES 的 Result Callback 回传。当前粗分机沙箱流程出现了不一致状态：`MEASUREMENT_REEL` 指令已创建并进入 Outbox，设备也已被占用，但 `WorklineSession` 仍保持 `NEW`，导致 sandbox ACK 被 session 状态校验拒绝。

## 问题判断

这是运行时状态机缺陷，不是操作顺序问题。

设备命令已经进入 WES 派发链路后，Session 不能继续保持 `NEW`。如果 Session 不记录当前等待的 command，后续 ACK、Result、超时扫描、runtime reconciliation 都无法稳定归属到正确会话。

当前可疑根因是 RuntimeIntent 的 `COMMAND` 处理依赖 `timeout_seconds` 决定是否进入 `COMMAND_RESULT` 等待态；插件未显式传入 `timeout_seconds` 时，只创建 command/outbox，没有建立 session 等待上下文。

## 目标

1. 任何设备 `COMMAND` intent 写回后，Session 必须进入设备结果等待态。
2. ACK 重试继续使用同一个 `command_code`，保持供应商侧幂等语义。
3. ACK 前不启动业务 Result deadline；ACK 到达后再启动 Result 等待窗口。
4. 沙箱和真实设备使用同一套 Session 状态语义。
5. 保持现有 runtime reconciliation 能力：ACK 耗尽进入 `COMMAND_ACK_EXHAUSTED`，ACK 后 Result 超时进入 callback deadline 对账。

## 非目标

- 不修改第三方白皮书协议。
- 不新增设备协议字段。
- 不改变 Result Callback 的业务处理链路。
- 不通过放宽 sandbox ACK 校验绕过 Session 状态错误。
- 不引入新命令重发策略；重试必须复用原 `command_code`。

## 状态约定

### Command 写回成功后

当 Runtime 写回 `COMMAND` intent 并创建 `DeviceCommand` / `SystemOutbox` 后，立即将 Session 设置为：

- `status = WAITING_DEVICE_RESULT`
- `current_wait_type = COMMAND_RESULT`
- `awaiting_command_id = command.id`
- `waiting_since = command 创建/写回时间`
- `current_wait_timeout_seconds = 有效 Result 等待窗口秒数`
- `deadline_at = NULL`

`deadline_at` 保持为空是刻意设计：业务完成窗口从 ACK 到达后开始计算，而不是从 WES 创建 outbox 时开始计算。

### ACK 到达后

收到设备同步 ACK 后：

- `DeviceCommand.status = ACK_RECEIVED`
- 写入 `ack_received_at`、`ack_code`、`ack_message`
- `SystemOutbox.status = SENT`
- 调用现有 `activate_execution_deadline_after_ack()`，根据 `current_wait_timeout_seconds` 激活 `session.deadline_at`
- Session 保持 `WAITING_DEVICE_RESULT`，继续等待 Result Callback

### Result 到达后

设备调用 `/callback/result` 后，Runtime 通过 `command_code` 找到 command，再通过 `awaiting_command_id` 找到 Session，交给插件处理业务结果并推进下一步。

### ACK 缺失或通信超时

WES 未收到 ACK 时，Outbox 层继续按现有策略重试同一个 outbox：

- 同一个 `dispatch_key`
- 同一个 `command_code`
- 同一个业务 payload

重试耗尽后：

- `SystemOutbox.status = FAILED`
- `DeviceCommand.status = FAILED`
- `WorklineSession.status = MANUAL_HOLD`
- `reconciliation_state = PENDING`
- `reconciliation_reason = COMMAND_ACK_EXHAUSTED`
- 进入人工对账，不能再普通 ACK 这条失败 outbox

## Timeout 来源

有效 Result 等待窗口按以下优先级解析：

1. `RuntimeIntent.timeout_seconds`
2. vendor payload 顶层 `timeout` 毫秒字段换算为秒，向上取整，最小 1 秒
3. 系统默认值 `300` 秒

该值写入 `current_wait_timeout_seconds`，用于 ACK 到达后激活 Result deadline。

## 重试策略

ACK 通信重试由 Outbox 派发层负责，不由插件或 Session 生命周期重复实现。

现有策略保持：

- HTTP ACK 等待超时为 10 秒
- 失败后指数退避
- 最多重试 3 次
- 重试时不创建新 command
- 供应商设备必须按白皮书缓存 `command_code` 并实现幂等

如果设备其实已经收到第一条命令，但 ACK 响应丢失，后续重试收到同一个 `command_code` 时应返回 200，并继续原任务，不重复执行物理动作。

## 实现边界

### RuntimeIntent COMMAND 写回

修改 Runtime 的 command 写回逻辑，使所有 `COMMAND` intent 都调用统一的 command wait 逻辑。即使插件未传 `timeout_seconds`，也必须基于 payload/default 得到有效 timeout。

现有 `COMMAND` intent 在 `timeout_seconds is None` 时进入 running 并清理 wait 的行为应移除或改为仅对明确声明“无需等待 Result”的未来 intent 类型使用。本次不新增该未来能力。

### Plugin 层

不要求每个插件显式传 `timeout_seconds`。插件仍可以传入 timeout 覆盖默认值，但是否进入等待态由 Runtime 层统一保证。

### Sandbox ACK

Sandbox ACK 不放宽校验。修复后，命令下发完成时 Session 已经满足：

- `status = WAITING_DEVICE_RESULT`
- `awaiting_command_id = command.id`

因此 ACK 可以通过现有校验。

## 数据流

### 状态流转与数据流 ASCII 拓扑

```
[ 设备事件 ] ──> WES ──> [ 触发插件业务逻辑 ]
                             │
                             ▼ (返回 COMMAND intent)
                       [ WES 写入 COMMAND / OUTBOX ]
                             │
                             ▼ (统一触发 start_wait)
                       [ Session 状态更新 ]
                       ├── status = WAITING_DEVICE_RESULT
                       ├── current_wait_type = COMMAND_RESULT
                       ├── awaiting_command_id = command.id
                       ├── current_wait_timeout_seconds = 有效超时时间 (秒)
                       └── deadline_at = NULL (暂不开启业务执行倒计时)
                             │
                             ▼ (设备同步响应)
                       [ 收到设备 ACK 响应 ]
                       ├── DeviceCommand.status = ACK_RECEIVED
                       ├── SystemOutbox.status = SENT
                       └── 触发 activate_execution_deadline_after_ack()
                           └── 根据 current_wait_timeout_seconds 激活 session.deadline_at 倒计时
                             │
                             ▼ (物理动作执行完毕)
                       [ 设备异步回调 Result ] ──> WES 推进业务步骤 ──> Session 完结 (COMPLETED)
```

### 数据流详述

1. 设备事件进入 WES，Runtime 创建或复用 Session。
2. 插件返回 `RuntimeIntent.command(...)`。
3. Runtime 创建 `DeviceCommand`。
4. Runtime 创建 `SystemOutbox`。
5. Runtime 将 Session 写成 `WAITING_DEVICE_RESULT` 并记录 `awaiting_command_id`。
6. Outbox dispatcher 尝试向设备下发 command。
7. 设备返回 ACK 后，command 进入 `ACK_RECEIVED`，Result deadline 被激活。
8. 设备完成任务后调用 Result Callback。
9. Runtime 根据 Result 推进业务状态。

## 错误处理

- Command 创建失败：保持现有异常处理，不进入等待态。
- Outbox 创建失败：保持现有异常处理，不进入等待态。
- ACK 未返回：Outbox 重试，耗尽后进入 `COMMAND_ACK_EXHAUSTED`。
- ACK 后 Result 未返回：按已存在 timeout scanner 进入 callback deadline 对账。
- Result 早于 ACK 到达：保持现有回调编排防护，不在本次设计中扩大范围。

## 验收标准

1. `MEASUREMENT_REEL` 下发后，Session 不再停留在 `NEW`。
2. 未显式传 `timeout_seconds` 的 `RuntimeIntent.command(...)` 也会让 Session 进入 `WAITING_DEVICE_RESULT`。
3. Session 记录正确的 `awaiting_command_id`。
4. `current_wait_timeout_seconds` 能从 payload `timeout` 推导，默认兜底为 300 秒。
5. ACK 前 `deadline_at` 为空。
6. sandbox ACK 对新下发 command 可通过现有校验，并将 command 标记为 `ACK_RECEIVED`。
7. ACK 后 `activate_execution_deadline_after_ack()` 能设置 Result deadline。
8. ACK 重试仍复用同一个 `command_code`，不会生成新 command。
9. ACK 重试耗尽仍进入 `COMMAND_ACK_EXHAUSTED` 对账。
10. 现有 Result Callback 流程不回退。

## 测试建议

- Runtime intent effects 单元测试：COMMAND 无 `timeout_seconds` 时创建 command/outbox 后 session 进入 `WAITING_DEVICE_RESULT`。
- Timeout 推导测试：payload `timeout=300000` 时写入 `current_wait_timeout_seconds=300`。
- Sandbox ACK 服务测试：对无显式 timeout 的 command，下发后可 ACK。
- Outbox 重试回归测试：ACK 超时仍按原 command_code 指数退避并最终耗尽。
- 集成测试：粗分机 `SCAN_COMPLETED -> MEASUREMENT_REEL -> ACK -> Result` 完整链路。
- **边界与换算单元测试**（GStack 决议新增）：在 `test_runtime_intent_effects.py` 中补全插件未传 `timeout_seconds` 但 payload 含有 `timeout` 的换算测试，覆盖 `1500ms` 向上取整为 `2s`、`500ms` 限制为最低 `1s` 等边界条件。

## 风险

- 如果历史上存在“不等待 Result”的 command，本设计会改变其 Session 语义。目前白皮书要求设备任务完成必须回调 Result，本次按白皮书收敛，不保留隐式 fire-and-forget command。
- 运行时修复后，旧的错误数据不会自动修复；已经处于 `FAILED` / `MANUAL_HOLD` 的历史 session 仍需走人工对账或重新生成流程。

## GSTACK REVIEW REPORT

### 覆审就绪看板 (Review Readiness)

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 2 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**UNRESOLVED:** 0 unresolved decisions across all reviews

### 架构决议 (Architectural Decisions)

1. **D1 — 补齐 `_apply_command_wait` 中的 vendor payload timeout 解析逻辑**
   - **问题**: 原有 `_apply_command_wait` 默认只取 `intent.timeout_seconds or 300`，导致 vendor payload 中的顶层 `timeout` (ms) 声明无法生效。
   - **决议 (Option A)**: 完整实现三优先级换算规则：
     - 优先读取 `intent.timeout_seconds`。
     - 若为空，解析 `intent.payload_json` 中的顶层 `timeout`，换算为秒：`max(1, math.ceil(timeout_ms / 1000))`。
     - 最终兜底 `300` 秒。

2. **D2 — 为新的等待超时解析逻辑补充单元测试**
   - **决议 (Option A)**: 针对 1500ms 向上取整为 2s、500ms 限制最低为 1s、无超时兜底为 300s 等取整及边界进行全覆盖测试。

### 🛠 落地实施任务清单 (Implementation Tasks)

- [ ] **T1 (P1, human: ~30min / CC: ~5min)** — `workline_runtime` — 补齐 `_apply_command_wait` 中的 vendor payload timeout 解析逻辑
  - **位置**: `src/workline_runtime/runtime_intent_effects.py`
  - **细节**: 在 `_apply_command_wait` 中解析 `intent.payload_json` 顶层 `timeout` 毫秒字段。
- [ ] **T2 (P2, human: ~30min / CC: ~5min)** — `tests/workline_runtime` — 补齐 `timeout_seconds` 缺失及毫秒换算取整单元测试
  - **位置**: `tests/workline_runtime/test_runtime_intent_effects.py`
  - **细节**: 编写针对毫秒向上取整、下限保护及兜底的单元测试用例。
