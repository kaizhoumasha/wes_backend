# 新 Manifest 下 SMT 分拣入库后端闭环优化 SPEC

> 日期：2026-06-16
> 状态：SPEC 已确认，可进入实施阶段（按 T0-T8 阶段门禁推进）
> 类型：后端流程破坏性优化（当前系统未发布，不保留向后兼容）
> 范围：SMT 分拣入库 handoff、runtime、plugin、Celery 兜底扫描和测试合同
> 非范围：前端处置页、供应商硬件协议、联调手册、数据库枚举新增、同一 target WorkLine 多 item 并发处理、单独 NG 工位配置
> Eng Review：2026-06-16 已完成四轮，所有工程评审建议均选择完整方案并折叠入本文；第四轮为实施前评审。

## 0. 评审后摘要

本 SPEC 的工程结论如下：

- `SMT_SORTING_INBOUND` 自动闭环由 handoff service 统一推进；粗分机 release fact、terminal success 和 Celery 兜底扫描不各自实现平行推进逻辑。
- `PICKED` / `SORTED` / `SKIPPED` 的 handoff ledger 落账由 runtime effect 调用 handoff service 完成；plugin 只产生命令、context 和 resource intents。
- claim session context 必须通过 `SortingInboundContext` typed helper 生成，且必须包含 `sorting.context_schema_version=1`。
- target WorkLine 是自动串行锁粒度；同一 target WorkLine 同时只允许一个 source item in-flight。
- source pick admission 默认使用真实 ECS realtime probe；测试 stub 必须显式注入。
- claim 必须采用两阶段短锁模型：外部 ECS probe 不持有 DB 行锁，最终短事务内重锁 source item + target WorkLine 并 recheck。
- source pick success、target success、NG success 都必须是幂等状态推进；重复 SUCCESS 不得重复 claim 下一条 READY item。
- 每条 source item 的独立 sorting session 必须在 terminal success 后进入完成态。
- 本 SPEC 必须与 v0.6.2.0 manifest 货架位编制合同一致：`rack_positions` 只表达 WES 管理的货架停靠位/库存事实锚点，NG 区、工作站和目标料箱/格位不得冒充 manifest rack position。
- 本期只闭合 SMT 分拣入库后端链路，不扩展 CTU/WMS/NG 对账、运营看板或硬件联调合同。
- 实施必须按阶段门禁推进：先锁 manifest 静态合同，再收敛 typed context/route/claim，再接 runtime ledger，最后接 Celery 和 E2E；不得在一个大补丁里同时改完整链路。

## 1. 背景

`SMT_SORTING_INBOUND` 新 manifest 已经声明分拣入库静态契约，包括：

- 设备角色：源端机械臂、目标机械臂、扫码平台、工作站。
- 命令：`SORTING_SOURCE_PICK`、`SORTING_TARGET_PLACE`、`SORTING_NG_PLACE`。
- 事件：`WORKING_BIN_SCAN`、`SORTING_SESSION_COMPLETE_REQUESTED` 以及命令结果事件。
- 资源边界：源端单层货架、目标五层货架等货架停靠/库存锚点语义。

当前后端流程尚未完全收敛到 manifest 驱动的自动闭环：

- 粗分机 release fact 当前只创建 handoff demand/source items，没有生产链路自动调用 `claim_next_source_item`。
- `claim_next_source_item` 创建的 sorting session 缺少 `sorting.context_schema_version=1`，导致后续 `SORTING_SOURCE_PICK SUCCESS` 可能被插件判定为 `SORTING_CONTEXT_INVALID`。
- `SORTING_TARGET_PLACE SUCCESS` / `SORTING_NG_PLACE SUCCESS` 成功后，runtime 和 handoff source item 的终态没有可靠闭环。
- demand `COMPLETED` 依赖 source item 聚合状态；若 source item 不进入终态，demand 完成态不可可信。
- 当前实现中曾把 NG 区表达为独立 `SORTING_NG_STATION` / `NG_STATION`；目标合同应与粗分机一致，NG 只是机械臂搬运动作的目标位置，不作为独立设备、货位或资源边界配置。

本 SPEC 的目标是定义后端合同，并作为实施阶段 T0-T8 的准入依据。

## 2. 目标

在不保留旧兼容的前提下，将 SMT 分拣入库流程收敛为后端自动闭环：

```text
粗分机 release fact
  -> handoff demand/source items
  -> READY source item 单个串行 claim
  -> 合法 SMT_SORTING_INBOUND session
  -> SORTING_SOURCE_PICK
  -> WORKING_BIN_SCAN
  -> SORTING_TARGET_PLACE 或 SORTING_NG_PLACE
  -> source item SORTED 或 SKIPPED
  -> demand COMPLETED
  -> 即时尝试 claim 下一条 READY item
```

目标行为：

- 粗分机 release fact 创建或更新 handoff demand 后，后端立即尝试认领一个 READY source item。
- 每次只认领一个 source item，避免同一 WorkLine 自动流程并发处理多盘。
- 每条 source item 使用独立 `SMT_SORTING_INBOUND` session。
- claim 创建的 session 必须符合插件自动流程 context 合同。
- target/ng place 成功后必须回写 handoff source item 终态并重算 demand。
- Celery 扫描作为兜底认领和恢复机制，而不是唯一推进入口。

## 3. 非目标

本 SPEC 不包含以下事项：

- 不新增或修改前端处置页。
- 不调整供应商硬件协议字段。
- 不更新硬件联调手册。
- 不新增 `LOCAL_NG` 等 source item 枚举状态。
- 不做旧 session context 兼容迁移。
- 不引入同一 target WorkLine 多 item 并发处理。
- 不改变粗分机自身硬件流程。
- 不把正常流程推进暴露为人工/API 必点动作。
- 不配置单独的 `SORTING_NG_STATION` 设备角色。
- 不配置单独的 `NG_STATION` rack position 或 `SORTING_INBOUND_NG` resource boundary。
- 不把 `WORKSTATION`、扫码平台、NG 区、料箱码或料格码建模为 manifest `RackPosition`。
- 不在本期生产化完整 CTU/WMS/NG 对账链路；继续由既有 P1 TODO 跟踪。
- 不在本期实现运营看板、告警阈值或现场 Runbook；首版运行数据后再定。
- 不在本期做 claim 吞吐调优面板；仅保留保守 `claim_limit` 与后续指标调优 TODO。

## 4. 术语和状态约定

### 4.1 READY Source Item

可自动认领的 READY source item 必须同时满足：

- `SmtInboundHandoffSourceItem.status = READY`。
- `next_attempt_at IS NULL OR next_attempt_at <= now`。
- 所属 demand 无 `failure_code`。
- 所属 demand 可推进到 `READY_FOR_SORTING`。
- route service 返回 `SELECTED`。
- 目标 WorkLine 为 `READY`。
- source rack position 必须解析到当前 `SMT_SORTING_INBOUND` manifest 的 `SORTING_INBOUND_SOURCE` boundary，且 source station 可用。
- 目标 sorting session 没有 open `current_material`。
- ECS realtime probe 判断设备可用于 source pick admission。

### 4.2 自动串行粒度

自动串行粒度按 target WorkLine 控制：

- 同一 target WorkLine 同一时刻最多允许一个自动 source item in-flight。
- in-flight source item 状态包括：
  - `PICK_REQUESTED`
  - `CLAIMED_BY_SORTING`
  - `PICKED`
  - `SORTING`
- 如果目标 WorkLine 已有 in-flight item，新 READY item 不创建 session，保持 `READY` 并写入 retry evidence。

### 4.3 Source Item 终态

终态集合：

- `SORTED`：目标料格放盘成功，物料进入目标五层货架。
- `SKIPPED`：本地分拣 NG 已由机械臂搬运到 NG 位置，未进入目标五层货架，但自动流程已闭环。
- `EXCHANGED`：满箱交换后无需逐盘分拣。
- 既有 `MANUAL_HOLD` 不视为业务完成终态；它会把 demand 聚合为 `MANUAL_HOLD`。

`SORTING_NG_PLACE SUCCESS` 使用现有 `SKIPPED` 状态，不新增 `LOCAL_NG`。

### 4.4 NG 区合同

SMT 分拣入库的 NG 处理必须与粗分机保持一致：

- NG 区不是独立设备角色，不声明 `SORTING_NG_STATION`。
- NG 区不是 WES 管理的 rack position，不声明 `NG_STATION`。
- NG 区不参与 station lease、rack snapshot、resource boundary 或目标料格分配。
- `SORTING_NG_PLACE` 命令仍由 `SORTING_TARGET_ARM` 执行。
- 插件只需在 `SORTING_NG_PLACE` command payload 中携带 NG 搬运目标，例如 `ng_location` / `target_location`。
- `SORTING_NG_PLACE SUCCESS` 表示机械臂已完成搬运到 NG 位置，后端据此将 source item 写为 `SKIPPED`。

### 4.5 组件职责边界

| 组件 | 职责 | 禁止事项 |
| --- | --- | --- |
| Handoff service | demand/source item 状态推进、claim、ledger 落账、demand 聚合 | 不直接执行硬件命令 |
| Runtime effect | 根据插件 effects 落地 command/outbox、resource fact，并调用 handoff service 写 ledger | 不绕过 handoff service 直接改 source item 状态 |
| SMT sorting plugin | 解析事件、产生命令 intents、context patch 和 resource intents | 不直接写 handoff ledger，不声明独立 NG station |
| Celery scan | 到期重算、READY 兜底 claim、stuck claim 恢复 | 不成为正常流程唯一推进入口 |

### 4.6 v0.6.2.0 Manifest 货架位编制合同

本 SPEC 必须与 v0.6.2.0 manifest rack-position rename 后的合同一致：

- `RackPosition` 只代表 WES 管理的货架停靠位或库存事实锚点，不代表泛化物理点位。
- `SMT_SORTING_INBOUND.rack_positions` 仅保留真实货架停靠/库存锚点：
  - 源端单层货架位：`role=SOURCE`，`rack_kind=SINGLE_LAYER`。
  - 目标五层货架位：`role=TARGET`，`rack_kind=FIVE_LAYER`。
- `WORKSTATION`、扫码平台、机械臂中转点、NG 区、料箱码和料格码都不得进入 `rack_positions`。
- `resource_boundaries` 必须只引用 manifest 中声明的 `rack_positions`，不得从 topology edge 推导资源边界。
- 源端 `resource_boundaries` 必须使用 `business_demand_type=SORTING_INBOUND_SOURCE` 和 `rack_kind=SINGLE_LAYER`。
- 目标端 `resource_boundaries` 必须使用 `business_demand_type=SORTING_INBOUND_TARGET` 和 `rack_kind=FIVE_LAYER`。
- 不声明 `SORTING_INBOUND_NG` resource boundary。
- 不声明 `SORTING_INBOUND_WORK` resource boundary，工作站状态只存在于 `sorting.stations`、`current_material` 和命令 payload evidence。
- 拓扑中的 `MATERIAL_FLOW` 只连接真实 `RACK_POSITION` 节点，不连接 `WORKSTATION` 或 `NG_STATION`。

命令 rack position 参数约定：

- `SORTING_SOURCE_PICK`
  - source rack position 来自 manifest-declared source boundary。
  - target 不再静态引用 `WORKSTATION` rack position；工作台/扫码平台占用由 `sorting.stations.scan_platform` 表达。
- `SORTING_TARGET_PLACE`
  - target rack position 固定引用 manifest-declared target boundary，例如 `TARGET_STATION`。
  - `target_bin_code` / `target_cell_code` 只作为 command payload 和 `MATERIAL_MOUNTED` fact 的库存定位字段，不得作为 `RackPositionArgSource` 的 rack position 值。
- `SORTING_NG_PLACE`
  - 仍绑定 `SORTING_TARGET_ARM`。
  - 不声明 target `RackPositionArg`。
  - NG 目标仅通过 command payload 的 `ng_location` / `target_location` 表达，并作为 terminal evidence 留存。

## 5. 功能需求

### 5.1 Release Fact 后即时尝试 Claim

粗分机 release fact 进入 handoff producer 后：

- 幂等创建或返回 `SmtInboundHandoffDemand`。
- 生成 source items 后，按 usage policy/evaluate 进入 `READY_FOR_SORTING`。
- 当 demand 存在 READY source item 时，立即尝试 claim 一个 source item。
- 如果 route busy 或资源暂不可用，source item 保持 `READY`，写 `next_attempt_at` 和失败 evidence，由 Celery 后续兜底。
- release/evaluate/claim 收敛到 handoff service 单一推进入口；粗分机 release fact 路径和 Celery 兜底扫描均复用该入口。
- target/ng terminal success 后优先尝试 claim 同一 demand 的下一条 READY item；Celery 兜底保留全局 READY 队列扫描。

### 5.2 Claim 创建合法 Sorting Session

`claim_next_source_item` 创建的 `WorklineSession.context_json` 必须包含：

```json
{
  "sorting": {
    "context_schema_version": 1,
    "source_pick_request": {
      "handoff_demand_id": 0,
      "handoff_source_item_id": 0,
      "claim_attempt_no": 1,
      "event_id": "smt-inbound-handoff-source-item:<item_id>:claim:<attempt>",
      "target_workline_code": "WL-SMT-SORT-01",
      "manifest_contract_version": "2026-06-01.p0",
      "source_rack_position_code": "SOURCE_STATION_A",
      "target_rack_position_code": "TARGET_STATION",
      "route_evidence": {}
    },
    "stations": {
      "scan_platform": "EMPTY"
    }
  }
}
```

要求：

- `context_schema_version=1` 是必填字段。
- `source_pick_request` 是后续 source item 终态回写的唯一稳定定位来源。
- `source_pick_request` 必须包含 manifest contract version、source rack position code 和 target rack position code，作为后续 command/evidence 的静态位置来源。
- 旧缺字段 context 不做兼容修复。
- session `context_schema_version` 字段可继续使用现有外层版本，例如 `smt-sorting-inbound.v1`；插件自动流程以 `sorting.context_schema_version` 为准。
- 创建 context 必须走 `SortingInboundContext` typed helper，禁止在 claim 路径手写松散 JSON。
- claim route 的 source position 必须从 manifest `resource_boundaries` 解析：筛选 `business_demand_type=SORTING_INBOUND_SOURCE` 且 `rack_kind=SINGLE_LAYER` 的 source boundary。
- route config 只能选择 manifest 已声明的 source boundary；不能用 `source_station_code` / `source_position_code` 创造新位置。
- 若 manifest 只有一个 source boundary，可默认选择；若有多个 source boundary 且 config 未显式选择，进入受控配置失败，不自动猜测。
- route evidence 必须记录 `manifest_contract_version`、`source_rack_position_code`、`source_station_code`、`target_rack_position_code` 和 boundary evidence。
- claim 前必须以 target WorkLine 为粒度做串行保护：target WorkLine 行锁内重查 open sorting session 和 handoff ledger in-flight item。
- 生产默认路径必须使用真实 ECS realtime probe；测试中的 allow-idle stub 只能显式注入，不能作为默认 source pick admission。
- ECS probe 必须有短 timeout；target WorkLine 行锁内不得等待外部 HTTP，只允许做 DB recheck 和 session/inbox 写入。
- claim 必须分两阶段执行：
  1. 不持有 source item / target WorkLine 行锁时执行 route 计算和 ECS realtime probe。
  2. 进入短事务后重锁 source item 和 target WorkLine，重查 READY、open session、handoff ledger in-flight、ECS probe 结果仍在短 TTL 内有效，再创建 session/inbox。
- 如果第二阶段 recheck 失败，source item 保持或恢复为 `READY`，写入 retry evidence，不创建 session/inbox。

### 5.3 Source Pick Command Correlation

`SORTING_SOURCE_PICK_REQUESTED` 内部事件继续由 `SMT_SORTING_INBOUND` plugin 转成 `SORTING_SOURCE_PICK` command。

runtime effect 创建 command/outbox 后，必须继续记录 source-pick correlation：

- `source_pick_command_id`
- `source_pick_command_code`
- `source_pick_dispatch_key`

`SORTING_SOURCE_PICK SUCCESS` effects 成功落地后，source item 应推进为 `PICKED`，并重算 demand。

`PICKED` 落账必须通过 handoff service 单一方法完成；runtime effect 和 recovery 路径共用该方法，避免正常路径与恢复路径状态语义分叉。

`PICKED` 状态推进必须幂等：

- `PICK_REQUESTED` / `CLAIMED_BY_SORTING` 收到 source pick success 时推进为 `PICKED`。
- 已是 `PICKED` 的重复 success 为 no-op，不重复重算后续 claim。
- 已进入 `SORTED` / `SKIPPED` / `EXCHANGED` 的迟到 source pick success 不改变终态。
- 已进入 `MANUAL_HOLD` 或失败恢复中的迟到 source pick success 进入受控人工复核，不静默改回 `PICKED`。

### 5.4 Target Place Success 回写

`SORTING_TARGET_PLACE SUCCESS` 成功后：

- 保留现有 `MATERIAL_MOUNTED` resource fact。
- `SORTING_TARGET_PLACE` 的 target rack position 必须固定引用 manifest-declared target boundary；`target_bin_code` / `target_cell_code` 只作为 command payload 与 resource fact 的库存定位字段。
- 保留现有 sorting context 清理：清除 `pending_target_placement`、关闭 `current_material`、释放扫码平台。
- 使用 session context 中的 `sorting.source_pick_request.handoff_source_item_id` 定位 source item。
- 将 source item 标记为 `SORTED`。
- 写入 `completed_at`。
- 清理 item `failure_code` / `failure_message` / `next_attempt_at`。
- 重算 demand 状态。
- 若同 demand 仍有 READY source item，立即尝试 claim 下一条。

如果 `MATERIAL_MOUNTED` 进入资源投影对账/阻断状态，不得提前写 `SORTED`。

`SORTED` 落账归 runtime effect 调用 handoff service 负责；plugin 只产生命令 effects 和 context/resource intents，不直接写 handoff ledger。

`SORTED` 落账必须幂等：同一 source item 已是 `SORTED` 时返回 already-terminal，不得再次触发下一条 READY claim；若已是 `SKIPPED` / `EXCHANGED` / `MANUAL_HOLD` 等冲突状态，进入受控 BLOCK/MANUAL_HOLD。

成功写入 `SORTED` 后，当前 sorting session 必须进入完成态，并记录 terminal evidence。

### 5.5 NG Place Success 回写

`SORTING_NG_PLACE SUCCESS` 成功后：

- 保留现有 sorting context 清理和 NG evidence。
- `SORTING_NG_PLACE` 不声明 target rack position；NG 目标只来自 command payload evidence。
- 使用 session context 中的 `sorting.source_pick_request.handoff_source_item_id` 定位 source item。
- 将 source item 标记为 `SKIPPED`。
- 写入 `completed_at`。
- 清理 item `failure_code` / `failure_message` / `next_attempt_at`。
- 重算 demand 状态。
- 若同 demand 仍有 READY source item，立即尝试 claim 下一条。

`SKIPPED` 落账归 runtime effect 调用 handoff service 负责；plugin 不直接写 handoff ledger。

`SKIPPED` 落账必须幂等：同一 source item 已是 `SKIPPED` 时返回 already-terminal，不得再次触发下一条 READY claim；若已是 `SORTED` / `EXCHANGED` / `MANUAL_HOLD` 等冲突状态，进入受控 BLOCK/MANUAL_HOLD。

成功写入 `SKIPPED` 后，当前 sorting session 必须进入完成态，并记录 terminal evidence。

### 5.6 Demand 聚合状态

demand 状态必须由单一聚合入口重算：

- 任一 source item 为 `MANUAL_HOLD`：demand 为 `MANUAL_HOLD`。
- 全部 source items 均为 `SORTED` / `SKIPPED` / `EXCHANGED`：demand 为 `COMPLETED`。
- 任一 source item 为 `PICKED` / `SORTING`：demand 为 `SORTING_IN_PROGRESS`。
- 任一 source item 为 `PICK_REQUESTED` / `CLAIMED_BY_SORTING`：demand 为 `CLAIMED_BY_SORTING`。
- 任一 source item 为 `READY`：demand 为 `READY_FOR_SORTING`。
- `CANCELLED`、`RECONCILING`、`WAITING_FULL_BOX_EXCHANGE` 保留现有短路规则。

### 5.7 Celery 兜底扫描

`scan_smt_inbound_handoff_demands_batch` 应覆盖三类工作：

1. 到期 demand 状态重算。
2. READY source item 兜底认领。
3. claim 后卡住 source item 恢复。

summary 增加 `claimed` 计数：

```json
{
  "scanned": 0,
  "claimed": 0,
  "advanced": 0,
  "retry_scheduled": 0,
  "manual_hold": 0,
  "recovery_errors": 0
}
```

既有 `stale_after_seconds` 默认继续使用 `300` 秒。

`limit` 拆为扫描/恢复上限和 READY claim 上限：Celery Beat 默认保持保守 `claim_limit`，避免一次扫描放大 ECS probe、DB 锁和 runtime inbox 压力。

同一次 scan 内的 READY claim 应按 target WorkLine / ECS endpoint 对 ECS realtime probe 做短生命周期去重或批量查询；该缓存只在本次 scan 有效，不引入跨任务全局缓存。

## 6. 失败模式

以下场景不得写入 `SORTED` 或 `SKIPPED`：

- `SORTING_SOURCE_PICK FAILED`。
- `SORTING_TARGET_PLACE FAILED`。
- target place 失败且物理位置未知。
- `MATERIAL_MOUNTED` 资源投影进入 reconciling。
- target station busy。
- target no capacity，业务阶段应停留在 `WAITING_TARGET_BIN_SWITCH`。
- route service 返回 retry/manual hold。
- session context 缺少 `sorting.source_pick_request` 或 source item ID 不匹配。

资源暂不可用时：

- source item 保持 `READY`。
- 写 `next_attempt_at`。
- demand 不进入 `MANUAL_HOLD`，除非失败原因为不可恢复。

物理位置未知或设备失败时：

- 保留现有 BLOCK/MANUAL_HOLD 语义。
- 不自动转 NG。
- 不写 source item 终态。

manifest 货架位合同异常时：

- source boundary 不存在、歧义或与 `SMT_SORTING_INBOUND` contract version 不匹配时，不创建 session/inbox。
- `target_bin_code` / `target_cell_code` 缺失时，不得 fallback 成 target rack position。
- `SORTING_NG_PLACE` 缺少 `ng_location` / `target_location` evidence 时，进入受控 BLOCK/MANUAL_HOLD，不创建 NG rack position。
- manifest 若重新出现 `SORTING_NG_STATION`、`NG_STATION`、`SORTING_INBOUND_NG`、`WORKSTATION` rack position/resource boundary，测试必须失败。

## 7. 内部接口合同

新增或收敛以下 handoff service 内部方法；若已有同义方法，应收敛为同一入口，不新增平行状态推进逻辑：

- `record_source_pick_success(...)`
  - 输入：db、session/source item evidence、trace_id。
  - 行为：按 source pick 状态机幂等推进 `PICKED` 并重算 demand。
  - 调用方：runtime effect 和 recovery 路径共用。

- `record_source_item_terminal_result(..., terminal_status)`
  - `terminal_status` 只允许 `SORTED` 或 `SKIPPED`。
  - 输入 source item 定位必须来自 session context 的 `source_pick_request`。
  - 行为：幂等写终态、写 `completed_at`、清理失败字段、完成当前 sorting session、重算 demand、尝试 claim 下一条。
  - 对账要求：target resource projection 进入 reconciling 时不得写 `SORTED`。
  - 重放要求：already-terminal 不得再次触发下一条 READY claim。

- `claim_next_source_item(...)`
  - 保持单 item claim。
  - 支持 demand-scoped claim；release/terminal success 优先 claim 同 demand 下一条 READY item。
  - route config 只能选择 manifest-declared source boundary；运行时必须拒绝无效或歧义 source boundary。
  - 增加 target WorkLine 行锁、open session 和 handoff ledger in-flight 检查。
  - 创建 session 时通过 typed helper 写合法 sorting context，并写入 manifest/source/target rack position evidence。
  - source pick admission 使用可注入 ECS realtime probe，默认生产路径不得 allow-idle。
  - 使用两阶段 claim：ECS probe 不持 DB 行锁，短事务内重锁和 recheck 后才写 session/inbox。

- `scan_smt_inbound_handoff_demands_batch(...)`
  - 增加 READY item 兜底 claim。
  - summary 增加 `claimed`。
  - 参数区分 `scan_limit` / `recovery_limit` 和 `claim_limit`。
  - 同一次 scan 内对 target WorkLine / ECS endpoint status probe 做短生命周期去重或批量查询。

## 8. 验收标准

- 新 release fact 产生 READY source item 后，无需人工/API 调用即可创建 `SORTING_SOURCE_PICK_REQUESTED` inbox 和合法 sorting session。
- claim 创建的 session 在 `SORTING_SOURCE_PICK SUCCESS` 后不会因 `sorting.context_schema_version` 缺失进入 `SORTING_CONTEXT_INVALID`。
- `SORTING_SOURCE_PICK SUCCESS` 成功落地后，source item 进入 `PICKED`。
- `SORTING_TARGET_PLACE SUCCESS` 成功落地后，source item 进入 `SORTED`。
- `SORTING_NG_PLACE SUCCESS` 成功落地后，source item 进入 `SKIPPED`。
- `SORTING_NG_PLACE` 仍绑定到 `SORTING_TARGET_ARM`，manifest 不声明独立 NG 设备角色或 NG rack position。
- `SMT_SORTING_INBOUND` manifest 只把源端单层货架位和目标五层货架位声明为 rack positions/resource boundaries；`WORKSTATION`、扫码平台、NG 区、料箱码和料格码不进入 manifest `rack_positions`。
- `SORTING_TARGET_PLACE` 的 target rack position 固定引用目标五层货架位；`target_bin_code` / `target_cell_code` 只出现在 command payload 和 resource fact。
- handoff route 的 source position 必须来自 manifest `SORTING_INBOUND_SOURCE` boundary，配置错误或多源歧义时不得创建 session/inbox。
- 所有 source items 进入终态后，demand 进入 `COMPLETED`。
- 同一 target WorkLine 同时只有一个自动 source item in-flight。
- target/ng 成功后会即时尝试 claim 下一条 READY source item。
- Celery 扫描可以兜底认领 READY item，并保留现有 dead-letter、retry、manual hold 恢复能力。
- 失败、资源等待和对账场景不会错误写入 `SORTED` / `SKIPPED`。

## 9. 测试合同

后续实施必须先补 RED 测试，再实现。

### 9.1 Handoff Claim 测试

- claim 创建的 session 包含 `sorting.context_schema_version=1`。
- claim 创建的 session 保留完整 `source_pick_request` evidence。
- claim 创建的 session 写入 `manifest_contract_version`、`source_rack_position_code` 和 `target_rack_position_code`。
- 同一 target WorkLine 已有 in-flight source item 时，不创建第二个 session。
- route retry 时 item 保持 `READY`，写 `next_attempt_at`。
- route service 测试覆盖：config 选择的 source boundary 必须存在于 manifest `SORTING_INBOUND_SOURCE` boundaries。
- route service 测试覆盖：单 source boundary 可默认选择，多 source boundary 未显式配置时失败，不自动猜测。
- route service 测试覆盖：无效 `source_position_code` 不调用 station lease，不创建 session/inbox。
- PostgreSQL 并发测试覆盖两个并发 claim 同一 target WorkLine 时只有一个成功。
- ECS realtime probe 矩阵覆盖 AUTO/IDLE、MANUAL、RUNNING、current command、timeout/error。
- 两阶段 claim 测试证明 ECS probe 期间不持有 source item / target WorkLine 行锁；最终短事务 recheck 失败时不创建 session/inbox。
- 同一 scan 内多个 READY item 指向同一 target WorkLine / ECS endpoint 时，ECS probe 被去重或批量调用。

### 9.2 Runtime / Plugin 测试

- manifest 不包含 `SORTING_NG_STATION` 设备角色。
- manifest 不包含 `NG_STATION` rack position 或 `SORTING_INBOUND_NG` resource boundary。
- manifest 不包含 `WORKSTATION` rack position 或 `SORTING_INBOUND_WORK` resource boundary。
- manifest 只包含源端单层货架位和目标五层货架位这两类 WES 管理 rack positions。
- `SORTING_NG_PLACE` 的 command target role 是 `SORTING_TARGET_ARM`。
- `SORTING_NG_PLACE` 不声明 target `RackPositionArg`，NG 目标只在 command payload evidence 中表达。
- `SORTING_TARGET_PLACE` 的 target `RackPositionArg` 固定引用目标 rack position，不从 `target_bin_code` / `target_cell_code` 动态解析。
- `target_bin_code` / `target_cell_code` 不得作为 `RackPositionArgSource.path` 的 rack position 值。
- `SOURCE_PICK SUCCESS` effects 成功后 source item 变为 `PICKED`。
- `TARGET_PLACE SUCCESS` effects 成功后 source item 变为 `SORTED`。
- `NG_PLACE SUCCESS` effects 成功后 source item 变为 `SKIPPED`。
- target resource projection reconciling 时不得写 `SORTED`。
- session context 缺少 `source_pick_request` 时进入受控 BLOCK/MANUAL_HOLD，不写终态。
- runtime effect 测试必须证明终态落账由 runtime 调用 handoff service 完成，而不是 plugin 直接写 ledger。
- source pick 正常成功和 recovery 成功共用同一个 `PICKED` service 方法。
- source pick success 状态机测试覆盖：首次成功、重复 `PICKED` no-op、终态后迟到 success、`MANUAL_HOLD` 后迟到 success。
- terminal result 幂等测试覆盖：重复 `SORTED` / `SKIPPED` 不重复 claim 下一条 READY item，冲突终态进入受控 BLOCK/MANUAL_HOLD。
- terminal success 测试覆盖当前 sorting session 进入完成态并记录 terminal evidence。

### 9.3 Celery 测试

- recovery scan 会认领 due READY item，并返回 `claimed`。
- recovery scan 的 `claim_limit` 与扫描/恢复 limit 分离。
- recovery scan 的 `claim_limit` 循环覆盖 ECS probe 去重/批量调用，不把 claim_limit 放大成同 endpoint 多次外部请求。
- recovery scan 保留现有 `FAILED` inbox retry 行为。
- recovery scan 保留现有 `DEAD_LETTER` manual hold 行为。
- recovery scan 保留现有 processed inbox without command 的 manual hold 行为。
- `stale_after_seconds=300` 默认行为不变。

### 9.4 集成测试

覆盖从粗分机 release fact 到 demand completed 的 happy path：

```text
release fact
  -> handoff demand/source item READY
  -> automatic claim
  -> SORTING_SOURCE_PICK_REQUESTED inbox
  -> SOURCE_PICK SUCCESS
  -> WORKING_BIN_SCAN
  -> TARGET_PLACE SUCCESS
  -> source item SORTED
  -> demand COMPLETED
```

覆盖多 item 串行：

- 第一条 item 完成前，第二条不被 claim。
- 第一条 item `SORTED` 或 `SKIPPED` 后，自动 claim 第二条。
- 覆盖 target/ng terminal success 后 demand-scoped 下一条 READY item claim。
- 重放同一 target/ng success 后不重复 claim 第二条。

### 9.5 回归测试

- source pick failed 不写 `PICKED` / `SORTED` / `SKIPPED`。
- target place failed unknown location 进入 BLOCK/MANUAL_HOLD。
- target no capacity 保持 `WAITING_TARGET_BIN_SWITCH`。
- station busy 不创建 session，不丢 READY item。
- route manual hold 继续写 item/demand failure code。

## 10. 实施任务拆分

后续实施按以下顺序推进，避免先写流程代码再补状态保护：

1. 补 RED 测试：先覆盖 manifest rack-position 合同、release-to-terminal E2E、runtime/service 终态落账、PostgreSQL 并发锁、ECS probe 矩阵、Celery `claimed`。
2. 收敛 SMT sorting manifest：删除 `SORTING_NG_STATION`、`NG_STATION`、`WORKSTATION` rack position/resource boundary；修正 `SORTING_TARGET_PLACE` / `SORTING_NG_PLACE` 的 `RackPositionArg` 合同。
3. 先抽 handoff 状态机/helper：实现 `PICKED` / `SORTED` / `SKIPPED` 幂等推进、terminal 冲突处理和 sorting session completion，用单测锁定语义。
4. 收敛 route/claim：实现 manifest source boundary 解析、typed context、两阶段 claim、target WorkLine 串行保护、demand-scoped claim 和最终短事务 recheck。
5. 接入 runtime effect：source pick success 和 target/ng terminal success 只通过 handoff service 写 ledger，并保留 resource/context intents 原有语义。
6. 收敛 Celery 兜底：拆分扫描/恢复 limit 与 READY `claim_limit`，保留现有 retry、dead-letter、manual hold 恢复能力，并在 scan 内去重/批量 ECS probe。
7. 回归质量门：运行聚焦测试后再运行受影响模块测试，确认无 API 层越级访问数据库或 Repository。

## 10.1 What Already Exists

- v0.6.2.0 已提供 manifest `RackPosition` / `RackPositionArg` / `ResourceBoundary` 数据合同、引用校验和 manifest summary API。
- `RuntimeQueryService._single_layer_boundary_positions` 已从 manifest `resource_boundaries` 推导单层货架边界；第三轮要求是让 SMT manifest 不再把 NG/WORKSTATION 放入该边界集合。
- `SortingInboundContext` 已提供 `sorting.context_schema_version`、`current_material`、`pending_target_placement` 和 `stations.scan_platform` 的 typed helper 基础。
- `SmtInboundHandoffRouteService` 已有 route retry/manual hold、station lease、open session 和 ECS probe 注入点；第三轮要求是把 source position 来源收敛到 manifest boundary。
- `SmtInboundHandoffService.claim_next_source_item` 已有 source item claim、sorting session/inbox 创建和 demand 聚合入口；第三轮要求是两阶段短锁和 manifest evidence。
- Runtime effect 已能落地 command/outbox、resource fact 和 context update；第三轮要求是补 handoff ledger 落账调用。

## 10.2 NOT In Scope

- 不实现前端配置向导或 runtime scene 展示改版；只要求后端 manifest/API 合同正确。
- 不生产化 CTU/WMS/NG 对账链路，继续由既有 P1 TODO 跟踪。
- 不定义运营看板、告警阈值或现场 Runbook，首版运行数据后再做。
- 不引入同一 target WorkLine 多 item 并发处理。
- 不实现多个目标五层货架位的动态 allocator；本期 target rack position 固定引用 manifest target boundary。
- 不把 NG 区、工作站、扫码平台、料箱或料格建模为 WES 管理 rack position。

## 10.3 Worktree Parallelization Strategy

本 SPEC 涉及同一 `workline`/`workline_plugins`/`workline_runtime` 运行闭环，建议顺序实施，避免 manifest 合同和 runtime ledger 同时分支改动产生冲突。

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Manifest 合同测试与插件 manifest 收敛 | `src/workline_plugins/`, `src/workline_runtime/`, `tests/workline_runtime/` | — |
| Handoff 状态机与 route/claim 收敛 | `src/app/workline/`, `tests/workline_runtime/`, `tests/integration/workline_runtime/` | Manifest 合同 |
| Runtime effect 与 Celery 兜底 | `src/workline_runtime/`, `src/celery_app/`, `tests/workline_runtime/` | Handoff 状态机 |

Sequential implementation, no parallelization opportunity.

## 10.4 Implementation Tasks

Synthesized from four rounds of Eng Review. Each task is deliberately staged so the implementation can ship in small, reviewable patches.

- [ ] **T0 (P1, human: ~30min / CC: ~8min)** — Implementation preflight — 逐 symbol 跑 GitNexus impact 并冻结改动顺序
  - Surfaced by: 第四轮 Step 0 Scope Challenge；本 SPEC 会触达 `src/workline_plugins/`、`src/workline_runtime/`、`src/app/workline/`、`src/celery_app/` 和集成测试，超过 8 文件复杂度门槛。
  - Files: 无代码改动；记录 impact 目标至少包括 `SmtSortingInboundPlugin`、`SortingInboundContext`、`SmtInboundHandoffRouteService`、`SmtInboundHandoffService.claim_next_source_item`、`runtime_intent_effects` 中 SMT source-pick correlation 路径、`scan_smt_inbound_handoff_demands_batch`。
  - Verify: impact 输出无 HIGH/CRITICAL 未确认风险；若有 HIGH/CRITICAL，先回到用户确认。

- [ ] **T1 (P1, human: ~2h / CC: ~20min)** — SMT manifest — 先写 RED 测试并收敛 v0.6.2.0 rack-position 静态合同
  - Surfaced by: 第三轮 Architecture Review D1/D2/D3；第四轮代码证据显示当前 manifest 仍声明 `ROLE_SORTING_NG_STATION`、`POSITION_NG_STATION`、`POSITION_WORKSTATION` 和对应 resource boundaries。
  - Files: `src/workline_plugins/smt_sorting_inbound/plugin.py`, `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`, `tests/workline_runtime/test_plugin_manifest_and_topology.py`。
  - Verify: `uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_plugin_manifest_and_topology.py -k "smt_sorting_inbound or rack_position"`。

- [ ] **T2 (P1, human: ~1h / CC: ~12min)** — Typed context — 为 `SortingInboundContext` 增加 source pick request helper
  - Surfaced by: 第四轮 Code Quality Review；当前 helper 已能写 `sorting.context_schema_version`，但 claim 仍手写 `source_pick_request` JSON。
  - Files: `src/workline_plugins/smt_sorting_inbound/context.py`, `tests/workline_runtime/test_smt_inbound_handoff_claim.py` 或新增 context 单测。
  - Verify: 测试证明 helper 写入 `context_schema_version=1`、`source_pick_request`、`stations.scan_platform=EMPTY`，且 Decimal/Mapping evidence JSON-safe。

- [ ] **T3 (P1, human: ~2h / CC: ~20min)** — Handoff route — 将 source position 准入绑定到 manifest source boundary
  - Surfaced by: 第三轮 Architecture Review D4；第四轮代码证据显示 route 当前仍读取 `source_station_code` / `source_position_code` 自由字符串。
  - Files: `src/app/workline/domain/services/smt_inbound_handoff_route_service.py`, `tests/workline_runtime/test_smt_inbound_handoff_route_service.py`。
  - Verify: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_route_service.py -q`。

- [ ] **T4 (P1, human: ~3h / CC: ~30min)** — Handoff claim — 实现两阶段 claim、target WorkLine 串行保护和 manifest evidence
  - Surfaced by: 第一/二/三轮 Architecture Review；第四轮代码证据显示当前 `claim_next_source_item` 在拿到 READY item 后继续 route/session/inbox，且默认 probe 是 allow-idle stub。
  - Files: `src/app/workline/services/smt_inbound_handoff_service.py`, `src/app/workline/repositories/smt_inbound_handoff_repository.py`, `tests/workline_runtime/test_smt_inbound_handoff_claim.py`, `tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py`。
  - Verify: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_claim.py tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py -q`。

- [ ] **T5 (P1, human: ~2h / CC: ~20min)** — Source pick ledger — 抽出幂等 `record_source_pick_success(...)`
  - Surfaced by: 第四轮 Architecture/Code Quality Review；当前 recovery 路径直接把 command success 修复为 `PICKED`，容易与正常 runtime effect 形成状态语义分叉。
  - Files: `src/app/workline/services/smt_inbound_handoff_service.py`, `src/workline_runtime/runtime_intent_effects.py`, `tests/workline_runtime/test_smt_inbound_handoff_recovery.py`, `tests/workline_runtime/test_runtime_intent_effects.py`。
  - Verify: source pick success 首次推进、重复 `PICKED` no-op、终态后迟到 success、`MANUAL_HOLD` 后迟到 success 均有测试。

- [ ] **T6 (P1, human: ~3h / CC: ~30min)** — Terminal ledger — 抽出幂等 `record_source_item_terminal_result(..., SORTED/SKIPPED)`
  - Surfaced by: 第一/二轮 Architecture Review；terminal success 必须完成 source item、demand 和 sorting session，且重复 success 不得重复 claim 下一条。
  - Files: `src/app/workline/services/smt_inbound_handoff_service.py`, `src/workline_runtime/runtime_intent_effects.py`, `src/workline_plugins/smt_sorting_inbound/flow_service.py`, `tests/workline_runtime/test_runtime_intent_effects.py`, `tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py`。
  - Verify: target success -> `SORTED`、NG success -> `SKIPPED`、resource reconciling 不写 `SORTED`、context 缺 source_pick_request 进入受控 hold、session completion evidence 全覆盖。

- [ ] **T7 (P1, human: ~2h / CC: ~20min)** — Celery recovery — 增加 READY claim 兜底、`claim_limit` 和同 scan ECS probe 去重
  - Surfaced by: 第一/二轮 Performance Review；第四轮代码证据显示 Celery summary 仍无 `claimed`，service scan 只扫 due demand 与 stuck item。
  - Files: `src/app/workline/services/smt_inbound_handoff_service.py`, `src/celery_app/tasks/workline.py`, `src/celery_app/config.py`, `tests/workline_runtime/test_smt_inbound_handoff_recovery.py`, `tests/workline_runtime/test_smt_inbound_handoff_celery.py`。
  - Verify: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_recovery.py tests/workline_runtime/test_smt_inbound_handoff_celery.py -q`。

- [ ] **T8 (P1, human: ~3h / CC: ~30min)** — E2E regression gate — 跑通 release-to-terminal 和多 item 串行闭环
  - Surfaced by: 第四轮 Test Review；单元测试无法证明 release fact、runtime effect、handoff ledger、Celery 兜底和 demand completion 的跨服务闭环。
  - Files: `tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py`, `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`, `tests/workline_runtime/test_smt_inbound_handoff_claim.py`。
  - Verify: `uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_smt_inbound_handoff_claim.py tests/workline_runtime/test_smt_inbound_handoff_recovery.py tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py -q`。

P2 follow-up：

- 首版上线数据稳定后，再设计自动 claim 指标、告警阈值、运营看板和吞吐调优策略。

## 11. 实施前门禁

后续真正实施前必须遵守项目规则：

- 修改函数、类或方法前运行 GitNexus impact analysis。
- HIGH/CRITICAL 风险必须先向用户汇报。
- 使用 `uv run ...` 运行项目测试命令。
- 不从 API 层直接访问数据库或 Repository。
- Commit 前运行 GitNexus detect changes。

建议实施入口：

- `src/app/workline/services/smt_inbound_handoff_service.py`
- `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- `src/workline_runtime/runtime_intent_effects.py`
- `src/celery_app/tasks/workline.py`
- `tests/workline_runtime/`
- `tests/integration/workline_runtime/`

### 11.1 第四轮实施前评审结论

第四轮实施前评审结论：SPEC 方向正确，可以实施；但实现必须按 T0-T8 分阶段推进。原始范围会同时触达 manifest、route、claim、runtime effect、Celery 和 E2E，超过 8 文件复杂度门槛；因此不建议一个 PR 做完整闭环。

已验证代码证据：

- `src/workline_plugins/smt_sorting_inbound/plugin.py` 当前仍声明 `ROLE_SORTING_NG_STATION`、`POSITION_NG_STATION`、`POSITION_WORKSTATION` 和 `SORTING_INBOUND_NG` / `SORTING_INBOUND_WORK` resource boundaries，必须由 T1 先收敛。
- `src/workline_runtime/plugin_manifest.py` 已把 `RackPosition` 定义为 WES 管理的货架停靠位/库存事实锚点，并已有 `RackPositionArg` 引用校验；本 SPEC 不需要改 manifest 基础数据模型。
- `src/workline_plugins/smt_sorting_inbound/context.py` 已有 `sorting.context_schema_version` typed helper，但还没有 `source_pick_request` helper；T2 必须先补 helper，避免 claim 路径继续手写 JSON。
- `src/app/workline/domain/services/smt_inbound_handoff_route_service.py` 当前仍从 route config 读取 `source_station_code` / `source_position_code`，且默认 `_allow_idle_ecs_probe` 返回可用；T3/T4 必须改为 manifest source boundary + 生产真实 ECS probe。
- `src/app/workline/services/smt_inbound_handoff_service.py` 当前 claim 在取得 READY item 后继续 route/session/inbox 创建，尚不是两阶段短锁；T4 必须避免外部 ECS probe 持有 DB 行锁。
- `src/app/workline/services/smt_inbound_handoff_service.py` 当前 recovery success 直接写 `PICKED`；T5 必须抽单一 service 方法，防止 runtime 正常路径和 recovery 路径分叉。
- `src/celery_app/tasks/workline.py` 的 SMT handoff recovery summary 当前没有 `claimed`；T7 必须把 READY claim 兜底变成显式合同和测试断言。

实施前覆盖图：

```text
CODE PATHS                                                     USER FLOWS
[+] Manifest static contract                                   [+] Release-to-terminal happy path [->E2E]
  |-- [GAP] no NG device role                                      |-- [GAP] release fact -> READY -> auto claim
  |-- [GAP] no NG/WORKSTATION rack position                       |-- [GAP] SOURCE_PICK SUCCESS -> PICKED
  |-- [GAP] no NG/WORK resource boundary                          |-- [GAP] TARGET_PLACE SUCCESS -> SORTED
  `-- [GAP] TARGET_PLACE fixed target rack arg                    `-- [GAP] demand -> COMPLETED
[+] Typed context / claim
  |-- [GAP] source_pick_request helper                         [+] NG terminal path [->E2E]
  |-- [GAP] manifest/source/target evidence                       |-- [GAP] NG_PLACE SUCCESS -> SKIPPED
  |-- [GAP] two-stage claim, no HTTP under row lock                `-- [GAP] no NG rack position/fact boundary
  `-- [GAP] target WorkLine serial guard
[+] Runtime ledger                                             [+] Multi-item serial path [->E2E]
  |-- [GAP] SOURCE_PICK success shared service                    |-- [GAP] second item waits while first in-flight
  |-- [GAP] TARGET terminal shared service                        |-- [GAP] terminal success claims next READY
  `-- [GAP] NG terminal shared service                            `-- [GAP] replay success does not double-claim
[+] Celery recovery
  |-- [GAP] READY claim fallback with claimed summary
  |-- [GAP] claim_limit separate from scan/recovery limit
  `-- [GAP] per-scan ECS probe dedupe/batch

COVERAGE: 当前代码已有部分 claim/recovery smoke tests，但本 SPEC 新合同仍以 RED 测试为主。
QUALITY TARGET: 所有 GAP 必须在对应 T1-T8 阶段先红后绿；E2E 路径不得只靠单测替代。
```

关键失败模式矩阵：

| Failure mode | Test requirement | Handling requirement | User/ops visible outcome |
| --- | --- | --- | --- |
| Manifest 重新声明 NG/WORKSTATION rack position | T1 manifest shape test | manifest 初始化或测试失败 | 开发阶段失败，不进入运行时 |
| 多 source boundary 未配置 route | T3 route ambiguity test | 不创建 session/inbox，写 retry/config failure evidence | demand/source item 保持可诊断 |
| ECS probe timeout | T4 probe matrix test | 不持锁等待；source item 保持 READY 并写 `next_attempt_at` | Celery 可重试，非静默丢盘 |
| 第二阶段 recheck 发现 target WorkLine 已 in-flight | T4 concurrency test | 不创建第二个 session/inbox | 同线串行，避免双盘并发 |
| SOURCE_PICK SUCCESS 重放 | T5 idempotency test | `PICKED` no-op，不重复推进 | 无重复 claim |
| TARGET/NG terminal SUCCESS 重放 | T6 no-double-claim test | already-terminal no-op，不重复 claim 下一条 | 无重复出库/放盘 |
| `MATERIAL_MOUNTED` reconciling | T6 resource projection test | 不提前写 `SORTED` | 进入受控对账/hold |
| Celery scan 多 READY item 同 endpoint | T7 performance test | probe 去重或批量查询，受 `claim_limit` 控制 | 避免兜底任务放大 ECS/DB 压力 |

本轮不新增 TODO：`TODOS.md` 已覆盖 SMT 分拣入库完整 CTU/WMS/NG 对账链路、Handoff 运营看板和 Workline worker benchmark；新增重复 TODO 会稀释后续队列。

## 12. 质量门记录

Plan Mode 讨论期间已完成以下 SPEC 质量检查：

- GitHub issue 去重：仓库 issues 已禁用，无法 filed/merge issue。
- 语义敏感信息检查：clean。
- redaction scan：clean。
- Codex 只读质量门：`8/10`。

Codex 质量门指出的模糊点已吸收到本文：

- READY item 精确定义。
- 串行锁粒度。
- route retry 状态和 `next_attempt_at` 规则。
- demand 聚合终态枚举。
- `CLAIMED_BY_SORTING` / `SORTING_IN_PROGRESS` 判定边界。
- target/ng success 如何定位 source item。
- Celery stuck recovery 阈值和 summary 字段。

第三轮 Eng Review 已补充并吸收到本文：

- v0.6.2.0 manifest rack-position rename 后，`RackPosition` 只表达 WES 管理货架停靠位/库存事实锚点。
- 删除 NG 静态资源化后，`SORTING_NG_PLACE` 不再声明 target rack position arg。
- `WORKSTATION` 不再作为 manifest rack position/resource boundary。
- `target_bin_code` / `target_cell_code` 不再冒充 `target_position_code` 的动态 rack position 来源。
- handoff route 的 source position 必须运行时校验到 manifest `SORTING_INBOUND_SOURCE` boundary。
- 测试矩阵必须覆盖 manifest shape、route invalid boundary、claim manifest evidence 和 no-double-claim。

第四轮实施前 Eng Review 已补充并吸收到本文：

- 原完整闭环实施范围触发复杂度门槛，必须按 T0-T8 阶段门禁推进，不做单个大补丁。
- `SortingInboundContext` 需要先补 `source_pick_request` typed helper，再让 claim 写 context。
- source pick success 正常路径和 recovery 路径必须共用 `record_source_pick_success(...)`。
- target/ng terminal success 必须共用 `record_source_item_terminal_result(...)`，并由该入口完成 session completion 和 no-double-claim。
- Celery READY claim 兜底必须显式输出 `claimed`，并把 `claim_limit` 与 scan/recovery limit 分离。
- 实施前覆盖图、失败模式矩阵和验证命令已写入 11.1。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 未运行；本轮为后端工程闭环，不新增产品范围 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 未运行 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 4 | CLEAR | 第四轮实施前评审 6 issues, 0 critical gaps；已折叠阶段化 T0-T8、typed context helper、source pick/terminal shared ledger、Celery claimed summary、覆盖图和失败模式矩阵 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 非 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

**VERDICT:** ENG CLEARED — ready to implement by T0-T8 after GitNexus impact analysis and RED tests.

NO UNRESOLVED DECISIONS
