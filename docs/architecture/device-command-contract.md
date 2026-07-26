---
status: Phase 0 设备接入合同
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/archive/specs/2026-06-25-workline-restructuring-phase-0-spec.md
authority: docs/integration/third_party_integration_whitepaper.md
related: docs/architecture/target-state-contract.md
note: |
  本合同以第三方设备白皮书为权威输入，锁定 ECS/设备上位机接入边界。
  字段白名单可被 scripts/architecture-guardrails.sh C4 规则扫描。
  DeviceDispatchPolicy 调度细节、status snapshot TTL 留 Phase 1 device-dispatch-policy-spec.md。
---

# ECS 设备接入边界合同（P0-005）

> 权威输入：`docs/integration/third_party_integration_whitepaper.md`（Command-Ack-Callback 异步机制）
> 父设计：主计划 §9.6 device 设备接入域
> 目标态合同：`target-state-contract.md` §6 WMS/RCS 集成边界

## 1. 编写目的

锁定 WES 与 ECS/设备上位机的接入边界，使后续 DeviceCommand 实现（Phase 1 CEO-010）无需回读白皮书和顶层设计即可判断字段、状态和调度约束。

## 2. 核心原则（来源白皮书 §1.3）

| # | 原则 | 合同约束 |
| --- | --- | --- |
| 1 | WES 主导权 | WES 是指令发起方，设备是执行方；WES 不被设备反向驱动 |
| 2 | 零代码适配 | WES 不为特定供应商开发驱动；供应商适配 WES 标准协议 |
| 3 | 异步机制 | Command → Ack → Callback；避免长连接阻塞 |
| 4 | 幂等性 | 设备端必须处理重复指令（`command_code` 去重），防止物理动作重复执行 |

## 3. Command-Ack-Callback 闭环

| 阶段 | 方向 | 行为 | 语义 |
| --- | --- | --- | --- |
| **Command** | WES → ECS | WES 调用 ECS `Receive Command`（`POST /api/v1/device/command`） | 下发业务命令 |
| **Ack**（同步响应） | ECS → WES | HTTP `200 Accepted` | **只表示收到/接受，不代表任务完成** |
| **Callback**（异步结果） | ECS → WES | ECS 调用 WES `/api/v1/callback/result` 回传 `command_code` 结果 | 动作完成事实只能由 callback 推进 |

**约束**：
- DeviceCommand 下发后只以 ECS HTTP `200` 表示"收到并接受"，不代表动作完成
- 动作完成必须由 `/api/v1/callback/result` 回传 `command_code` 后推进
- 同一 `command_code` 重试不得触发重复物理动作；WES 侧保留 `request_hash` 和 `idempotency_key`，ECS 侧按白皮书缓存最近 1 小时 `command_code`

## 4. Event_Push 响应约束

| 约束 | 要求 |
| --- | --- |
| Event_Push 响应 | **固定 ACK**，不允许 action/command-like 字段 |
| 后续动作 | 必须经 `RuntimeIntentLog` + `DeviceCommand` 下发，保证可追踪、可幂等、可审计 |
| 缺 `event_id` | 只落 evidence + diagnostic，**不推进** session/projection 归属 |

**Event_Push 入站处理**（主计划 §9.6）：
- `/api/v1/callback/event` 只 ACK，不允许在响应体中返回下一步动作
- 缺 `event_id` 或乱序 `sequence_no` 的事件只落 evidence + diagnostic，不直接推进 session 或 projection
- Runtime 后续动作通过 `DeviceCommand` 下发

> Event_Push 响应拦截 command-like 字段由 P0-007 guardrail I8/R-I3a 覆盖（`Event_Push` 响应 schema 固定为 ACK）。

## 5. DeviceCommand 字段白名单

### 5.1 顶层字段（来源白皮书 §3.1.1 Receive Command）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `device_code` | yes | 目标设备编码 |
| `command_code` | yes | 全局唯一指令编码（必须用于去重） |
| `task_type` | yes | 指令类型（PUT / PICK / SCAN 等） |
| `priority` | yes | 优先级（1-10，10 最高） |
| `timeout` | yes | 期望完成时间（ms） |
| `timestamp` | yes | 下发时间戳 |
| `params` | yes | 业务参数对象（随 task_type 变化） |

**包络约束**：顶层仅允许放置协议控制字段；所有业务参数**必须**放入 `params` 对象。**严禁**将业务参数拍平到顶层；若未遵循该包络，WES 视为协议不合规。

### 5.2 内部字段（DeviceCommand 模型，来源主计划 §9.6）

| 字段 | 说明 |
| --- | --- |
| `device_code` | 设备编码 |
| `command_code` | 幂等指令编码 |
| `task_type` | 业务命令类型 |
| `payload` | 业务参数（对应白皮书 `params`） |
| `correlation_id` | 跨域 correlation key（替代 `session_id`/`session_id_int`，见 P0-004 §4.4） |
| `deadline` / `ack_deadline_at` | 完成与 ACK 截止时间 |
| `idempotency_key` | 幂等键 |
| `lease` | 租约 |
| `ack_status` | ACK 状态 |

### 5.3 禁止字段（C4 guardrail 扫描目标）

DeviceCommand / manifest / runtime schema **不得包含**以下字段（来源主计划 §9.6 + §7.5 C4）：

| 禁止字段 | 原因 |
| --- | --- |
| PLC 点位 | WES 不与 PLC 通讯 |
| 物理坐标（`coordinate` / `x_coord` / `y_coord`） | WES 不下发物理坐标 |
| 关节角度（`joint`） | WES 不控制运动学 |
| 速度曲线 | WES 不控制运动参数 |
| 安全回路（`safety_loop`） | 硬件防呆由 ECS 自主完成 |
| 急停复位指令 | 恢复必须来自 ECS 状态回传或人工 reconcile |

> C4 guardrail 命令：`rg -n "(plc|coordinate|joint|axis|x_coord|y_coord|safety_loop)" src/app/device src/app/workline src/app/runtime`

**WES 下发的是 `task_type + params` 业务命令，只包含逻辑位置和业务参数**。WES 不与 PLC 通讯，不下发 PLC 点位、物理坐标、关节角度、速度曲线、安全回路或急停复位指令。

## 6. 设备状态机（来源主计划 §9.6 M6 回归）

### 6.1 完整 6 态

| 状态 | 含义 | 可下发 |
| --- | --- | --- |
| `IDLE` | 设备空闲 | ✅ |
| `RUNNING` | 设备执行中 | ❌（有界等待） |
| `ERROR` | 设备故障 | ❌（短退避后 RuntimeHold） |
| `OFFLINE` | ECS 明确回传离线 | ❌（短退避后 RuntimeHold） |
| `UNKNOWN` | WES 未拿到有效 ECS 状态，或状态快照过期且查询失败 | ❌（只能重查 ECS 或等待状态事件） |
| `MAINTENANCE` | 设备本地或 ECS 标记维护中 | ❌（跳过该设备） |

**`OFFLINE` 与 `UNKNOWN` 区别**（主计划 §9.6）：
- `OFFLINE`：ECS 明确回传离线
- `UNKNOWN`：WES 无法确认（未拿到有效状态，或快照过期且查询失败）
- 二者都不能派发，但告警和排障路径不同

### 6.2 状态快照 TTL

| 项 | 规则 |
| --- | --- |
| `status_snapshot_ttl_ms` | 由 manifest 或 `DeviceDispatchPolicy` 定义；默认 1000ms |
| 快照过期 | 必须重新查询 ECS，查询失败按状态查询超时处理 |
| `DeviceRuntime.status_valid_until` | 快照有效期字段；`now > status_valid_until` 视为过期 |

## 7. 设备准入与调度契约

### 7.1 dispatch 前准入（来源主计划 §9.6）

| 步骤 | 规则 |
| --- | --- |
| 1. 状态确认 | dispatch 前必须调用 ECS `GET /api/v1/device/status` 或读取 `now <= DeviceRuntime.status_valid_until` 的快照，确认目标设备 `status=IDLE` |
| 2. `IDLE` | 正常下发 |
| 3. `RUNNING` | Runtime 不下发命令，进入有界等待：按 `wait_poll_interval_ms` 轮询或订阅 ECS 状态变化，直到 `IDLE` 或到达 `dispatch_deadline_at` |
| 4. `ERROR` / `OFFLINE` / `UNKNOWN` / 状态查询超时 | Runtime 不下发命令，按指数退避重试（默认 1s / 2s / 4s，最多 3 次） |
| 5. `MAINTENANCE` | Runtime 选设备时跳过该设备，直到收到 `MAINTENANCE_LEFT` / `DEVICE_ONLINE` 且状态重新变为 `IDLE` |

### 7.2 等待与超时

| 情况 | 处理 |
| --- | --- |
| `RUNNING` 等待到 `dispatch_deadline_at` 仍未 `IDLE` | 写 `DeviceCommand.ack_status=TIMEOUT`，创建 `RuntimeHold`；**不进入无限排队** |
| 退避耗尽（`ERROR`/`OFFLINE`/`UNKNOWN`/查询超时） | 写 `ack_status=TIMEOUT` 或 `REJECTED`，创建 `RuntimeHold` |
| ACK deadline 到期（设备未 ACK） | Runtime 必须扫描并写 diagnostic / `RuntimeHold`；是否取消或人工恢复由 `DeviceDispatchPolicy` 和 `ReconciliationManager` 决定 |

### 7.3 DeviceDispatchPolicy 调度契约（来源主计划 §9.6）

| 项 | 规则 |
| --- | --- |
| 设备选择 | 先按 `device_role + capability_code + manifest_version` 选候选，再按 `priority + deadline + order_policy` 生成候选命令队列 |
| in-flight 限制 | 同一 `device_code` 默认 in-flight = 1；只有 manifest 显式声明并通过 ECS 能力校验后才允许提高 `concurrency_limit` |
| 多设备同能力 | 优先选 `IDLE` 且状态快照未过期的设备；若全部 `RUNNING`，只等待到最早 `dispatch_deadline_at`，不得无限排队 |
| session HOLD | session 进入 `HOLD` / `RECONCILING` / `CLOSED` 时，未下发命令必须取消或冻结；已下发命令只能等待 ECS callback 或人工 reconcile |
| PLC 级抢占 | Runtime **不做** PLC 级抢占、急停复位或运动控制；这些只能由 ECS/现场安全系统处理后以事件形式回传 WES |

### 7.4 扫码平台互锁与预取（来源主计划 §9.6）

- 分拣机北向机械臂把物料放到扫码平台后，Runtime 默认**不允许立即下发下一条取料命令**
- `source_arm_prefetch_capacity` 默认为 0：只有扫码平台状态为 `FREE`、上一物料已被扫码平台或南向机械臂接管，且相关 work item 未处于 HOLD/RECONCILING 时，才允许北向机械臂取下一件
- 若现场 ECS 支持平台外暂存、手持等待或预取缓存，必须在 WorkLine manifest 中显式声明 `source_arm_prefetch_capacity > 0`，并通过 capability admission、设备状态、超时和行为契约测试验证
- **禁止靠经验默认开启预取**

## 8. WorkLine 启停门禁（来源主计划 §9.6）

| 设备标记 | `INACTIVE → ACTIVE` | RUNNING session 期间变为 |
| --- | --- | --- |
| `required=true` | 若 `OFFLINE` / `UNKNOWN` / `MAINTENANCE`，WorkLine **不允许**启动 | Runtime 进入 `RuntimeHold`，停止新 effect，等待 ECS 恢复或人工 reconcile |
| `optional=true` | WorkLine 可启动，但对应 capability 从候选设备集中剔除，并在 `PlaneSnapshot.warnings[]` 中展示 | —— |

## 9. 安全事件边界（来源主计划 §9.6 + §7.1）

| 事件 | 处理 |
| --- | --- |
| `ESTOP_PRESSED`、安全门、光栅 | 只能由 ECS 转换为 WES event/evidence/`RuntimeHold`；进入 `RECONCILING` |
| 恢复条件 | 必须来自 ECS 状态回传或人工 reconcile |
| 硬件防呆 | 由 ECS 自主完成；WES 只根据 ECS 暴露的设备状态、Ack、Result、Event 做业务编排 |
| 物理动作停止 | WES 只能按 ECS 支持的 Cancel Command 请求取消仍在排队/执行的业务命令；是否能安全停止由 ECS/现场安全系统决定 |

**WES 的隔离动作是软件层禁发、hold、告警和证据冻结，不直接控制 PLC 或安全回路**。

## 10. DeviceEvent 字段（来源主计划 §9.6）

| 字段 | 说明 |
| --- | --- |
| `device_code` | 设备编码 |
| `event_type` | 事件类型 |
| `event_id` | 事件 ID（**缺失则不推进业务**，只落 evidence） |
| `sequence_no` | 序列号（乱序只落 evidence + diagnostic） |
| `payload` | 事件载荷 |
| `source_event_id` | 来源事件归因 |

## 11. 验收（SPEC P0-005）

1. ✅ DeviceCommand 字段白名单可被 `architecture-guardrails.sh` C4 规则扫描（§5.3 禁止字段 + §5.1 顶层白名单）
2. ✅ `data.event_id` 缺失时，不允许推进异步链路归属（§4 Event_Push + §10 DeviceEvent）
3. ✅ 不建立 WES 直连 PLC 的任何字段或 adapter（§5.3 禁止字段 + §9 安全事件边界）

## 12. 后续 Phase SPEC

| Phase | SPEC | 本合同锁定项 |
| --- | --- | --- |
| Phase 1 | `device-dispatch-policy-spec.md` | DeviceDispatchPolicy 能力选择、deadline、status snapshot TTL、取消 |
| Phase 1 CEO-010 | DeviceCommand ECS API contract + manifest concurrency limit | 字段白名单、6 态、Command-Ack-Callback 闭环 |
