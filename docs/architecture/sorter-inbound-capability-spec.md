# Sorter Inbound Capability SPEC

> 状态：Phase 4 设计 SPEC，未实现
> 父计划：`workline-and-plugin-restructuring.md` §10.5

---

## 1. 边界声明

本 SPEC 定义粗分机、满箱交换和分拣机入库的目标态能力边界。实现时必须按 runtime/orchestration、wms_integration port、device command、MaterialLocationQuery 和 ReconciliationManager 重建，不复用旧 plugin 入口，不保留旧 plugin 兼容入口。

不预留 RCS/AGV/CTU direct provider adapter；默认由 WMS 中转履约。只有客户要求绕过 WMS，或 WMS 实测无法满足实时性时，才另写 `fulfillment-provider-adapter-spec.md`。

## 2. Residual Readiness

| 遗留门禁 | 本 SPEC 处理方式 |
| --- | --- |
| Phase 1 callback admission 未关闭 | 所有 WMS/ECS callback 只描述目标态 normalizer admission，不假设旧 callback 可扩展 |
| Phase 2 `WorkLine.runtime_status` 未清空 | 入库流程状态归 ExecutionWorkItem、WmsFulfillmentRequest、MaterialUnit、CellReservation 等 owner，不写 WorkLine 运行状态 |
| Phase 3 RuntimeInbox / P0 E2E / benchmark 未关闭 | 设计可完成；实现前必须通过 Phase 3 closure gate，尤其是 RuntimeInbox cutover 与 queue writer PostgreSQL evidence |

## 3. 粗分机正常流

目标态流程：

1. 入料机械臂扫码/测量并写入对象级 evidence。
2. WMS GRN 绑定与测量校验通过 WMS port 完成。
3. 入料机械臂投流水线，粗分机流水线推进到出料口。
4. 出料格位分配并创建 CellReservation。
5. 必要时请求 WMS 补空箱货架。
6. 出料机械臂投格后，先落本地物理位置事实和格位占用。
7. 再通知 WMS PKG 绑定或库存事务。
8. WMS 失败进入 WMS_SYNC_PENDING 或 RECONCILING，不抹掉本地物理事实。

当前对象进入流水线后，入料机械臂可处理下一个对象；并发边界由 work item、queue membership 和 DeviceDispatchPolicy 控制。

## 4. 满箱交换前置分流

目标态流程：

- 粗分机移出单层货架后，先进入满箱交换区或交换决策点。
- 无满箱需求时，进入分拣机 STATION / 排队区。
- 有满箱需求时，创建 `FULL_BOX_EXCHANGE`，按 `rack_code + rack_side` 分批。
- 必要时 `CHANGE_RACK_FACE` 是独立履约，不合并到 exchange 成功。
- 满箱物料完成箱级入库和 WMS 同步后，剩余未满箱物料才进入分拣机逐件流程。

满箱交换区与分拣机 STATION A/B 不得混用；满箱交换完成前，分拣机北向机械臂不得对该单层货架取料。

## 5. 分拣机入库正常流

目标态流程：

1. STATION A/B 与 FIVE STATION admission。
2. WMS/CTU 批量投箱入线与逐箱 callback。
3. SCAN1 授权料箱 resolve；未授权或冲突进入 NG / RuntimeHold / RECONCILING。
4. SCAN2/SCAN3 路由到工作位、退料线或 NG。
5. 北向机械臂取料到扫码平台。
6. 扫码后进行格位分配和 CellReservation。
7. 必要时换箱或等待目标料箱。
8. 南向机械臂投料。
9. 先落本地位置事实与格位占用，再通知 WMS PKG 绑定或库存事务。

默认 `source_arm_prefetch_capacity=0`。未显式声明预取能力时，北向机械臂必须等待扫码平台 FREE 后才能取下一件。

## 6. CellReservation 消费合同

CellReservation 表示目标格位预约，不表示物理完成。它是 Phase 4 三个 SPEC（sorter-inbound、material-location-query、smt-ng-wms-reconciliation）的共同依赖，完整生命周期、目标语义与现有 `WorklineBinCellReservation` 状态映射以 `cell-reservation-spec.md` 为准。

本 SPEC 只声明入库流程如何消费 CellReservation：

| 职责 | 负责方 | 说明 |
|------|--------|------|
| 创建预约 | SorterInbound capability（格位分配逻辑） | 校验目标料箱处于滚筒线工作位、目标格位可预约 |
| 确认占用 | DeviceCommand RESULT callback（投放成功 evidence） | 只有 ECS 投放成功 evidence 才能转 OCCUPIED |
| 释放预约 | SorterInbound capability + TTL 定时器 | TTL 过期且未物理投放时自动释放 |
| WMS reject 处理 | ReconciliationManager | WMS 拒绝 PKG 绑定时，预约进入 RECONCILING |
| source_version drift 处理 | ReconciliationManager | WMS 格位数据版本漂移时，预约进入 RECONCILING |

### 6.1 TTL 与过期策略

| 场景 | TTL | 过期行为 |
|------|-----|---------|
| 出料格位预约（粗分机） | 30s | 过期 → RELEASED（前提：未物理投放） |
| 分拣机格位预约 | 60s | 过期 → RELEASED（前提：未物理投放） |
| 已物理投放的格位 | 无 TTL | TTL 过期不得静默释放；只能通过 WMS 确认或人工 reconcile 释放 |

### 6.2 异常回滚矩阵

| 异常 | 预约状态转移 | 格位状态 | 后续动作 |
|------|------------|---------|---------|
| 投放 DeviceCommand 失败 | RESERVED → RELEASED | 释放 | 重新分配格位或换箱 |
| 投放超时（ECS 无响应） | RESERVED → RECONCILING | 冻结 | ECS status probe → 确认投放结果 |
| WMS PKG 绑定拒绝 | OCCUPIED → RECONCILING | 冻结（物理事实保留） | WMS_SYNC_PENDING → 重试或人工 reconcile |
| active projection 冲突 | RESERVED → RECONCILING | 冻结 | ReconciliationManager 仲裁 |
| source_version drift | RESERVED → RECONCILING | 冻结 | WMS drift query → 确认格位仍可用 |
| 重复预约同一格位 | 409 Conflict | 不变 | 返回已有预约 + 审计事件 |

### 6.3 与其他 SPEC 的集成合同

| 消费方 | 消费方式 | 合同 |
|--------|---------|------|
| MaterialLocationQuery | 位置来源优先级 #3 | RESERVED/OCCUPIED 状态正确展示；RELEASED 不展示；RECONCILING 标记冲突 |
| WorklineActiveObjects | 通过 MaterialLocationQuery 间接消费 | active object 展示预约 deadline 和状态 |
| SMT/NG/WMS Reconciliation | 预约冲突 → RECONCILING | WMS reject 或 drift 触发对账，不静默覆盖预约 |

## 7. CTU 父子 work item 查询视图

CTU 批量履约必须保留父请求和逐对象子 work item。查询视图展示：

- 子项缺失、重复、乱序和未 resolve placeholder。
- 部分失败与批次收敛状态。
- 父请求成功但子项未收敛时，不得显示业务完成。

## 8. 行为契约测试

- 粗分机正常流完整通过，且 WMS 失败不抹掉本地物理事实。
- 满箱交换前置分流按有无满箱需求分支。
- `CHANGE_RACK_FACE` 独立履约，不能被 full-box exchange 吞并。
- 已满箱交换入库的物料不得进入逐件分拣候选集。
- SCAN1 未授权料箱进入 NG / RuntimeHold / RECONCILING。
- 物料 work item 与料箱 work item join 条件缺一不可。
- source arm prefetch 未声明时容量为 0。
- CTU 父请求不能掩盖子项缺失或部分失败。

## 9. 实施前置条件

实现前必须关闭 Phase 1 callback admission、明确 Phase 2 runtime status 兼容投影口径，并通过 Phase 3 closure gate。否则只能保留为设计和 characterization mapping。

## 10. Runtime 集成映射

> 将 3 大流程的每个步骤映射到已有 runtime/orchestration 实体。标注 ✅（已存在）/ 🆕（需新建）。

### 10.1 粗分机正常流

| 步骤 | Runtime 实体 | 状态 |
|------|-------------|------|
| 入料机械臂扫码/测量 → 对象级 evidence | `ExecutionWorkItem` (object_type=material, step_status=PENDING→IN_PROGRESS) | ✅ |
| WMS GRN 绑定与测量校验 | `WmsDocumentPort.get_grn()` (query-only, 不写 IntentLog) → 校验通过后 `RuntimeIntentLog` (target_domain=wms_integration, 后续 mutation) | ✅ |
| 入料机械臂投流水线 | `RuntimeIntentLog` → `DeviceCommandPort` | ✅ |
| 粗分机流水线推进到出料口 | `ConveyorQueueMembership` (queue_code 按 manifest) | ✅ |
| 出料格位分配 → CellReservation | `CellReservation` (♻️ 复用 `WorklineBinCellReservation`，目标语义映射见 `cell-reservation-spec.md` §3) | ♻️ |
| WMS 补空箱货架 | `WmsFulfillmentPort.request_rack_supply()` → `RuntimeIntentLog` | ✅ |
| 出料机械臂投格 → 本地位置事实 | `RuntimeLocationEvent` (🆕 目标态位置事实表；实现前需新增，或明确由 `ObjectTransitionEvent` 演进承载并补迁移合同) → `BinCellOccupancy` / `MaterialUnit.location_summary` | 🆕 |
| WMS PKG 绑定通知 | `RuntimeIntentLog` → `WmsFulfillmentPort.notify_pkg_binding()` | ✅ |
| WMS 库存事务 | `RuntimeIntentLog` → `WmsInventoryTransactionPort` | ✅ |
| WMS 失败 → WMS_SYNC_PENDING / RECONCILING | `RuntimeHold` + `ReconciliationManager` | ✅ |

### 10.2 满箱交换前置分流

| 步骤 | Runtime 实体 | 状态 |
|------|-------------|------|
| 粗分机移出单层货架 | `WmsFulfillmentPort.request_rack_transport()` → `RuntimeIntentLog` | ✅ |
| 满箱需求判断 → FULL_BOX_EXCHANGE | `WmsFulfillmentPort.full_box_exchange()` → `RuntimeIntentLog` | ✅ |
| rack_code + rack_side 分批 | `ExecutionWorkItem` (parent_correlation_id 批次追溯) | ✅ |
| CHANGE_RACK_FACE 独立履约 | `WmsFulfillmentPort.change_rack_face()` → `RuntimeIntentLog` | ✅ |
| 满箱物料箱级入库 + WMS 同步 | `RuntimeIntentLog` → `WmsInventoryTransactionPort` | ✅ |
| 剩余未满箱物料 → 分拣机逐件 | `ExecutionWorkItem` (新 work item, parent 指向满箱批次) | ✅ |

### 10.3 分拣机入库正常流

| 步骤 | Runtime 实体 | 状态 |
|------|-------------|------|
| STATION A/B + FIVE STATION admission | `ExecutionSession` (start admission) | ✅ |
| WMS/CTU 批量投箱入线 + 逐箱 callback | `WmsFulfillmentPort` → `RuntimeInbox` (ACK-before-processing) | ✅ |
| SCAN1 授权料箱 resolve / 未授权 NG | `ConveyorQueueMembership` (placeholder→bin_code resolve) + `RuntimeHold` | ✅ |
| SCAN2/SCAN3 路由到工作位/退料线/NG | `RuntimeIntentLog` → `DeviceCommandPort` (输送线路由命令) | ✅ |
| 北向机械臂取料到扫码平台 | `RuntimeIntentLog` → `DeviceCommandPort` | ✅ |
| 扫码后格位分配 → CellReservation | `CellReservation` (♻️ 复用 `WorklineBinCellReservation`，目标语义映射见 `cell-reservation-spec.md` §3) | ♻️ |
| 换箱/等待目标料箱 | `RuntimeHold` (scope=object, deadline 驱动) | ✅ |
| 南向机械臂投料 | `RuntimeIntentLog` → `DeviceCommandPort` | ✅ |
| 本地位置事实 + 格位占用 | `RuntimeLocationEvent` (🆕 目标态位置事实表；实现前需新增，或明确由 `ObjectTransitionEvent` 演进承载并补迁移合同) → `BinCellOccupancy` | 🆕 |
| WMS PKG 绑定通知 | `RuntimeIntentLog` → `WmsFulfillmentPort.notify_pkg_binding()` | ✅ |
| WMS 库存事务 | `RuntimeIntentLog` → `WmsInventoryTransactionPort` | ✅ |

## 11. 实时决策延迟预算

> 输送线是物理设备，SCAN 事件到达后必须在数百毫秒内做出路由决策。

| 决策点 | P95 延迟目标 | 超时降级行为 |
|--------|------------|------------|
| SCAN1 扫码 → 授权判断 | < 200ms | 未授权 → NG 路由；超时 → `RuntimeHold` + 告警 |
| SCAN2/SCAN3 扫码 → 路由决策 | < 200ms | 超时 → 保持当前队列，触发 `RuntimeHold` |
| 格位分配 → CellReservation 创建 | < 100ms | 超时 → 对象进入等待队列，deadline 触发换箱或 `RuntimeHold` |
| DeviceCommand 下发 → ECS ACK | < 500ms | ACK 超时 → `DeviceCommand` lease 过期 → `RuntimeReconciliation` |
| 全链路 (SCAN1 → 投料完成) | P95 < 30s | 任一步骤超时 → 该对象 `RuntimeHold`，不阻塞其他对象 |

## 12. Phase 5 legacy 判定

旧 rough_sorter / smt_sorting_inbound / full-box 相关入口只有在对应目标态 behavior contract 通过后才能删除。未被合同覆盖的 legacy 只能冻结入口并保留 characterization tests。
