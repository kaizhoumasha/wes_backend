> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: state = 原文件 §6 状态与恢复设计。

---

## 6. 状态与恢复设计

### 6.1 11 态机（外部履约）

```text
REQUESTED -> SENT -> ACCEPTED -> RUNNING -> SUCCEEDED
                         │          │
                         │          ├── FAILED
                         │          └── TIMEOUT
                         ├── REJECTED
                         └── BLOCKED_BY_CB  (新增: circuit breaker open)

任意非终态 -> CANCELLED
不可信/证据冲突 -> RECONCILING evidence + RuntimeHold  (ReconciliationManager 登记/隔离/决议)
```

**状态含义**：

| 状态 | 含义 | 终态 |
| --- | --- | --- |
| `REQUESTED` | WES 已生成搬运意图，尚未成功发出 | 否 |
| `SENT` | 已调用 Adapter，下游响应未定 | 否 |
| `ACCEPTED` | 下游接受请求 | 否 |
| `RUNNING` | 下游已开始执行 | 否 |
| `SUCCEEDED` | 下游确认完成 | ✓ |
| `REJECTED` | 下游业务拒绝（无可用货架、无空箱位、目标不合法） | ✓ |
| `FAILED` | 执行失败或技术错误 | ✓ |
| `TIMEOUT` | 执行超时（**新增**：与 FAILED 区分） | ✓ |
| `CANCELLED` | WES 或下游取消 | ✓ |
| `BLOCKED_BY_CB` | circuit breaker open 阻塞期间（**新增**：不混进 RECONCILING） | 否 |
| `RECONCILING` | WES evidence / WMS 回调 / 现场投影冲突 | 否（需产生恢复决议） |

### 6.2 4 条 timeout 转移规则

| 源态 | 触发 | 目标态 | 默认时长 |
| --- | --- | --- | --- |
| `REQUESTED` | Adapter 宕机 / CB open 持续 > 30s / 进程崩溃 | `FAILED` | 30s |
| `SENT` | 收到 `ACCEPTED/REJECTED` 之前超时 | `TIMEOUT` | 60s |
| `ACCEPTED` | 长时间无 `RUNNING` 进展 | `RECONCILING` | 5 min |
| `RUNNING` | 长时间无 `SUCCEEDED` | `RECONCILING` | 30 min |

**注**：时长按 WorkLine 配置可覆盖（不同 WorkLine 业务节奏不同）。

### 6.3 Circuit breaker 集成

`WmsFulfillmentAdapter` 持 `circuit_breaker` 状态（open / half-open / close），现有 `src/app/wms_integration/services/circuit_breaker_service.py` 实现。

| CB 状态 | 新请求行为 | 已有请求行为 |
| --- | --- | --- |
| `open` | 进 `BLOCKED_BY_CB`，不消耗 `SENT` 配额 | 继续等原状态机超时 |
| `half-open` | 限速（默认 1 req / 10s）尝试 | 继续 |
| `close` | 正常进 `REQUESTED` | 正常 |

**CB `open → half-open` 转移时**：所有 `BLOCKED_BY_CB` 请求自动恢复为 `REQUESTED` 重试（保留 `idempotency_key`）。如重试期间 `idempotency_key` hash 一致，直接返回旧 record。

**BLOCKED_BY_CB 语义（M2 回归）**：

- `BLOCKED_BY_CB` 是 circuit breaker open 期间的系统侧延迟状态，不代表 WMS/RCS/ECS 已接收或拒绝业务请求。
- `BLOCKED_BY_CB` 只适用于出站 effect dispatch，不适用于已到达 WES 的 callback/event。
- 业务查询 API 默认不把 `BLOCKED_BY_CB` 计入 in-flight fulfillment 列表；运维视图可单独展示 CB 阻塞队列。
- `BLOCKED_BY_CB` 不计入履约 P95 指标；但必须计入可观测性指标 `effect_blocked_by_cb_total` 与告警。

**CB 恢复期入站 callback 规则**：

Circuit breaker 只保护 WES 主动调用外部系统的出站链路。WMS/RCS/ECS/device callback 是现场或外部履约
evidence，不能因为出站 breaker 处于 `open/half-open` 被标成 `BLOCKED_BY_CB` 或丢弃：

- callback API 仍必须先做 HMAC、nonce、schema normalize、幂等检查，写入 `RuntimeInbox` 后 ACK。
- 同 `source_event_id + provider_code + event_type` 且同 `payload_hash` 的旧 callback 合并为既有 evidence。
- 同 key 不同 hash、callback 与 active projection 冲突、或无法解释的乱序回调进入 `RECONCILING` / `RuntimeHold`。
- 出站 `BLOCKED_BY_CB` 请求在恢复重试前，必须先重读 inbox/evidence，避免已完成的物理事实被重复下发。

### 6.4 RECONCILING 冲突决议模型（ReconciliationManager）

**触发矩阵**：

| 触发类型 | 检测源 | 默认处理 |
| --- | --- | --- |
| 投影冲突 | 同 object 在 2+ 投影源 | `RECONCILING` + `RuntimeHold` |
| External callback 与本地 projection 不一致 | callback normalize + drift detector | `RECONCILING` + audit log |
| device 事件与 handling 业务意图状态不一致 | runtime event monitor | `RECONCILING` + `RuntimeHold` |
| WMS master-data drift | reconciliation QUERY Definition + `WmsQueryExecutionPort` + `ReconciliationManager` | `RECONCILING` 分类处理（详见 §6.5） |
| `RuntimeHold` 关联的现场异常 | runtime intent effects | 创建 `RECONCILING` evidence |
| 传感器抖动 | ECS/device event 去抖窗口 | N 秒内同 sensor 同 object 合并 evidence；超阈值 `RuntimeHold` |
| 通信丢包或 callback 延迟 | deadline + provider query | 超过 TTL 后主动 query WMS/ECS；不可确认时 `RECONCILING` |
| 重复上报 | idempotency + payload_hash | 同 key 同 hash 合并 evidence；同 key 不同 hash 409 + 安全审计 |
| CB 恢复期收到旧 callback | callback 幂等 + payload_hash + projection drift detector | 同 hash 合并 evidence；冲突或无法解释的乱序进入 `RECONCILING` |

**强制动作**：

1. 创建 `RECONCILING` evidence（`detected_at` + `detected_by` + `reason`）
2. 创建/关联 `RuntimeHold`
3. 对相关 `correlation_id` / object / workline scope 加 effect 禁发闸门，不再创建新的 DeviceCommand / WMS transaction effect
4. 冻结相关 active projection 写入；只允许追加 evidence、event、diagnostic，不允许“猜测式修正”
5. 通知操作员 dashboard（事件总线 `reconciliation.conflict.detected`）
6. 写 audit log

**现场隔离语义**：

- WES 的隔离动作是软件层禁发、hold、告警和证据冻结，不直接控制 PLC 或安全回路。
- 如需停止物理动作，WES 只能按 ECS 支持的 Cancel Command 请求取消仍在排队/执行的业务命令；是否能安全停止由 ECS/现场安全系统决定。
- `ESTOP_PRESSED`、安全门、光栅等事件一律进入 `RuntimeHold + RECONCILING`，恢复条件必须来自 ECS 状态回传或人工 reconcile。
- 人工 reconcile 必须记录操作者、恢复依据、允许恢复的 object scope 和下一步 effect 范围。
- 物理现场 RECONCILING 采用 push + pull 双通道：ECS/device push 事件优先，超过 TTL 未恢复时由 WES 主动 query ECS/WMS 状态；两者不一致时保留 evidence 并继续 hold。

**恢复决议**：

`ReconciliationManager` 不直接写入 operation-specific fulfillment evidence、`HandlingOperation`、
`ExecutionSession` 或 active projection 的业务状态。它只产出
`ReconciliationRecord.resolution_decision`、追加 evidence、解除/维持 `RuntimeHold`，再由各状态
owner 按 evidence 自己转移。

| 路径 | 触发 | 决议输出 |
| --- | --- | --- |
| WMS status 重查 | operation identity 与 idempotency key 命中 | `FULFILLMENT_EVIDENCE_ACCEPTED`，由 operation-specific reducer 单调应用权威状态 |
| device 事件恢复 | runtime 检测到一致状态 | `DEVICE_EVIDENCE_ACCEPTED`，由 `DeviceCommand` / `ExecutionSession` owner 决定恢复或继续 hold |
| 人工 reconcile | 操作员确认后 close | `MANUAL_RESOLUTION_ACCEPTED`，指定允许恢复的 object scope 和下一步 effect 范围 |
| 超时升级 | `RECONCILING > 5 分钟` 告警；`> 30 分钟` 升级 P1 | `RuntimeHold` 升级 |

**告警分级**：

- `info`：瞬态冲突、同 hash 重复上报、自动恢复的 callback 延迟。
- `warn`：进入 `RuntimeHold`、设备 `UNKNOWN` 持续超过 TTL、单 WorkLine inbox 积压接近阈值。
- `critical`：`ESTOP_PRESSED`、同 object 多归属超过 `transient_until`、WMS/ECS 长时间不可用、`RECONCILING > 30 分钟`。
- 告警目标首版只写 `audit_logs` + dashboard event；外部钉钉/PagerDuty 等通知作为后续 provider adapter，不进入 P0。

**owner 转移约束**：

`RECONCILING` 不是跨域全局状态写入口。各 owner 只能根据 reconciliation evidence 自行转移：

| Owner | 允许根据 reconciliation evidence 转移到 | 禁止 |
| --- | --- | --- |
| operation-specific fulfillment reducer | `ACCEPTED` / `PROCESSING` / `COMPLETED` / `REJECTED` / `FAILED` | 直接由 `ReconciliationManager` 写 effect 状态 |
| `HandlingOperation` | `IN_PROGRESS` / `COMPLETED` / `FAILED` / `CANCELLED` / `RECONCILING` | 写入外部履约细态 |
| `ExecutionSession` | `RUNNING` / `HOLD` / `CLOSED` | 绕过 owner 直接改投影 |
| active projection | 解除冻结后由 projection writer 重放 evidence | 人工猜测式覆盖当前归属 |

### 6.5 WMS master-data drift 分类

operation-specific reconciliation QUERY 定期通过 `WmsQueryExecutionPort` 只读拉取 WMS 权威事实，
并由 `ReconciliationManager` 分类处理：

| drift 类型 | WES 处理 |
| --- | --- |
| `MISSING_IN_WMS` | 标 `RECONCILING`，等待 WMS 确认；5 分钟升级告警 |
| `RENAMED_IN_WMS` | 写 `source_version` 升级 evidence，projection 用新 code；旧 code 留 evidence |
| `METADATA_DRIFT` | 写 conflict evidence，触发人工 reconcile；30 分钟升级 P1 |

启动时跑一次 full reconcile（性能预算：1 条 WorkLine < 5 分钟）；运行期按 5 分钟周期跑增量对账。

**drift SLA 与 WMS 可用性（M10 回归）**：

- drift 恢复 SLA 与 WMS 可用性 SLA 分离；WMS 不可用期间不得把每次 reconcile 失败都升级为新的业务 drift。
- WMS 长时间不可用时进入 `DRIFT_WAITING_FOR_WMS` 降级模式：保留首个 P0/P1 告警、停止重复告警风暴、继续追加 evidence。
- WMS 恢复后，ReconciliationManager 必须从最后一次成功 `source_version` 继续增量对账，再决定是否解除 `RuntimeHold`。

### 6.6 3 路 UNION 冲突 policy

3 路 UNION（`ON_CONVEYOR` + `AT_WORK_POSITION` + `IN_TRANSFER`）现唯一约束只在各自表内生效；同一 `bin_code` 同时出现在多个来源时，**没有跨投影唯一 active 归属**。引入 `ActiveObjectRegistry` 跨投影仲裁读模型。

| 组合 | 处理 | 说明 |
| --- | --- | --- |
| `(IN_TRANSFER, ON_CONVEYOR)` 在 `handling_request.created_at + N 秒` 内 | 合法 | 物理瞬态：CTU 送入瞬间料箱已 conveyor 接住但请求未终结 |
| `(IN_TRANSFER, ON_CONVEYOR)` 超过 `N` 秒 | 进 RECONCILING | CTU 卡死或请求未终结异常 |
| `(ON_CONVEYOR, AT_WORK_POSITION)` 任何时候 | 进 RECONCILING | 料箱不能在两处 |
| `(AT_WORK_POSITION, IN_TRANSFER)` 任何时候 | 进 RECONCILING | 料箱必须先离开工作位才能搬运 |

`N` 暂取 30 秒。evidence 中通过 `transient_until` 字段区分"瞬态合法"与"真冲突"。

**非料箱冲突扩展（M5 回归）**：

- 货架维度：`RackPlacement.status=IN_TRANSIT` 后，新 placement 写入必须基于 WMS/RCS/ECS evidence；同一 `rack_code` 同时处于 2 个 `work_position_code` 直接进 `RECONCILING`。
- 命令维度：同一 `correlation_id` 下，不允许同时存在 `DeviceCommand.status=RUNNING` 与 operation-specific
  terminal result 已完成且 evidence 时间线无法解释的组合；发现后进入 `RECONCILING`。
- active 归属仲裁不只面向 `bin_code`，还必须支持 `rack_code`、`pkg_code`、`command_code` 四类 object key。

---
