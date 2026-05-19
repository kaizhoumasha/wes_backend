# Resource 资源模型破坏性优化设计

## 背景

SMT 粗分机流程已经明确两个资源事实边界：

1. WMS/RCS 回传有效空架到达后，WES 需要记录货架、4 个料箱和空料格快照。
2. 出料臂把物料实际放入料格成功后，WES 需要记录物料占用料箱内部格位的事实。

当前 `resource` 模型已经具备资源主数据、事实事件、投影和快照的雏形，但边界没有被稳定表达：

- `ResourceStateEvent` 适合作为 append-only 事实账本。
- `RackPlacement` 应表达货架当前占用的工作线停靠位。
- `RackBinMount` 能表达货架槽位挂载料箱。
- `BinContentSnapshot` / `BinContentSnapshotItem` 能表达料箱内容历史快照。
- `RackRelease` / `RackReleaseBinSnapshot` 是满箱释放流程证据，与 `workline_inbox`、`workline_outbox`、`workline_timelines` 职责重复。
- `RackMaterialMount` 以 `rack_code + rack_slot_code` 表达物料占用，不适合 SMT 料箱内部格位。

该系统尚未发布，允许破坏性迁移，不需要保留向后兼容。

## 目标

用第一性原理重整 resource 域：只保留清晰的资源实体、事实事件、当前投影和历史证据边界，让 WMS 到架、料箱挂载、物料放入、满箱释放和后续对账都能走同一套模型。

## 第一性原理审查结论

从 SRS 和 ADR 出发，WES 的资源模型必须先回答三个问题：

1. **WES 对什么负责**：WES 负责自动化执行编排、设备指令、料格分配、执行事实记录和异常对账。
2. **WMS/RCS 对什么负责**：WMS/RCS 负责库存主账、空箱资源授权、RCS/CTU 搬运闭环和外部地码事实。
3. **设备控制系统对什么负责**：ECS/设备控制系统负责把 WES 下发的逻辑位置解析为物理坐标并执行动作。

因此，WES 不应把 WMS/RCS 地码或设备物理坐标建成自己的当前状态主账。WES 的主状态边界应是 **工作线上的货架停靠位**：

- 工作线和设备决定 WES 的任务路由。
- 货架停靠位决定某条工作线当前可使用哪个货架。
- 货架槽位、料箱格位决定机械臂指令和料格算法。
- WMS/RCS 地码、货位条码、设备坐标只作为外部证据或指令逻辑位置保存。

SMT 分拣机的“三个货架可停留位置”应建成工作线能力配置，而不是库存地码：

| 位置 | 角色 | 允许货架 | WES 语义 |
| --- | --- | --- | --- |
| `FIVE_LAYER_STORAGE` | 五层货架接驳/来源位 | `FIVE_LAYER` | 表示该工作线当前可从哪个五层货架接驳或拣选 |
| `SINGLE_LAYER_A` | 单层货架输出/缓存位 | `SINGLE_LAYER` | 表示该工作线当前可向哪个单层货架装箱 |
| `SINGLE_LAYER_B` | 单层货架输出/缓存位 | `SINGLE_LAYER` | 支持双单层位轮换、满架释放和补空架 |

同一时刻，一个工作线停靠位最多只能有一个 active 货架；同一货架最多只能 active 在一个工作线停靠位。

## 非目标

- 不设计 WMS 主库存账本，WMS 仍是库存主账。
- 不让插件直接访问数据库。
- 不把所有资源关系合并成一张 JSON 化大表。
- 不保留 `RackMaterialMount` 的兼容 API 或迁移旧数据。
- 不把 WMS/RCS 地码、ECS 物理坐标或库存 location 建成 WES 的当前状态主账。

## 设计原则

1. **事实先行**：所有外部或运行时事实先写 `ResourceStateEvent`，再更新 active 投影。
2. **计划不落状态**：料格分配只是计划，不写 active resource 状态。
3. **物理成功才落占用**：只有 OUTPUT_ARM `PICK_AND_PUT SUCCESS` 才写物料占用。
4. **工作线位置优先**：WES active 货架位置以 `workline_code + position_code` 表达，不以 WMS/RCS 地码表达。
5. **快照只作证据**：快照用于追溯和对账，不作为当前状态主来源。
6. **冲突不覆盖**：事实可以追加，active 投影冲突时创建 RuntimeHold 并进入对账。
7. **外部位置作证据**：`external_location_code`、`rack_slot_location_code`、`bin_cell_location` 可保存和下发，但不能替代工作线停靠位投影。
8. **流程证据归 WorkLine**：外部请求、回调、插件决策、等待状态、失败原因和人工挂起统一由 `workline_inbox`、`workline_outbox`、`workline_timelines`、`workline_sessions.context_json`、`runtime_holds` 承载。

## 目标模型分层

### 1. 资源实体

资源实体表继续表达稳定资源主数据：

- 货架类型、货架实例、货架槽位模板
- 料箱类型、料箱实例、料箱内部槽位模板

这些表不记录流程事实，也不表达当前挂载关系。

`resource` 域不复制 WorkLine/Device 主数据。工作线、设备、插件配置和工作线货架停靠位由 workline/device 域维护；`resource` 域只引用 `workline_id` / `workline_code` / `position_code`。

workline 域新增配置表：

- `workline_rack_positions`：工作线可停靠货架位置配置。

建议字段：

- `workline_id`
- `workline_code`
- `position_code`
- `position_name`
- `position_role`
- `allowed_rack_kind`
- `capacity`
- `logic_location_code`
- `external_location_code`
- `device_role`
- `priority`
- `enabled`
- `metadata_json`

约束：

- `workline_code + position_code` 唯一。
- `capacity` 第一阶段固定为 1。
- `allowed_rack_kind` 必须与到达货架类型匹配。

SMT 分拣机示例配置：

| `workline_code` | `position_code` | `position_role` | `allowed_rack_kind` | `logic_location_code` |
| --- | --- | --- | --- | --- |
| `SMT_SORTER_01` | `FIVE_LAYER_STORAGE` | `SOURCE_STORAGE` | `FIVE_LAYER` | `SMT_SORTER_01_FIVE_LAYER` |
| `SMT_SORTER_01` | `SINGLE_LAYER_A` | `OUTPUT_BUFFER` | `SINGLE_LAYER` | `SMT_SORTER_01_SINGLE_A` |
| `SMT_SORTER_01` | `SINGLE_LAYER_B` | `OUTPUT_BUFFER` | `SINGLE_LAYER` | `SMT_SORTER_01_SINGLE_B` |

### 2. 事实事件

保留并强化 `resource_state_events` 作为唯一事实账本。

建议字段边界：

- `event_code`：全局唯一，必须以单条资源事实为粒度，建议使用 `{event_type}:{source_system}:{source_event_id}:{resource_code}`，必要时追加 `fact_seq` 或稳定摘要。
- `idempotency_key`：显式幂等键，必须区分同一外部事件派生的多条事实，避免只依赖外部 event id。
- `event_type`：`RACK_ARRIVED`、`RACK_DEPARTED`、`BIN_MOUNTED`、`BIN_UNMOUNTED`、`MATERIAL_MOUNTED`、`MATERIAL_UNMOUNTED`、`RESOURCE_RECONCILED`。
- `resource_type` / `resource_code`：事件主对象。
- `source_system`：统一来源系统枚举。
- `source_event_id` / `source_version`：只作为外部事件关联字段，不作为 `ResourceStateEvent` 全局唯一键。
- `trace_id` / `session_id` / `workline_id` / `workline_code`。
- `position_code` / `logic_location_code` / `external_location_code`。
- `occurred_at` / `received_at`。
- `payload_json`：事实摘要和原始关键字段，不作为主查询依赖。

破坏性调整：

- 合并 `ResourceRelationSourceSystem` 到 `ResourceSourceSystem`。
- 所有投影服务统一使用 `ResourceSourceSystem`。

### 3. 当前投影

当前投影只表达“现在是什么状态”，并保留历史行。active 行以 `ended_at IS NULL` 判定。

保留：

- `resource_rack_placements`：货架当前占用哪个工作线货架停靠位。
- `resource_rack_bin_mounts`：货架 A/B/C/D 槽位当前挂哪个料箱。

删除：

- `resource_rack_material_mounts`：该模型表达“物料直接占用货架槽位”，不适合 SMT 料箱内部格位。

新增：

- `resource_bin_material_mounts`：物料当前占用哪个料箱内部料格。

`resource_bin_material_mounts` 建议字段：

- `bin_code`
- `bin_cell_code`
- `bin_cell_index`
- `material_identity_key`
- `pkg_code`
- `material_code`
- `lot_code`
- `date_code`
- `qty_snapshot`
- `reel_diameter`
- `reel_thickness`
- `wms_inventory_id`
- `wms_inventory_version`
- `wms_confirmation_status`
- `wms_writeback_evidence_id`
- `mount_status`
- `source_system`
- `source_event_id`
- `source_version`
- `trace_id`
- `session_id`
- `started_at`
- `ended_at`

唯一约束：

- active `bin_code + bin_cell_index` 唯一。
- active `material_identity_key` 唯一。

`resource_rack_placements` 调整字段：

- `rack_code`
- `rack_kind`
- `workline_id`
- `workline_code`
- `position_code`
- `position_role`
- `logic_location_code`
- `external_location_code`
- `placement_status`
- `source_system`
- `source_task_id`
- `source_event_id`
- `source_version`
- `trace_id`
- `session_id`
- `started_at`
- `ended_at`

`logic_location_code` 是 WES 对 ECS/控制系统下发的逻辑位置。`external_location_code` 是 WMS/RCS 回调中的地码或位置引用，只作为证据保存。

`resource_rack_placements` 唯一约束：

- active `rack_code` 唯一。
- active `workline_code + position_code` 唯一。

冲突定义：

- 同一货架已 active 在其他 `workline_code + position_code`。
- 同一 `workline_code + position_code` 已被其他 active 货架占用。
- 到达货架类型与 `workline_rack_positions.allowed_rack_kind` 不匹配。

### 4. 历史证据和快照

保留：

- `resource_bin_content_snapshots`
- `resource_bin_content_snapshot_items`

边界调整：

- 它们表示某时刻看到的料箱内容。
- 不作为 active 状态来源。
- WMS/RCS 空架到达、OUTPUT_ARM 放入成功、满箱释放前都可以写快照。
- `BinContentSnapshotItem.bin_slot_code` 改名为 `bin_cell_code`，并补齐 `bin_cell_index`。
- `BinContentSnapshot` 增加 `snapshot_reason`，例如 `EMPTY_RACK_ARRIVED`、`MATERIAL_MOUNTED`、`RACK_RELEASED`、`RECONCILIATION_CHECK`。
- `BinContentSnapshot` 增加 `snapshot_group_key`，用于把同一满架释放、同一外部回调或同一次对账产生的多个料箱快照关联起来。
- `BinContentSnapshot` 保留 `source_session_id` / `source_event_id`，用于回到 workline 过程证据主线。

不再新增或保留独立流程证据表。流程编排和集成证据由 workline / integration 域承载：

- `resource_rack_releases` 删除。满架释放是 workline 内部事件 `SINGLE_LAYER_RACK_RELEASED`，由 `workline_inbox`、timeline 和 session context 追溯。
- `resource_rack_release_bin_snapshots` 删除。释放瞬间 4 个料箱内容使用 `BinContentSnapshot + BinContentSnapshotItem`，并设置 `snapshot_reason=RACK_RELEASED`、`snapshot_group_key=rack_release_id`。
- `resource_full_box_exchange_tasks` 改为 `workline_full_box_exchange_tasks` 或独立 `smt_full_box_exchange_tasks`，因为它表达外部请求、等待、回调和 Session 生命周期。
- `resource_wms_writeback_evidence` 改为 integration/wms 域的通用 WMS 交互证据；resource 域只通过 `writeback_evidence_id` 或 `wms_confirmation_status` 引用确认结果。

### 5. 资源域归属裁剪

第一阶段破坏性优化后，`resource` 域只保留三类模型：

1. **资源对象**：货架、料箱、槽位模板。
2. **资源事实与 active 投影**：资源事实事件、货架停靠位占用、货架槽位挂箱、料箱格位占料。
3. **资源证据快照**：料箱内容快照。

明确迁出或删除：

| 当前模型 / 表 | 新归属 | 决策 |
| --- | --- | --- |
| `ExecutionZone` / `resource_execution_zones` | workline/device 域 | 删除 resource 表，由工作线/设备拓扑配置承载区域和并发能力。 |
| `ExecutionLocation` / `resource_execution_locations` | workline/device 域或外部位置字典 | 删除 resource active 语义；如需保留外部位置映射，只作为 workline/device 配置。 |
| `resource_workline_rack_positions` | workline 域 | 改名为 `workline_rack_positions`，不使用 `resource_` 前缀。 |
| `RackRelease` / `resource_rack_releases` | workline 事件 | 删除独立表，由 `SINGLE_LAYER_RACK_RELEASED` inbox、timeline 和 session context 表达释放过程。 |
| `RackReleaseBinSnapshot` / `resource_rack_release_bin_snapshots` | resource 通用快照 | 合并到 `BinContentSnapshot + BinContentSnapshotItem`。 |
| `FullBoxExchangeTask` / `resource_full_box_exchange_tasks` | workline 或 `smt_exchange` 域 | 改名为 `workline_full_box_exchange_tasks` 或 `smt_full_box_exchange_tasks`，resource 只保留释放快照和投影。 |
| `WmsWritebackEvidence` / `resource_wms_writeback_evidence` | integration/wms 域 | 改为通用 WMS 交互证据，resource 只引用确认结果。 |
| `RackMaterialMount` / `resource_rack_material_mounts` | 删除 | SMT 物料占用改由 `resource_bin_material_mounts` 表达。 |

字段级裁剪：

| 模型 | 字段 | 决策 |
| --- | --- | --- |
| `Rack` | `current_location_code` | 删除，当前位置由 `resource_rack_placements` 投影表达。 |
| `Rack` | `status` | 收窄为主数据启停或可用性，不表达当前在途、到位、交换中等运行状态。 |
| `Rack` | `last_seen_at` | 移入事实事件或投影，不放在主数据实例。 |
| `Bin` | `status` | 收窄为主数据启停或可用性，不表达当前挂载、满箱、空箱验证等运行状态。 |
| `Bin` | `last_seen_at` | 移入事实事件或快照，不放在主数据实例。 |

枚举裁剪：

- `ResourceType` 只保留 `RACK`、`BIN`、`MATERIAL`。
- `WORKLINE`、`DEVICE`、`LOCATION`、`EXCHANGE_TASK` 改为事件上下文字段，不作为资源主对象类型。

## 关键流程

### WMS/RCS 有效空架到达

触发点：`WMS_RACK_ARRIVED` 回调，且 `active_bin_rack` 通过 4 料箱和全空校验。

工作线停靠位来源：

- 优先使用当前 Session `rack_supply.target_position_code`。
- 如果回调显式携带 `workline_code` / `position_code`，必须与当前等待 Session 一致。
- WMS/RCS 回调中的地码只写入 `external_location_code` 或 evidence，不作为 active placement 的主键。

同一事务内处理：

1. 写 `ResourceStateEvent(RACK_ARRIVED)`。
2. 通过 workline 域读取 `workline_rack_positions`，校验该工作线停靠位存在、启用且允许 `SINGLE_LAYER`。
3. 写或更新 `RackPlacement(workline_code, position_code)`。
4. 写 `ResourceStateEvent(BIN_MOUNTED)`。
5. 写或更新 4 条 `RackBinMount`。
6. 写 4 个空料箱内容快照。
7. 写 session context：`active_bin_rack`、`rack_supply.status=ARRIVED`、`rack_supply.position_code`。
8. 调用料格分配算法。
9. 下发 OUTPUT_ARM 命令。

冲突处理：

- 货架已有不同 active workline position，工作线位置已被其他货架占用，或料箱已有不同 active mount 时，事实事件可以追加。
- active 投影不覆盖。
- 创建 RuntimeHold，进入 RECONCILING。
- 不继续分配料格。

### 料格分配成功

触发点：算法选出 `bin_location`。

只写 session context，不写 active resource 状态：

- `bin_location`
- `pkg_id`
- 调度决策摘要

原因：分配是计划，不是物理事实。

### OUTPUT_ARM 放入成功

触发点：OUTPUT_ARM `PICK_AND_PUT SUCCESS`。

同一事务内处理：

1. 从 session context 读取 `pkg_id`、`six_in_one`、`bin_location`。
2. 解析或生成 `material_identity_key`。
3. 写 `ResourceStateEvent(MATERIAL_MOUNTED)`。
4. 写 `BinMaterialMount`。
5. 写新的 `BinContentSnapshot + Item`。
6. 更新 session context：`material_mounted=true`、`material_mount_event_id`。
7. 完成当前 workline session，或进入后续 WMS 回写等待。

冲突处理：

- active `bin_code + bin_cell_index` 已占用时，事实事件可以追加。
- active 投影不覆盖。
- 创建 RuntimeHold，进入 RECONCILING。
- session 不直接 complete。

### OUTPUT_ARM 放入失败

不写 `MATERIAL_MOUNTED`，不写 `BinMaterialMount`。

处理方式：

- 可重试时保留 `bin_location`。
- 不可重试时 block material 或 device。
- 人工处理后再决定重新分配或确认落库。

### WMS 回写确认

触发点：WES 通知 WMS 后收到确认，或 WMS 后续回调。

处理方式：

- 写 integration/wms 域的 WMS 回写证据。
- 更新 `BinMaterialMount.wms_confirmation_status`。
- 可选更新 `wms_inventory_version`。

WMS 确认不创建物理占用事实；物理事实由 OUTPUT_ARM 成功产生。

## 服务边界

### 模型层

文件：`src/app/resource/models/resource.py`

短期保留单文件，但按概念分段排序：

1. 枚举和通用引用。
2. 主数据。
3. 事实和投影。
4. 快照。

workline 侧新增模型放在 workline 域，不放入 resource 模型文件：

- `src/app/workline/models/rack_position.py`：`WorklineRackPosition`
- `src/app/workline/repositories/rack_position_repository.py`
- `src/app/workline/services/rack_position_service.py`

### 仓储层

文件：`src/app/resource/repositories/resource_repository.py`

新增：

- `BinMaterialMountRepository`
- `RackPlacementRepository.get_active_by_workline_position(db, workline_code, position_code)`
- `get_active_by_bin_cell(db, bin_code, bin_cell_index)`
- `get_active_by_material_identity(db, material_identity_key)`

删除：

- `RackMaterialMountRepository`

### 投影服务

文件：`src/app/resource/services/projection_service.py`

职责：统一“事实事件 + active 投影”。

方法：

- `record_rack_arrived`
- `record_empty_rack_mounted`
- `record_material_mounted_to_bin_cell`
- `record_material_unmounted_from_bin_cell`
- `record_full_box_exchange_physical_completed`

`record_rack_arrived` 必须先通过 workline 域校验 `workline_code + position_code` 配置，再写 active placement。它不得用 WMS/RCS 地码直接决定 WES 当前货架位置。

### 快照服务

文件：`src/app/resource/services/snapshot_service.py`

职责：只处理快照证据。

方法：

- `record_empty_bin_snapshots_from_arrived_rack`
- `record_bin_content_snapshot_after_material_mount`
- `record_bin_content_snapshots_for_reason`

### 主数据服务

文件：`src/app/resource/services/resource_service.py`

保留：

- 主数据 service。

不再继续塞投影逻辑。

迁出：

- `FullBoxExchangeTaskService` 迁到 workline 或 `smt_exchange` 域。
- `WmsWritebackEvidenceService` 迁到 integration/wms 域。

### Runtime 集成

插件不直接访问数据库。资源落库由 runtime/service 层统一处理。

建议新增 runtime intent：

- `RuntimeIntentKind.RESOURCE_FACT`

插件只产生资源事实 intent，runtime 负责在事务内执行投影、快照、session 更新、冲突 hold。

## SMT 插件职责

文件：`src/workline_plugins/smt_classifier/plugin.py`

插件负责判断何时产生资源事实 intent：

- WMS/RCS `WMS_RACK_ARRIVED` 校验通过后，产生 `RACK_ARRIVED` 和 `BIN_MOUNTED` 资源事实 intent。
- OUTPUT_ARM `PICK_AND_PUT SUCCESS` 后，产生 `MATERIAL_MOUNTED` 资源事实 intent。
- 料格分配成功时不产生资源事实 intent。

SMT 插件不直接判断地码占用。它只根据当前 workline runtime context 和 `rack_supply.target_position_code` 指定需要补给的工作线停靠位。三停靠位分拣机中，插件只把 `SINGLE_LAYER_A` / `SINGLE_LAYER_B` 作为 `active_bin_rack` 候选；`FIVE_LAYER_STORAGE` 是五层货架接驳/来源位，不参与单层空架装箱分配。

## 幂等键

建议统一规则：

- 到架：`WMS_RACK_ARRIVED:{dispatch_key}:{workline_code}:{position_code}:{source_event_id}`
- 料箱挂载：`BIN_MOUNTED:{rack_code}:{snapshot_hash}:{source_event_id}`
- 物料放入：`MATERIAL_MOUNTED:{source_event_id}:{pkg_id}:{bin_code}:{bin_cell_index}`
- 快照：`BIN_SNAPSHOT:{bin_code}:{source_event_id}:{snapshot_hash}`

## 测试策略

新增或调整：

- `tests/workline_runtime/test_workline_rack_position_service.py`
  - 覆盖三停靠位配置、货架类型约束、启停状态和 `workline_code + position_code` 唯一性。
- `tests/resource/test_resource_projection_service.py`
  - 覆盖工作线停靠位校验、到架投影、料箱挂载、物料占格、冲突和幂等。
- `tests/resource/test_resource_snapshot_service.py`
  - 覆盖空架快照、物料放入后快照、快照 hash。
- `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
  - 只断言插件产生正确 resource intent。
- runtime 测试
  - 覆盖 resource intent 被执行。
  - 覆盖冲突时创建 RuntimeHold 并阻断流程。

## 迁移策略

因为系统未发布，采用破坏性迁移：

- 删除 `resource_rack_material_mounts`。
- 删除 `resource_rack_releases`。
- 删除 `resource_rack_release_bin_snapshots`。
- 创建 `resource_bin_material_mounts`。
- 创建 `workline_rack_positions`，归 workline 域维护。
- 将 `resource_rack_placements` 从 `location_code` 主语义改为 `workline_code + position_code`。
- 调整 `resource_bin_content_snapshots` 字段，增加 `snapshot_reason`、`snapshot_group_key`。
- 调整 `resource_bin_content_snapshot_items` 字段，`bin_slot_code` 改为 `bin_cell_code`，并补齐 `bin_cell_index`。
- 合并来源系统枚举。
- 删除 `resource_execution_zones` 和 `resource_execution_locations`，由 workline/device 配置承载区域、工作线、设备和外部逻辑位置。
- 将 `resource_full_box_exchange_tasks` 迁出为 workline 或 `smt_exchange` 域表。
- 将 `resource_wms_writeback_evidence` 迁出为 integration/wms 域表。
- 从 `resource_racks` 删除 `current_location_code`，并收窄 `status` / `last_seen_at` 语义。
- 从 `resource_bins` 收窄 `status` / `last_seen_at` 语义。
- 将 `ResourceType` 裁剪为 `RACK`、`BIN`、`MATERIAL`。
- 不写旧数据兼容迁移。
- Alembic revision 必须由 Alembic 生成器生成，再编辑生成文件。

## 验收标准

- WMS/RCS 有效空架到达后，资源事实、工作线货架停靠位投影、4 个料箱挂载、空箱快照都可查询。
- SMT 三停靠位配置可表达 1 个五层货架位和 2 个单层货架位，且 active 占用约束生效。
- 料格分配成功但 OUTPUT_ARM 未成功前，不出现物料 active 占用。
- OUTPUT_ARM 成功后，`resource_bin_material_mounts` 出现唯一 active 占用。
- 同一料格重复占用或同一物料重复占用会进入 RECONCILING，不覆盖现有投影。
- 同一工作线停靠位重复到架、货架类型不匹配或同一货架跨位置冲突会进入 RECONCILING，不覆盖现有投影。
- 满架释放过程由 `workline_inbox`、`workline_outbox`、`workline_timelines`、`workline_sessions.context_json` 和 `runtime_holds` 追溯；resource 只保存 `snapshot_reason=RACK_RELEASED` 的通用料箱内容快照。
- 满箱交换任务状态迁出 workline 或 `smt_exchange` 域，当前资源状态从 resource 投影表读取。
- `resource_execution_zones`、`resource_execution_locations`、`resource_rack_releases`、`resource_rack_release_bin_snapshots`、`resource_full_box_exchange_tasks`、`resource_wms_writeback_evidence` 不再作为 resource 域目标表。
- 插件无数据库访问，资源落库由 runtime/service 层处理。
