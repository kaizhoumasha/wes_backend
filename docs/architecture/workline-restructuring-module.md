> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: module = 原文件 §9 模块设计。

---

## 9. 模块设计

### 9.1 workline 配置域

**职责**：滚筒线/队列/入口/出口/设备角色/资源边界/平面布局/启停配置生命周期

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `WorkLine` | `line_code`, `manifest_yaml`, `status`, `config_version` | 配置聚合根；只管理 manifest 生命周期，不保存运行状态 |
| `ConveyorLine` | `workline_id`, `code`, `layout` | 滚筒线配置来自 manifest；运行态队列 membership 不写回配置 |
| `PipelineQueue` | `conveyor_code`, `code`, `role`, `capacity`, `order_policy` | `code` 仅在当前 WorkLine manifest 内唯一，不提升为系统级 enum |
| `EntryPoint` / `ExitPoint` | `conveyor_code`, `queue_code`, `external_handler` | 表达 CTU/AGV/WMS 等外部交接点，不直接调度外部设备 |
| `Device`（配置） | `role`, `code`, `capabilities` | 只声明设备角色和能力；运行时状态归 device 域 |
| `SafetyZone` | `affected_workline_codes`, `affected_device_codes`, `affected_conveyor_codes`, `recovery_policy` | 定义故障影响范围；不包含 PLC/坐标/安全回路控制字段 |

**Manifest 扩展示意（非实现 schema）**：

```yaml
manifest_version: "2026-06-p0"
capabilities:
  - code: sorter_inbound
    requires:
      wms_ports: [master_data, inventory_query, fulfillment, event]
      device_roles: [scanner, sorter_host, conveyor_host]
      queues: [entry_buffer, scan_station, sorting_station, exit_buffer]
workflow_steps:
  - code: scan_bin
    capability: sorter_inbound
    input_queue: entry_buffer
    device_role: scanner
    emits: [bin_scanned]
  - code: request_sorting
    capability: sorter_inbound
    effect_port: wms_fulfillment
    waits_for: [bin_scanned]
```

该示意只表达配置边界：capability 声明“需要哪些 port / 设备角色 / 队列”，workflow steps 声明“Runtime 如何解释步骤和 effect”。`workflow_steps` 只能声明 capability 模板的步骤绑定、队列、设备角色和 effect port；不得包含条件表达式、脚本、provider 字段映射或状态机逻辑。复杂流程留在 runtime capability 代码和 Phase SPEC 中。WorkLine manifest 不保存运行状态，不直接调用 WMS/ECS，也不把 provider DTO、HTTP client 或状态机实现写入配置。

**API**（v1 router，`src/app/workline/v1/`）：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/worklines/` | GET | 列出 WorkLine |
| `/worklines/{id}` | GET | WorkLine 详情（含 manifest） |
| `/worklines/{id}/manifest` | GET | manifest YAML |
| `/worklines/{id}/queue-memberships` | GET | 队列 active memberships（经 `ConveyorQueueMembership` 查询服务） |
| `/worklines/{id}/plane/scene` | GET | PlaneSceneView（详见 §5.2） |
| `/worklines/{id}/plane/snapshot` | GET | PlaneSnapshot（详见 §5.2） |

**Boot-time manifest validator**：

WorkLine 激活时**必须**运行：

1. 拉 `ConveyorQueueMembership.ACTIVE` 集合的 `queue_code`
2. 与 manifest `pipeline_queues.code` 集合做集合差
3. 不匹配行打 `warnings[].code=STALE_QUEUE_CODE`，**不**删除
4. 未知队列写入尝试直接拒绝并生成 RuntimeHold
5. 校验 required device role、capability、SafetyZone 归属和 shared-device 影响范围；缺失或冲突时拒绝激活

**SafetyZone / shared-device 拓扑**：

- WorkLine manifest 必须能描述设备、滚筒线、缓存位、机械臂、共享输送线与 `SafetyZone` 的归属关系。
- 同一设备或输送线被多个 WorkLine 共享时，运行时调度必须按 `SafetyZone` 计算影响范围；设备进入 `ERROR` / `ESTOP_PRESSED` / `MAINTENANCE` 时，只冻结受影响的 session/effect，不用整库停摆。
- `ESTOP_PRESSED`、安全门、光栅等物理安全事实只能由 ECS/device event 进入 WES；WES 不复位 PLC，不解除硬件急停，只记录 evidence、停止新 effect 并等待 ECS 状态恢复或人工 reconcile。
- `SafetyZone` 不替代 `DeviceDispatchPolicy`。前者定义影响范围，后者定义 dispatch 排队、限流、deadline 和取消。

**Manifest 版本冻结**：

- `ExecutionSession` 创建时必须记录 `manifest_version = WorkLine.config_version`。
- session 生命周期内的队列、入口、出口、设备角色、容量、平面布局解析都以 pin 住的 `manifest_version` 为准。
- WorkLine manifest 更新只影响新 session；已有 RUNNING/HOLD session 不热切到新配置。
- 需要现场热切换时，必须走 `DRAINING -> HOLD -> VALIDATE -> ACTIVATE` 流程：停止为旧版本创建新 effect，等待在途命令完成或人工 hold，验证新 manifest 后再创建新 session。
- manifest validator 必须同时支持 boot-time 与 activation-time；activation-time 失败不得污染 active projection。

**DRAINING / HOLD / VALIDATING 边界（M3 回归）**：

- `DRAINING` 是 WorkLine 配置域状态：停止为旧 manifest 创建新 session 或新 effect，等待旧 session 自然结束或被人工 hold。
- `HOLD` 是 ExecutionSession 状态：暂停某个 session 的新 effect，不代表整条 WorkLine 不可用。
- `VALIDATING` 是 WorkLine manifest 激活前临时状态：只运行 manifest validator、资源边界校验和 active projection 污染检测。
- 热切换流程固定为 `WorkLine.ACTIVE -> DRAINING -> session.HOLD(per active session) -> VALIDATING -> ACTIVE`；失败则回到 `MAINTENANCE` 并保留旧 manifest_version 的 evidence。

### 9.2 runtime/orchestration 执行域

**职责**：ExecutionSession/Inbox/Timeline/Hold/EffectPort；RuntimeIntentLog；ExecutionCorrelation

**域演化预留**：当前 7 个核心实体集中在 runtime/orchestration 单域，Phase 2 完整迁移时若发现职责密度过高（worker 抢占、claim 锁竞争、Outbox dispatch 与 session 聚合事务冲突），允许按以下边界拆分为子域，**不视为破坏目标态契约**：

- `runtime/orchestration` 保留 ExecutionSession + ExecutionCorrelation + RuntimeTimeline + RuntimeHold（session 聚合根 + 状态源）
- `runtime/messaging` 承担 RuntimeInbox + dead-letter + 人工重放（入站消息边界）
- `runtime/intent_ledger` 承担 RuntimeIntentLog + EffectPort + 崩溃重放（出站效果边界）

拆分触发条件由 Phase 2 benchmark gate（§8.1 RuntimeInbox claim、Outbox dispatch 基准）决定；不拆分时三类实体仍在单域 + 单 ADR 描述。拆分发生后跨子域引用通过 `execution_session_id`（runtime/orchestration 内部）或 `ExecutionCorrelation.correlation_id`（messaging/intent_ledger ↔ 业务域），不引入新跨域 FK。

**核心实体**：

runtime 域内表使用 `execution_session_id` 作为 `ExecutionSession` FK；跨域实体只允许持有 `ExecutionCorrelation.correlation_id`。

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `ExecutionSession` | `workline_id`, `manifest_version`, `state`, lifecycle timestamps | 唯一 session 聚合根；创建时 pin WorkLine manifest version |
| `RuntimeInbox` | `execution_session_id?`, `correlation_id?`, `provider_code`, `event_type`, `source_event_id`, `payload_hash`, `status`, retry fields | ACK-before-processing 边界；未解析入站事件允许暂时无 session/correlation |
| `RuntimeTimeline` | `execution_session_id`, `trace_id`, `correlation_id?`, `event_type`, `occurred_at` | append-only 执行轨迹；不作为 owner 状态源 |
| `RuntimeHold` | `execution_session_id`, `correlation_id?`, `reason`, `hold_type`, scope fields, `resolved_at` | 暂停新 effect 的运行时闸门；必须有 object/device/resource scope，解除必须有 evidence |
| `RuntimeIntentLog` | `execution_session_id`, `correlation_id`, `provider_code`, `target_domain`, `target_action`, `request_hash`, `idempotency_key`, `dispatch_status`, retry fields | outbox/effect ledger；不是下游状态源 |
| `ExecutionCorrelation` | `correlation_id`, `execution_session_id?`, `trace_id`, `source_event_id`, `business_owner_key` | 跨域唯一 correlation key；`execution_session_id` 仅 runtime 域内强 FK |
| `ExecutionWorkItem` | `execution_session_id`, `correlation_id`, object identity, current step, step status, parent correlation, concurrency scope | 对象级执行令牌；表达某个料盘/物料/料箱/履约子项在会话内的独立推进状态 |

**对象级流水并发契约**：

- `ExecutionSession` 表示一条 WorkLine 或一次作业上下文的运行会话，不代表同一时间只能处理一个料盘、物料或料箱。
- `ExecutionWorkItem` 或等价模型是 runtime capability 的最小推进单位；粗分机单个料盘、分拣机单个物料、滚筒线单个料箱、CTU 批次中的单个料箱都必须有独立 correlation。
- 设备串行只按 `DeviceDispatchPolicy` 和设备 `concurrency_limit` 控制；它不能把业务流串成“物料 N 完成入格后才允许处理 N+1”。
- step 完成以对应 DeviceResult / WMS callback / RuntimeLocationEvent evidence 为准。某设备完成当前 step 并释放后，Runtime 可以为同一设备派发下一个 work item 的命令，即使前一个 work item 仍在下游设备或 WMS 履约中。
- 父子 work item 只用于追溯和批次收敛，不允许子项失败静默污染父项成功；父项关闭必须校验子项终态、投影收敛和冲突状态。
- WorkLine manifest 的 `workflow_steps` 只声明 capability 模板绑定；具体 step 转移、并发窗口、等待条件和异常恢复由 runtime capability 与 Phase SPEC 定义，并由行为契约测试锁定。

**RuntimeInbox 处理契约**：

- Callback API 在鉴权、schema normalize 和幂等检查通过后立即写入 `RuntimeInbox(status=RECEIVED)` 并 ACK；不得同步推进 session、projection 或 device runtime。
- 外部 callback 入站时允许 `session_id=None` / `correlation_id=None`；worker 通过 `source_event_id`、`command_code`、`request_id`、`idempotency_key` 或 normalized evidence 解析 correlation。解析成功写 `RESOLVED`，解析失败写 `FAILED` + diagnostic，不回滚原始 ACK。
- 异步 worker 以 `RECEIVED -> PROCESSING -> PROCESSED` 为唯一成功路径；处理异常写 `FAILED`、`attempt_count + 1`、`last_error_*` 和 `next_retry_at`。
- `FAILED` 超过重试上限或超过业务 deadline 后转 `DEAD_LETTER`，创建 `RuntimeHold` 并进入人工审计队列。
- 人工重放只能从 `DEAD_LETTER` 复制生成新的 inbox 记录，保留原 `payload_hash/source_event_id/idempotency_key` 作为审计链；不得原地改写历史 payload。
- `source_event_id + provider_code + event_type` 必须唯一；同 key 同 hash 直接返回既有 ACK，同 key 不同 hash 返回 409 并写安全审计。
- RuntimeInbox 积压超过阈值时停止创建新 effect，但 callback 仍可在鉴权通过后 ACK + 持久化 evidence，防止外部系统重试风暴。

**RuntimeHold scope 契约**：

- `RuntimeHold` 默认必须限定作用域，至少声明 `scope_type + scope_key`；优先使用 work item / object / device / resource / queue scope，只有明确影响整线安全或共享设备不可用时才使用 session / workline scope。
- `scope_type` 只允许显式枚举：`WORK_ITEM / OBJECT / DEVICE / RESOURCE / QUEUE / SESSION / WORKLINE`。
- 单个料盘、料箱、格位、扫码结果或 WMS 同步失败只能 hold 对应 `ExecutionWorkItem`、对象或资源，不得默认停止整条 WorkLine。
- Hold 解除必须声明 `allowed_next_effect_scope`，Runtime 只能释放该 scope 内的下一步 effect；不允许用一个人工解除动作恢复所有 pending effect。
- `ReconciliationRecord.owner_scope` 与 `RuntimeHold.scope` 必须一致或可解释地包含，避免 reconciliation 决议和运行时禁发范围不一致。

**API**（v1 router，`src/app/runtime/orchestration/v1/`）：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/sessions/` | POST | 创建 ExecutionSession |
| `/sessions/{id}` | GET | ExecutionSession 详情 |
| `/sessions/{id}/inbox` | GET | RuntimeInbox（分页） |
| `/sessions/{id}/timeline` | GET | RuntimeTimeline（分页） |
| `/sessions/{id}/holds` | GET | RuntimeHold（分页） |
| `/sessions/{id}/intents` | GET | RuntimeIntentLog（分页，只读） |
| `/correlations/` | POST | 创建 ExecutionCorrelation |
| `/correlations/{id}` | GET | ExecutionCorrelation 详情 |

**Intent 写入口约束**：生产业务 API 不提供公开 `POST /intents`。`RuntimeIntentLog` 只能由 runtime/orchestration worker、runtime capability 或受控内部任务在通过 admission、幂等和状态门禁后创建；人工调试入口如确需保留，只能放在 internal/admin 路由，默认关闭并写审计。API 层不得绕过 Runtime capability 直接创建 effect。

**EffectPort 接口**：

| 契约项 | 要求 |
| --- | --- |
| 输入 | 已持久化的 `RuntimeIntentLog` |
| 输出 | effect id、dispatch 结果、`correlation_id`、deadline、拒绝原因 |
| 状态 | `ACCEPTED` / `REJECTED` / `BLOCKED_BY_CB` / `FAILED` |
| 不变量 | dispatch 前必须完成 admission、幂等和状态门禁；不得由 API 层直接调用 provider client |

**状态命名边界**：`EffectPort` 返回的是 provider dispatch result，不是 ledger 状态源。`RuntimeIntentLog.dispatch_status`
只允许使用 `PENDING -> DISPATCHING -> DISPATCHED/ACKED/FAILED`；provider 返回 `ACCEPTED` 时映射为
`DISPATCHED` 或等待 callback 后写 `ACKED`，返回 `REJECTED/BLOCKED_BY_CB/FAILED` 时写入对应失败原因和重试字段。

**RuntimeCapabilityContext 接口**：

| 能力 | 用途 |
| --- | --- |
| `readonly_facts` | 只读事实查询 port，可注入 WMS 主数据/单据/库存/对账 drift snapshot 查询或测试 fake provider |
| `effects` | 唯一允许触发 WMS 履约/库存事务和设备命令副作用的出口；RCS/AGV/CTU 直连若触发，也只作为 fulfillment provider 实现隐藏在该出口之后 |
| `clock` | 统一时间来源 |
| `idempotency` | 跨域幂等检查与审计 |

`readonly_facts` 可注入 WMS 主数据/单据/库存查询端口和 `WmsReconciliationQueryPort`，也可在测试中替换为 fake provider。`WmsReconciliationQueryPort` 只能返回 drift snapshot / `source_version` / WMS 权威事实，不创建 `RuntimeIntentLog`，不写 `ReconciliationRecord`，不触发 WMS 写入确认或补偿动作。`effects` 是唯一允许触发 WMS 履约/库存事务和设备命令副作用的出口。`InboundEventPort` 不属于 `RuntimeCapabilityContext`：capability 不允许持有 `WmsEventPort`、`DeviceEventPort`、`RuntimeInbox` consumer、`wms_integration` service locator、HTTP client、供应商 DTO 或 provider exception。

**Effect ledger 约束**：

- `RuntimeIntentLog` 是 outbox/effect ledger，不是下游状态源；下游状态仍归 handling/device/resource/material/wms_integration 各自拥有。
- 每条 effect 必须带 `correlation_id`、`provider_code`、`idempotency_key`、`request_hash`，用于崩溃恢复、幂等复查和乱序回调归因。
- dispatch worker 只能从 `PENDING` 抢占到 `DISPATCHING`，成功写 `DISPATCHED/ACKED`；失败写 `FAILED` 并保留 `attempt_count/last_error_*`。
- 进程崩溃恢复时只重放 `PENDING` 或过期 `DISPATCHING` 且 `request_hash` 一致的记录；不允许重新构造 payload 发起新 effect。

### 9.3 handling 搬运意图域

**职责**：搬运意图、请求生命周期、幂等、超时、重试

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `HandlingOperation` | `workline_id`, `kind`, `coarse_business_status`, `source`, `target`, `correlation_id`, `idempotency_key` | 只表达 WES 业务搬运意图；不持有外部履约 11 态细节 |
| `HandlingMove` | `handling_operation_id`, from/to location, `kind`, `status`, `evidence_json` | 记录业务意图下的局部动作；事实依据必须来自 typed evidence |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/handling/operations/` | POST | 创建 HandlingOperation |
| `/handling/operations/{id}` | GET | HandlingOperation 详情 |
| `/handling/operations/{id}/moves` | GET | HandlingMove 列表 |

滚筒线队列查询不挂在 Handling API 下。队列当前态由 runtime/orchestration 写入，WorkLine/plane 侧提供只读查询和展示入口。

**WmsFulfillmentPort 集成**：

Handling 只表达 WES 业务搬运意图和本地完成语义；外部履约 11 态机的事实源归 `WmsFulfillmentRequest` / fulfillment adapter。Handling 可通过 `correlation_id`、`RuntimeIntentLog` 和 fulfillment evidence 派生粗粒度状态，但不得直接持有或双写 `SENT/ACCEPTED/RUNNING/BLOCKED_BY_CB/TIMEOUT` 等外部履约细态。

状态归属固定如下：

| 状态层 | Owner | 允许写入方 | 说明 |
| --- | --- | --- | --- |
| 业务搬运意图 | `HandlingOperation` | handling service / runtime capability | 表达 WES 需要完成的业务动作及最终本地语义 |
| 外部履约请求 11 态机 | `WmsFulfillmentRequest` | `WmsFulfillmentPort` adapter + callback worker | 表达 WMS/RCS/provider 是否接收、执行、拒绝、超时或被 CB 阻塞 |
| 作业位置事实 | `RuntimeLocationEvent` / active projection writer | runtime worker / reconciliation | 表达对象在 WES 作业期位置，不由 Handling 或 WMS ACL 直接改写 |

`HandlingOperation.coarse_business_status` 只能由本地业务语义和 evidence 汇总推进：`PLANNED -> WAITING_FULFILLMENT -> IN_PROGRESS -> COMPLETED`，或进入 `REJECTED/FAILED/CANCELLED/RECONCILING`。若需要展示外部履约细态，查询层通过 `correlation_id` 联合 `WmsFulfillmentRequest.status` 返回派生视图，不落双份状态。

**满箱/换箱/换架完成语义**：

- `FULL_BOX_EXCHANGE` / `RACK_BIN_EXCHANGE` / 满货架换架不按普通单步 `BIN_MOVE` 处理。
- 这类 operation 必须显式进入 callback + reconciliation 完成语义：WMS/RCS/ECS callback 只能证明外部动作结果，WES 还必须校验 active projection、queue membership 和目标箱/货架 evidence 后才能关闭。
- 若外部 callback 成功但本地投影冲突，operation 进入 `RECONCILING`，不得覆盖 active projection 或直接标记完成。
- 目标态不要求保留旧 `completion_policy` 字段名；可以用枚举、状态机或 policy object 表达，但语义必须可查询、可测试、可审计。

**满箱交换前置分流契约**：

- 满箱交换区是独立 `work_position_code`，不等同于分拣机 `STATION A/B`；WorkLine manifest 必须能声明满箱交换区、交换决策点、分拣机 STATION 和排队区的不同角色。
- 粗分机移出的单层货架必须先进入满箱交换区或交换决策点；满箱交换完成前，该货架不得进入分拣机 `STATION A/B`，北向机械臂也不得对该货架取料。
- 若无满箱交换需求，Runtime 可创建进入分拣机 `STATION A/B` 或排队区的 WMS fulfillment；若有满箱交换需求，必须先创建箱级 `ExecutionWorkItem` 和 `FULL_BOX_EXCHANGE` operation。
- CTU 满箱交换批次必须按 `rack_code + rack_side` 分组；同一批次不得混合处理同一货架两面的源料箱。
- 若另一货架面仍有满箱，`CHANGE_RACK_FACE` 必须作为独立 WMS/AGV fulfillment 建模；换面期间锁定该货架、当前货架面和对应 exchange work items，不影响其它货架或其它 WorkLine。
- 已完成满箱交换并进入箱级入库物理完成/WMS 同步状态的物料，不得再次进入分拣机逐件入库候选集；剩余未满箱料箱的物料才允许进入分拣机 `STATION A/B` 或排队区。

### 9.4 resource 运行投影域

**职责**：作业期运行投影（不复制 WMS 主数据）

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `RackPlacement` | `workline_id`, rack `ExternalReference`, `work_position_code`, `current_operable_side`, `status`, `correlation_id`, `evidence_json` | 表达货架在 WES 工作位的作业期投影；`current_operable_side` 支撑满箱交换按货架面分批和换面校验 |
| `RackBinMount` | `rack_placement_id`, bin `ExternalReference`, `cell_code`, `status`, `correlation_id`, `evidence_json` | 表达料箱与货架格位的作业期挂载关系 |
| `BinPlacement` | `work_position_code`, bin `ExternalReference`, `status`, `correlation_id`, `evidence_json` | 表达料箱在工作线/工作位的作业期投影 |
| `BinMaterialMount` / `BinCellOccupancy` | `pkg_code`, `material_identity_key`, `cell_code`, occupancy metrics, `correlation_id` | 表达 WES 作业期物料与箱格占用，不复制库存主数据 |
| `CellReservation` | reservation key, target cell, object identity, `correlation_id`, status, expires_at, evidence_json | 下发机械臂投放前的作业期格位预约；防止并发双占 |
| `ResourceStateEvent` | `workline_id`, `event_type`, `source_event_id`, payload, `occurred_at` | 资源投影事件记录；按 `source_event_id` 幂等 |
| `RuntimeLocationEvent` | `object_type`, `object_key`, `location_scope`, `location_code`, `business_step`, `source`, `evidence_json`, `correlation_id` | append-only 位置事实；active projection 和查询视图均由它派生 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/resource/rack-placements/` | GET | 列出 rack placement |
| `/resource/rack-bin-mounts/` | GET | 列出 rack-bin mount |
| `/resource/bin-material-mounts/` | GET | 列出 bin-material mount |
| `/resource/state-events/` | GET | 列出 state event |

**FK 策略**：

- WES 自有实体（RackPlacement / RackBinMount / BinPlacement / BinMaterialMount）补 SQL FK
- 外部对象用 typed `ExternalReference`（无 FK）
- 跨域关联用 `ExecutionCorrelation.correlation_id`（无 `execution_session.id` FK）

**work_position_code 归属（M9 回归）**：

- `work_position_code` 是 WES 内部工作位编号，例如 `WP-KITTING-01`，用于运行投影、plane 展示和设备调度。
- WMS `location_code` 是外部库位/区域编码；二者通过 WorkLine manifest 或 WorkLine 配置中的映射关系关联，不允许混用。
- 映射关系来源于 `WmsMasterDataPort.list_locations()` / `list_zones()` 的只读结果，经配置发布流程写入 WorkLine manifest；运行时只引用 pin 住的 `manifest_version`。
- WES 内部查询默认暴露 `work_position_code`；需要回传 WMS 时，通过映射表转换为 WMS `location_code` 并写入 evidence。

**位置事实契约**：

- `RuntimeLocationEvent` 是 append-only 事实表，表达作业期对象在 WES 视角下的 `what/where/when/why/source`。
- `RackPlacement`、`BinPlacement`、`ConveyorQueueMembership`、`MaterialUnit.location_summary` 和 `PlaneSnapshot` 均由 `RuntimeLocationEvent` 或同等 evidence 投影得到。
- WMS 仍是货架、料箱、物料、库存和单据主数据权威；WES 只拥有作业期位置事实与 active projection，不创建 WMS 主数据副本。
- 查询“某个料箱/料盘/物料在哪里”必须返回 `location_scope/location_code/source/evidence_at/correlation_id`，不能只返回裸位置字符串。
- 位置事实冲突时不覆盖旧值，必须写冲突 evidence 并进入 `RECONCILING` 或 `RuntimeHold`。

**格位分配与预约契约**：

粗分机出料入单层货架格位、分拣机南向机械臂投料入五层货架料箱格位，均属于 WES 作业期内的局部分配决策。WMS 仍是主数据、库存和业务单据权威，但 WES 必须基于 pinned manifest、WMS 查询 evidence、active projection 和当前 work item 选择目标格位：

- `CellAllocationPolicy` 只在 WES 作业期 projection 内选择候选格位，不做全局库存规划、波次规划或货架选择。
- 分配前必须读取 `RackPlacement`、`RackBinMount`、`BinPlacement`、`BinCellOccupancy`、相关 `ConveyorQueueMembership` 和必要 WMS 只读 facts；缺少关键事实时进入 `RuntimeHold`，不得猜测投放。
- 下发机械臂投放命令前必须创建 `CellReservation` 或等价预约记录，唯一约束覆盖 `workline_id + target_cell + active_status` 与 `object_identity + correlation_id`。
- 同一预约重复请求必须按 `idempotency_key + request_hash` 返回既有预约；不同对象竞争同一格位必须失败并重新分配，无法重新分配时进入 `RECONCILING`。
- 机械臂成功回调后，预约转为 `BinMaterialMount / BinCellOccupancy` 事实；机械臂失败、超时或人工取消时，预约必须释放或进入 `RECONCILING`，不能长期占用。
- 粗分机出料货架位没有单层货架、没有可用料箱或没有可用格位时，Runtime 只能请求 `SUPPLY_EMPTY_SINGLE_LAYER_RACK` 等 WMS fulfillment，并等待 callback/evidence 后重新计算。

### 9.5 material 物料根实体域

**职责**：WES 作业期料盘/物料处理单元身份；当前位置只保留投影摘要，不拥有位置事实源

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `MaterialUnit` | `pkg_code`, `material_identity_key`, material/vendor/lot/date 派生身份字段, `status`, `location_summary`, `current_session_correlation_id` | WES 作业期唯一自有根实体；`location_summary` 只读投影摘要，不能被 material service 直接写位置事实 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/material/material-units/` | GET | 列表 |
| `/material/material-units/{pkg_code}` | GET | 详情 |
| `/material/material-units/location-query` | GET | `MaterialLocationQuery`（按 pkg_code / material_identity / workline / status 查） |

**Authority**：WES material 域是**唯一自有根实体域**（作业期料盘/物料处理单元身份）——其他域不能直接修改身份字段。物料主数据、批次、库存数量、货主和单据仍归 WMS；`material_identity_key` 只用于 WES 作业期归因、查询和投影。`location_summary` 不是事实源，只能由 RuntimeLocationEvent projection writer 更新，material service 不得直接写位置。

### 9.6 device 设备接入域

**职责**：ECS/设备上位机 API 接入、设备角色、EVENT/COMMAND/RESULT、设备诊断。

**边界**：

- WES 只按 `third_party_integration_whitepaper.md` 调用 ECS/设备上位机 HTTP API。
- WES 下发的是 `task_type + params` 业务命令，只包含逻辑位置和业务参数。
- WES 不与 PLC 通讯，不下发 PLC 点位、物理坐标、关节角度、速度曲线、安全回路或急停复位指令。
- 硬件防呆由 ECS 自主完成；WES 只根据 ECS 暴露的设备状态、Ack、Result、Event 做业务编排。
- `ESTOP_PRESSED`、安全门、光栅等安全事件只能由 ECS 转换为 WES event/evidence/RuntimeHold；恢复必须来自 ECS 状态回传或人工 reconcile。

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `DeviceRuntime` | `device_code`, `role`, `diagnostic_state`, heartbeat/result/event timestamps, status snapshot TTL, current command | 设备运行诊断状态来自 ECS/device；本分支以 `wes_runtime.device_runtime_projections` 持久化运行态投影，不包含 PLC 级控制状态 |
| `DeviceDispatchPolicy` | `device_role`, `capability_code`, priority/concurrency/deadline/order policy, status snapshot TTL | 定义设备选择、限流、有界等待和取消策略 |
| `DeviceEvent` | `device_code`, `event_type`, `event_id`, `sequence_no`, payload, `source_event_id` | 缺 `event_id` 或乱序事件只落 evidence + diagnostic，不推进业务 |
| `DeviceCommand` | `device_code`, `command_code`, `task_type`, payload, `correlation_id`, deadline/ack deadline, `idempotency_key`, lease, ack status | WES 下发给 ECS 的业务命令；不包含 PLC/坐标/关节/安全回路字段 |
| `DeviceResult` | `command_code`, result status, payload, `evidence_json`, `occurred_at` | 动作完成事实只能由 ECS callback/result 推进 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/device/devices/` | GET | 设备列表 |
| `/device/devices/{code}` | GET | 设备详情 |
| `/device/commands/` | POST | 发送命令 |
| `/device/commands/{id}/result` | GET | 查询结果 |
| `/device/events/` | GET | 事件流 |

**Command-Ack-Callback 约束**：

- DeviceCommand dispatch 前必须先调用 ECS `GET /api/v1/device/status` 或读取 `now <= DeviceRuntime.status_valid_until` 的快照，确认目标设备 `status=IDLE`；本分支由 `DeviceRuntimeProjectionWriterService` 持久同步 DeviceService 运行态入口的 `status_valid_until`。
- `status_snapshot_ttl_ms` 由 manifest 或 `DeviceDispatchPolicy` 定义；默认 1000ms。快照过期必须重新查询 ECS，查询失败按状态查询超时处理。
- 若设备状态为 `RUNNING`，Runtime 不下发命令，进入有界等待：按 `wait_poll_interval_ms` 轮询或订阅 ECS 状态变化，直到设备变为 `IDLE` 或到达 `dispatch_deadline_at`。
- 若等待到 `dispatch_deadline_at` 仍未变为 `IDLE`，写 `DeviceCommand.ack_status=TIMEOUT`，创建 RuntimeHold；不进入无限排队。
- 若设备状态为 `ERROR` / `OFFLINE` / `UNKNOWN` / `MAINTENANCE`，或状态查询超时，Runtime 不下发命令，按指数退避重试（默认 1s / 2s / 4s，最多 3 次）。
- 故障/查询超时退避耗尽后写 `DeviceCommand.ack_status=TIMEOUT` 或 `REJECTED`，创建 RuntimeHold。
- DeviceCommand 下发后只以 ECS HTTP `200 Accepted` 表示“收到并接受”，不代表动作完成。
- 动作完成必须由 `/api/v1/callback/result` 回传 `command_code` 后推进。
- ACK 前等待必须有 `ack_deadline_at` 或等价 lease；设备未 ACK 的命令不得无限停留在等待态。
- ACK deadline 到期后 Runtime 必须扫描并写入 diagnostic/RuntimeHold；是否取消或人工恢复由 `DeviceDispatchPolicy` 和 ReconciliationManager 决定。
- `/api/v1/callback/event` 只 ACK，不允许在响应体中返回下一步动作；Runtime 后续通过 DeviceCommand 下发动作。
- 同一 `command_code` 重试不得触发重复物理动作；WES 侧保留 `request_hash` 和 `idempotency_key`，ECS 侧按白皮书缓存最近 1 小时 command_code。
- 缺 `event_id` 或乱序 `sequence_no` 的事件只落 evidence + diagnostic，不直接推进 session 或 projection。

**DeviceState 扩展语义（M6 回归）**：

- `UNKNOWN`：WES 未拿到有效 ECS 状态，或状态快照过期且查询失败；禁止下发命令，只能重查 ECS 或等待状态事件。
- `MAINTENANCE`：设备本地或 ECS 标记维护中；Runtime 选设备时跳过该设备，直到收到 `MAINTENANCE_LEFT` / `DEVICE_ONLINE` 且状态重新变为 `IDLE`。
- `OFFLINE` 表示 ECS 明确回传离线；`UNKNOWN` 表示 WES 无法确认。二者都不能派发，但告警和排障路径不同。

**DeviceDispatchPolicy 调度契约**：

- Runtime 先按 `device_role + capability_code + manifest_version` 选择候选设备，再按 `priority + deadline + order_policy` 生成候选命令队列。
- 同一 `device_code` 默认 in-flight = 1；只有 manifest 显式声明并通过 ECS 能力校验后才允许提高 `concurrency_limit`。
- 多设备具备同一能力时，优先选择 `IDLE` 且状态快照未过期的设备；若全部 `RUNNING`，只等待到最早 `dispatch_deadline_at`，不得无限排队。
- session 进入 `HOLD` / `RECONCILING` / `CLOSED` 时，未下发命令必须取消或冻结；已下发命令只能等待 ECS callback 或人工 reconcile。
- Runtime 不做 PLC 级抢占、急停复位或运动控制；这些只能由 ECS/现场安全系统处理后以事件形式回传 WES。

**扫码平台互锁与取料因果约束**：

分拣机北向机械臂把物料放到扫码平台后，Runtime 不允许立即下发下一条取料命令。只有上一物料的南向
`PICK` 已形成 `southbound_pick_acknowledged`，且相关 work item 未处于 `HOLD` / `RECONCILING`，
才允许北向机械臂取下一件；扫码平台占用状态仅作诊断证据，不得替代南向 `PICK ACK` 因果或开启预取旁路。

**WorkLine 启停门禁**：

- manifest 中标记为 `required=true` 的设备若处于 `OFFLINE` / `UNKNOWN` / `MAINTENANCE`，WorkLine 不允许从 `INACTIVE` 切到 `ACTIVE`。
- manifest 中标记为 `optional=true` 的设备不可用时，WorkLine 可启动，但对应 capability 从候选设备集中剔除，并在 `PlaneSnapshot.warnings[]` 中展示。
- RUNNING session 期间 required 设备变为 `OFFLINE` / `MAINTENANCE` 时，Runtime 进入 `RuntimeHold`，停止新 effect，等待 ECS 恢复或人工 reconcile。

**业务资源 admission**：

设备在线只证明物理能力可用，不证明业务流可推进。Runtime 创建入库类 `ExecutionSession` 前必须按 capability 的 admission rule 校验作业期资源：

- 粗分机启动/入口 admission：只校验入口检测、扫码、测量设备可用，粗分机流水线入口队列无硬阻塞，入口到出料缓冲路径容量未超过 manifest 上限；不要求出料货架位或可用料格在 session 创建时已存在。
- 粗分机出料 step admission：物料到达出料分配 step 时才校验出料货架位、可用料箱和可用格位；若无可用格位，只 hold 当前出料 `ExecutionWorkItem` 并请求 WMS 补充空箱货架或换架。入口侧是否继续放行由流水线缓冲容量自然反压，不因单个出料 work item 缺格位直接阻断整条入口流程。
- 满箱交换前置分流 admission：粗分机移出的单层货架进入分拣机前，必须先校验是否存在满箱交换需求；有需求时目标只能是满箱交换区或交换决策点，不得直接进入分拣机 `STATION A/B`。
- 满箱交换区 admission：按 `rack_code + rack_side` 校验当前可操作货架面、CTU 可用性、满箱可取集合、五层货架空箱和可用空储位；跨货架面满箱必须等待 `CHANGE_RACK_FACE` fulfillment 完成后再释放下一面 exchange work items。
- 分拣机：STATION A/B 至少一个单层货架位可进入取料流程；FIVE STATION 有可解释五层货架投影，或已创建 WMS 补架 fulfillment；滚筒线入口线、工作位、退料线容量可由 manifest + active projection 计算。
- 分拣机取料 admission：只允许选择已通过满箱交换前置分流且仍需逐件分拣的物料；已满箱交换入库的物料必须从候选集中排除。
- CTU 入线批次创建前必须计算 `min(入口线空位, CTU 背篓容量, 五层货架可用料箱数)`；退线批次创建前必须计算 `min(退料线料箱数, CTU 背篓容量, 五层货架可用空储位)`。
- admission 只能基于 WES active projection、pinned manifest 和 WMS QueryPort evidence；缺事实时写 `RuntimeHold` 或创建 WMS fulfillment，不能用本地默认值假装资源可用。
- admission 失败不得污染 active projection；已创建但未派发的 effect 必须取消或冻结，并保留 timeline/evidence 供调试回放。

**Authority**：device 到位信号、硬件防呆和设备忙闲状态归 ECS/device；WES 只接收并转换为业务事件。WES 不拥有 PLC 通讯、安全回路、坐标映射或运动控制权。

### 9.7 wms_integration ACL 域

**职责**：WMS 反腐层：能力面 ports + ACL；不复制 WMS 主数据

详见 §5.1 能力面 Port 详细。

**核心数据**：

| 数据 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `WmsFulfillmentRequest` | fulfillment kind, source/target, 11 态 `status`, immutable `request_hash`, `idempotency_key`, `correlation_id` | 外部履约状态 owner；WMS/RCS/provider callback 和 adapter 推进 |
| `WmsCallbackEnvelope` | callback type, `source_event_id`, `source_version`, signature/timestamp/nonce, raw body hash, normalized evidence, normalizer status | 外部 callback 原始归档与 normalize 结果；外部不直接写 envelope API |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/wms-integration/fulfillment/requests` | GET | 查询履约请求列表（只读） |
| `/wms-integration/fulfillment/requests/{id}` | GET | 履约请求详情 |
| `/wms-integration/callback-envelopes` | GET | 查询已归档 WMS callback envelope（只读，外部不写入） |
| `/wms-integration/inventory/query` | POST | 查询库存 |
| `/wms-integration/reconciliation/drift-check` | POST | 只读触发 WMS 权威事实拉取并返回 drift snapshot；不得写 WMS 或跨域 owner 状态 |

**入口约束**：外部 callback 写入口只允许 §5.3 的统一 callback API；出站 WMS 履约、库存事务、PKG 绑定和补偿动作只能由 runtime/orchestration 在 admission、幂等和状态门禁通过后写 `RuntimeIntentLog`，再经 EffectPort dispatcher 调用 `WmsFulfillmentPort` / `WmsInventoryTransactionPort`。`wms_integration` 不提供公开创建履约请求的 POST API，不提供第二个外部 POST 写入口，只提供 normalizer、port 和只读查询。若调试期确需人工重放或补发 effect，入口必须放在受控 internal/admin runtime 路由，默认关闭并写审计，不得绕过 `RuntimeIntentLog`。

**目标态优先**：可复用 `src/app/wms_integration/` 已有 ACL 实现，但允许破坏性整理目录、模型和 import。

### 9.8 reconciliation 对账域

**职责**：统一冲突登记、隔离动作、决议输出和审计；不直接写跨域 owner 状态

详见 §6.4 RECONCILING 冲突决议模型。

**核心数据**：

| 数据 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `ReconciliationRecord` | `conflict_type`, detector/reason, `evidence_json`, `resolution_decision`, `owner_scope`, `allowed_next_effect_scope`, `recovery_path`, `resolved_at`, `correlation_id` | 决议记录和审计字段；不作为跨域 owner 状态写入指令 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/reconciliation/conflicts` | GET | 当前冲突列表 |
| `/reconciliation/conflicts/{id}` | GET | 冲突详情 |
| `/reconciliation/reconcile` | POST | 人工 reconcile |
| `/reconciliation/resolve` | POST | 写入 resolution_decision 并标记解决 |

**API 写入约束**：

- `/reconciliation/reconcile` 与 `/reconciliation/resolve` 只能写 `ReconciliationRecord.resolution_decision`、`owner_scope`、`allowed_next_effect_scope`、`resolved_at` 和 audit log。
- API 不得直接修改 `WmsFulfillmentRequest`、`HandlingOperation`、`ExecutionSession`、`DeviceCommand` 或 active projection；这些 owner 只能根据 reconciliation evidence 自行转移。
- 人工 resolve 必须记录操作者、依据、object scope、允许释放的 effect 范围和幂等键。

**SMT / NG / WMS 对账语义**：

- SMT 入库 P0 可以先保证 WES 本地可信最终去向，但完整目标态必须把目标箱回写失败、WMS 确认/拒绝、NG evidence 消费、WMS confirmation version 和 session 结算材料纳入统一对账。
- NG 周转箱、NG 库位、返工工单主档不归 WES 维护；WES 只保存 typed `ExternalReference`、RuntimeHold、物理交接 evidence、解除条件和回调归因。
- WMS 版本冲突或目标箱回写失败时，不允许本地 projection 冒充 WMS 事实成功；必须写 `ReconciliationRecord`，等待 WMS 重试、人工 reconcile 或 provider callback。
- 返入口真实 EVENT 若需要归因，只关联原 `ExecutionCorrelation` / material identity / external reference，不恢复旧 plugin session 语义。

---
