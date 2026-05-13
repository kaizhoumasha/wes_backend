<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260513-212526.md -->

# SMT 执行资源模型设计

## 背景

`docs/architecture/SRS.md` 同时提出两类约束：

- WES 需要完成“物理-数字”映射，跟踪料箱、货架、地码、任务和设备执行状态。
- WES 采用纯代理模式，不维护库存主数据；库存数量、库存状态、预留、扣减、账务和 SAP 同步均由现有 WMS 负责。

因此，SMT 资源模型不能被设计成第二套库存系统。它的目标是让 WES 能可靠回答执行问题：

- 这个物理货架是什么结构？
- 当前这个料箱挂在哪个货架槽位？
- 这次粗分机释放事件中的 4 个料箱快照是什么？
- WES 是基于哪一版现场事实发起满箱交换？
- WES 是基于哪一个 WorkLine、Device、Material、Rack、Bin 运行时资源事实做出决策？
- WMS/RCS 回调后，WES 如何追踪执行闭环与异常对账？

库存问题仍交给 WMS 回答：

- 某物料当前可用库存是多少？
- 某批次是否冻结、预留、可发？
- 某次入库、交换、发料是否已经完成账务确认？

## 设计目标

- 为单层货架、五层货架、生产/退货货架、料箱、槽位、地码建立统一执行资源模型。
- 为 WorkLine、Device、Rack、Bin、Material 等 WES 运行时资源提供统一事实引用和状态证据；WorkLine/Device 复用现有领域模型，不在资源域重复建主数据。
- 支持粗分机装箱、单层货架释放、满箱交换、零散拣选、发料和退料过程复用。
- 持久化过程快照和物理关系历史，支撑重复事件、迟到回调、人工对账和追溯。
- 保留入库、上架、生产发料、生产退料的统一物料流转事实口径，支撑后续工件、产能、效能、异常和资源利用率分析；完整统计投影后置。
- 不持久化 WMS 库存主账，不把 `qty_snapshot` 当成可用库存。
- 持久化 WMS 回写请求、响应、确认和失败证据，用于审计、重试、对账和问题复盘。
- 与现有 WorkLine、Device、Inbox、Outbox、Session、Timeline 运行时模型解耦但可关联。
- 保持 API -> Service -> Repository -> Database 分层，不让插件或 API 直接写资源状态。

## 非目标

- 不替代 WMS 库存、账务、预留、批次主数据。
- 不在第一版实现 WES 自主分配五层货架空箱资源；满箱交换资源仍由 WMS/RCS 判断并回调。
- 不直接建 AGV/CTU 调度模型；AGV/CTU 是 WMS/RCS 调度域。
- 不把设备坐标、RCS 路径、机械臂关节参数建模为 WES 业务资源。
- 不为每个插件建立私有资源表。
- 不在执行插件里累计 KPI，不让统计聚合反向影响现场执行事务。
- 不在第一版实现完整 `MaterialFlowRun/Event`、BI 看板、OEE 报表或经营分析页面；本设计只保证事件命名、扩展点和聚合边界。
- 不在资源域重复维护 WorkLine/Device 主数据；资源域只引用现有 WorkLine/Device 身份并记录运行时证据。

## 数据权威边界

| 数据类别                  | WES 存储        | 权威来源                     | 用途                              |
| --------------------- | ------------- | ------------------------ | ------------------------------- |
| 区域、地码、坐标引用            | 可存执行副本        | WMS/RCS 或上线初始化配置         | WorkLine 路由、搬运请求起终点、现场查询。       |
| 货架类型、槽位结构             | 可存执行主数据       | 上线初始化配置，必要时由 WMS 同步      | 判断单层 4 箱、五层 20 箱、A/B 面等物理结构。    |
| 货架实例、料箱实例             | 可存执行主数据       | WMS 资产主数据或上线导入           | 识别物理载体，绑定 WorkLine 与现场扫描。       |
| 货架当前地码                | 存最后已确认状态      | WMS/RCS 回调，现场扫描可补证       | 判断执行位置和异常恢复，不作为库存位置权威。          |
| 料箱挂载槽位                | 存当前关系与历史      | ECS 扫描、WMS/RCS 交换结果、人工对账 | 判断 4 箱释放、交换前后物理关系。              |
| 物料占用卡槽                | 存当前关系与历史      | ECS 扫描、WMS/RCS 回调、人工对账    | 追踪退料/运转货架上的物料位置，不作为库存位置权威。     |
| 料箱内容摘要                | 存过程快照         | WES 执行过程 + WMS 查询快照      | 分箱、满箱评估、追溯。                     |
| 物料库存数量/状态             | 不存主账，只存快照引用   | WMS                      | 决策证据与审计；实际可用库存必须实时查 WMS。        |
| 库存预留/扣减/确认            | 只存请求/响应引用     | WMS                      | 对账和重试，不作为本地库存账。                 |
| 满箱交换任务状态              | 存 WES 过程状态    | WES Session + WMS/RCS 回调 | 运行时闭环、阻断、对账、查询。                 |
| WorkLine/Device 运行时引用 | 存引用和事件证据      | 现有 WorkLine/Device 模块    | 资源事实关联、追溯、统计维度，不重复建主数据。         |
| WMS 回写证据              | 存脱敏请求、响应、确认引用 | WES Outbox + WMS 回调      | 证明 WES 是否已提交、WMS 是否已确认、失败是否可重放。 |

核心原则：

> WES 可以拥有“执行事实”，不能拥有“库存事实”。

资源事实分层：

- `ResourceStateEvent` 是 append-only 事实账本，是可追溯证据源。
- `RackPlacement`、`RackBinMount` 等当前关系表是投影，用于查询和执行判断，不是独立真相源。
- Runtime Session 拥有等待、重试、超时、人工阻断和闭环状态；资源任务表只记录业务过程和 WMS/RCS 证据。

## 总体模型

```text
WorkLine / Device / Material
  └─ ResourceRef(existing-domain identity)

ExecutionZone
  └─ ExecutionLocation
       └─ RackPlacement(active/current projection + history)

RackType
  └─ RackSlotTemplate
       └─ Rack
            ├─ RackBinMount(active/current projection + history)
            │    └─ Bin
            │         └─ BinSlotTemplate / BinContentSnapshot
            └─ RackMaterialMount(active/current projection + history)
                 └─ MaterialRef / WMS inventory refs

RackRelease
  └─ RackReleaseBinSnapshot
       └─ BinContentSnapshotRef

FullBoxExchangeTask
  ├─ RackRelease
  ├─ WorklineSession
  ├─ WorklineOutbox(EXTERNAL_HTTP)
  └─ WMS/RCS callback + writeback evidence refs

ResourceStateEvent
  └─ append-only resource fact ledger

WmsWritebackEvidence
  └─ request/response/confirmation audit refs

MaterialFlowRun
  └─ MaterialFlowEvent
       └─ FlowStageMetric / FlowAnalyticsProjection
```

## 模型分组

### 1. 执行区域与地码

`ExecutionZone` 表示 WES 可识别的作业区域，例如装箱区、SMT 存储区、满箱交换区、退料区。

建议字段：

| 字段                     | 说明                                                                  |
| ---------------------- | ------------------------------------------------------------------- |
| `zone_code`            | WES 区域编码，业务唯一。                                                      |
| `zone_name`            | 区域名称。                                                               |
| `zone_type`            | `KITTING`、`SMT_STORAGE`、`FULL_BOX_EXCHANGE`、`RETURN`、`LINE_BUFFER`。 |
| `wms_zone_code`        | WMS 区域引用。                                                           |
| `status`               | `ACTIVE`、`DISABLED`。                                                |
| `allowed_rack_types`   | 允许进入的货架类型。                                                          |
| `max_concurrent_tasks` | 并发任务上限，来自 WMS/RCS 配置或现场策略。                                          |
| `metadata_json`        | 坐标、说明、现场扩展属性。                                                       |

`ExecutionLocation` 表示地码、缓存位、工作站位置或交换区排队位。

建议字段：

| 字段                   | 说明                                                              |
| -------------------- | --------------------------------------------------------------- |
| `location_code`      | WES 地码编码，业务唯一。                                                  |
| `zone_code`          | 所属区域。                                                           |
| `location_type`      | `WORK_STATION`、`BUFFER`、`STORAGE`、`EXCHANGE_SLOT`、`QUEUE_SLOT`。 |
| `wms_location_code`  | WMS/RCS 地码引用。                                                   |
| `rack_capacity`      | 可容纳货架数量。                                                        |
| `allowed_rack_types` | 允许货架类型。                                                         |
| `status`             | `AVAILABLE`、`OCCUPIED`、`LOCKED`、`DISABLED`、`UNKNOWN`。           |
| `coordinates_json`   | RCS 坐标透传，不由 WES 解释。                                             |

设计说明：

- `coordinates_json` 只用于透传和排障；WES 不解析具体坐标。
- `current_rack_count` 不建议做成主字段，可由 active `RackPlacement` 投影得到。
- 满箱交换区空位 v1 由 WMS/RCS 判断；WES 本地 `ExecutionLocation` 只用于 trace 和现场核对。

### 2. 货架类型与货架实例

`RackType` 定义物理结构。

建议类型：

| 类型             | 槽位          | 承载对象          | A/B 面 | 用途                |
| -------------- | ----------- | ------------- | ----- | ----------------- |
| `SINGLE_LAYER` | 4 个料箱位      | 料箱            | 无     | 粗分机出料、入库中转、发料准备。  |
| `FIVE_LAYER`   | 20 个料箱位     | 料箱            | 有     | SMT 自动化存储、CTU 交换。 |
| `RETURN`       | 卡槽式物料位      | 物料/PKG/料盘引用   | 有     | 退料暂存与退库。          |
| `TRANSFER`     | 卡槽式物料位      | 物料/PKG/料盘引用   | 有     | 运转、周转、线边暂存。       |
| `PRODUCTION`   | 按槽位模板声明      | 由 `slot_kind` 决定 | 有     | 产线在用料盘。           |

`RackSlotTemplate` 定义某类货架的标准槽位。

建议字段：

| 字段                  | 说明                             |
| ------------------- | ------------------------------ |
| `rack_type_code`    | 所属货架类型。                        |
| `slot_code`         | 槽位编码，如单层 `S1-S4`，五层 `A-L1-S1`。 |
| `side`              | `A`、`B` 或 `NONE`。              |
| `layer_no`          | 层号，单层可为 `1`。                   |
| `position_no`       | 同层序号。                          |
| `slot_kind`         | `BIN_SLOT`、`MATERIAL_SLOT`。    |
| `allowed_bin_types` | 允许的料箱类型，仅 `BIN_SLOT` 使用。        |
| `allowed_material_carrier_types` | 允许的物料承载形态，如 `PKG`、`REEL`、`TRAY`，仅 `MATERIAL_SLOT` 使用。 |
| `active`            | 是否启用。                          |

槽位承载规则：

- 单层货架和五层货架的货位均为 `BIN_SLOT`，货架槽位承载料箱，物料位于料箱内部。
- 退料货架和运转货架为卡槽式结构，货位为 `MATERIAL_SLOT`，货架槽位直接承载物料/PKG/料盘引用，不经过料箱。
- 生产货架不在 `RackType` 层推断承载对象，必须由每个 `RackSlotTemplate.slot_kind` 显式声明；同一货架可以按现场模板拥有不同槽位类型，但单个槽位不能混用。
- 同一个 `rack_slot_code` 只能选择一种承载模型：`RackBinMount` 或 `RackMaterialMount`，不能同时存在 active 关系。
- `MATERIAL_SLOT` 中的物料数量、冻结、预留和账务状态仍以 WMS 为准；WES 只保存执行位置、快照和 WMS 引用。

`Rack` 表示物理货架实例。

建议字段：

| 字段                      | 说明                                                                                              |
| ----------------------- | ----------------------------------------------------------------------------------------------- |
| `rack_code`             | WES 货架编码，业务唯一。                                                                                  |
| `wms_rack_id`           | WMS 货架 ID。                                                                                      |
| `rack_type_code`        | 货架类型。                                                                                           |
| `status`                | `AVAILABLE`、`LOCKED`、`IN_TRANSIT`、`AT_WORKLINE`、`IN_EXCHANGE`、`EXCEPTION`、`DISABLED`、`UNKNOWN`。 |
| `current_location_code` | 最后确认地码，可由 active placement 投影。                                                                  |
| `last_seen_at`          | 最近一次被 WES 现场确认时间。                                                                               |
| `source_system`         | `WMS`、`RCS`、`ECS`、`MANUAL_IMPORT`。                                                              |
| `source_version`        | 来源版本或更新时间。                                                                                      |

设计说明：

- `Rack.status` 是执行可用性状态，不是库存状态。
- 货架结构由 `RackType + RackSlotTemplate` 决定，不在每个 `Rack` 上重复写 4/20 个槽位。
- 若 WMS 是货架资产主数据源，WES 仍可持久化执行副本，但必须记录 `wms_rack_id` 和同步版本。

### 3. 料箱类型与料箱实例

`BinType` 定义料箱内部结构。

建议类型：

| 类型        | 结构            | 用途            |
| --------- | ------------- | ------------- |
| `TYPE_A`  | 多个 7 寸料盘储位    | 7 寸料盘优先。      |
| `TYPE_B`  | 7 寸储位 + 大尺寸储位 | 13/15 寸或混合料盘。 |
| `UNKNOWN` | 未识别结构         | 现场异常或历史数据兼容。  |

`BinSlotTemplate` 定义料箱内部储位。

建议字段：

| 字段              | 说明                                 |
| --------------- | ---------------------------------- |
| `bin_type_code` | 所属料箱类型。                            |
| `bin_slot_code` | 料箱内槽位编码。                           |
| `slot_size`     | `7INCH`、`13INCH`、`15INCH`、`LARGE`。 |
| `max_depth_mm`  | 最大深度。                              |
| `max_weight_g`  | 可选最大重量。                            |
| `active`        | 是否启用。                              |

`Bin` 表示物理料箱实例。

建议字段：

| 字段               | 说明                                                                                   |
| ---------------- | ------------------------------------------------------------------------------------ |
| `bin_code`       | WES 料箱编码，业务唯一。                                                                       |
| `wms_bin_id`     | WMS 料箱 ID。                                                                           |
| `bin_type_code`  | 料箱类型。                                                                                |
| `status`         | `EMPTY_VERIFIED`、`IN_USE`、`LOCKED`、`FULL_SNAPSHOT`、`EXCEPTION`、`DISABLED`、`UNKNOWN`。 |
| `last_seen_at`   | 最近一次现场确认时间。                                                                          |
| `source_system`  | `WMS`、`ECS`、`RCS`、`MANUAL_IMPORT`。                                                   |
| `source_version` | 来源版本或更新时间。                                                                           |

设计说明：

- `Bin.status=FULL_SNAPSHOT` 只表示 WES 最近一次过程快照判断为满，不表示 WMS 库存状态。
- 是否可用、是否冻结、是否被预留，必须查询 WMS。

### 4. 当前物理关系与历史

`RackPlacement` 记录货架处于哪个执行地码。

建议字段：

| 字段                 | 说明                                           |
| ------------------ | -------------------------------------------- |
| `rack_code`        | 货架。                                          |
| `location_code`    | 地码。                                          |
| `placement_status` | `ARRIVED`、`IN_TRANSIT`、`DEPARTED`、`UNKNOWN`。 |
| `source_task_id`   | WMS/RCS 搬运任务 ID。                             |
| `source_event_id`  | WMS/RCS/ECS 事件 ID。                           |
| `started_at`       | 进入该关系的时间。                                    |
| `ended_at`         | 离开该关系的时间，active 记录为空。                        |

约束：

- 同一 `rack_code` 只能有一条 active placement。
- 同一 `location_code` 的 active placement 数量不得超过 `rack_capacity`。
- 发生冲突时不覆盖旧记录，进入资源对账。

`RackBinMount` 记录料箱挂载在哪个货架槽位。

建议字段：

| 字段                | 说明                                            |
| ----------------- | --------------------------------------------- |
| `rack_code`       | 货架。                                           |
| `rack_slot_code`  | 货架槽位。                                         |
| `bin_code`        | 料箱。                                           |
| `mount_status`    | `MOUNTED`、`UNMOUNTED`、`EXCHANGING`、`UNKNOWN`。 |
| `source_system`   | `ECS`、`WMS_RCS`、`MANUAL_RECONCILIATION`。      |
| `source_event_id` | 来源事件 ID。                                      |
| `started_at`      | 挂载确认时间。                                       |
| `ended_at`        | 解除挂载时间，active 记录为空。                           |

约束：

- 同一 `rack_code + rack_slot_code` 只能有一条 active mount。
- 同一 `bin_code` 只能有一条 active mount。
- mount 必须符合 `RackSlotTemplate.allowed_bin_types`，且目标槽位必须是 `BIN_SLOT`。

`RackMaterialMount` 记录物料/PKG/料盘直接占用哪个卡槽式货架槽位。

建议字段：

| 字段 | 说明 |
| --- | --- |
| `rack_code` | 货架。 |
| `rack_slot_code` | 卡槽货位。 |
| `material_identity_key` | WES 过程物料身份幂等键，优先来自 WMS 库存/分拆记录，其次由受控规则生成。 |
| `pkg_code` | 六合一码或现场物料身份展示字段。 |
| `material_code` | 物料编码引用。 |
| `lot_code` | 批次展示字段。 |
| `vendor_code` | 供应商引用。 |
| `qty_snapshot` | 当时执行过程看到的数量，不作为可用库存。 |
| `wms_inventory_id` | WMS 库存记录引用。 |
| `wms_inventory_version` | WMS 库存或分拆版本引用。 |
| `wms_split_policy` | `NOT_SPLITTABLE`、`SPLITTABLE`、`UNKNOWN`，来自 WMS 合同。 |
| `wms_confirmation_status` | `PENDING`、`CONFIRMED`、`REJECTED`、`NOT_REQUIRED`。 |
| `writeback_evidence_id` | 关联 WMS 回写证据。 |
| `mount_status` | `OCCUPIED`、`REMOVED`、`LOCKED`、`UNKNOWN`。 |
| `source_system` | `ECS`、`WMS_RCS`、`WES_RUNTIME`、`MANUAL_RECONCILIATION`。 |
| `source_event_id` | 来源事件 ID。 |
| `started_at` | 占用确认时间。 |
| `ended_at` | 离开卡槽时间，active 记录为空。 |

约束：

- 同一 `rack_code + rack_slot_code` 只能有一条 active material mount。
- `RackMaterialMount` 只能用于 `RackSlotTemplate.slot_kind=MATERIAL_SLOT`。
- 同一 `rack_code + rack_slot_code` 不得同时存在 active `RackBinMount` 和 active `RackMaterialMount`。
- `material_identity_key` 必须稳定且不可为空；缺失、模糊或多候选时进入 RuntimeHold/资源对账，不创建可信 active 投影。
- 同一 active `material_identity_key` 是否允许跨多个卡槽出现，必须由 WMS `wms_split_policy` 决定；`UNKNOWN` 时进入对账而不是自动合并。
- `qty_snapshot`、`pkg_code`、`material_code`、`lot_code` 和 `vendor_code` 只能用于展示、追溯和对账，不能作为库存可用性或拆分合法性的本地判断依据。

设计说明：

- 这张表是满箱交换、释放快照和人工对账的核心。
- `RackBinMount` 是单层/五层货架的核心；`RackMaterialMount` 是退料/运转卡槽货架的核心。
- `RackPlacement`、`RackBinMount` 和 `RackMaterialMount` 都是当前关系投影，必须由 `ResourceStateEvent` 驱动或引用 `source_event_id`，不能绕过资源服务直接覆盖。
- 物理关系变更统一走 `ResourceRelationService`：外部系统交互必须先完成并形成 evidence，随后在短事务内写 append-only 事实并更新当前投影，不得持有数据库锁等待 WMS/RCS HTTP。
- 并发冲突、容量冲突、迟到事件不得依赖数据库唯一约束自然失败；服务层应显式转入 `RECONCILING` 并创建 RuntimeHold 或资源对账项。
- CTU 交换完成后，WES 应根据 WMS/RCS 回调更新 mount 关系；若回调缺少交换后关系，标记为 `UNKNOWN` 并进入对账。

### 5. 过程快照

`RackRelease` 表示单层货架一次释放周期。

建议字段：

| 字段                            | 说明                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `rack_release_id`             | 释放周期 ID，业务唯一。                                                                                                       |
| `single_layer_rack_code`      | 单层货架。                                                                                                               |
| `source_classifier_line_code` | 来源粗分机 WorkLine。                                                                                                     |
| `source_task_batch_id`        | 粗分机整架任务或批次。                                                                                                         |
| `release_status`              | `CANDIDATE`、`INBOX_CREATED`、`SESSION_STARTED`、`EXCHANGE_REQUESTED`、`COMPLETED`、`BLOCKED`、`RECONCILING`、`CANCELLED`。 |
| `released_at`                 | 整架完成时间。                                                                                                             |
| `moved_out_at`                | 离开粗分机时间。                                                                                                            |
| `inbox_id`                    | 关联 WorklineInbox。                                                                                                   |
| `session_id`                  | 关联 WorklineSession。                                                                                                 |
| `release_cycle_seq`           | 同一货架连续释放周期序号，避免迟到事件命中旧终态。                                                                                           |
| `idempotency_key`             | 由 `rack_code + source_task_batch_id/source_event_id + release_cycle_seq + snapshot_hash` 组成。                        |
| `snapshot_hash`               | 4 箱快照摘要。                                                                                                            |

`RackReleaseBinSnapshot` 表示释放瞬间每个槽位的料箱快照。

建议字段：

| 字段                        | 说明                   |
| ------------------------- | -------------------- |
| `rack_release_id`         | 所属释放周期。              |
| `slot_code`               | 单层货架槽位，v1 为 `S1-S4`。 |
| `bin_code`                | 料箱。                  |
| `bin_type_code`           | 快照时料箱类型。             |
| `bin_execution_status`    | 快照时 WES 执行状态。        |
| `usage_snapshot`          | WES 过程计算使用率，0-1。     |
| `material_summary_json`   | 物料摘要，不作为库存主账。        |
| `wms_inventory_refs_json` | WMS 库存记录引用、版本、查询时间。  |
| `content_snapshot_hash`   | 内容快照摘要。              |

`BinContentSnapshot` 表示料箱内部过程内容快照头；`BinContentSnapshotItem` 表示快照明细。这样可以表达一个料箱内多个 PKG、多个料箱内部槽位，以及完整/部分/未知快照状态。

`BinContentSnapshot` 建议字段：

| 字段                     | 说明                              |
| ---------------------- | ------------------------------- |
| `snapshot_id`          | 快照 ID。                          |
| `bin_code`             | 料箱。                             |
| `source_session_id`    | 产生快照的 WorklineSession。          |
| `source_event_id`      | 来源事件或命令结果。                      |
| `captured_at`          | 快照时间。                           |
| `snapshot_status`      | `COMPLETE`、`PARTIAL`、`UNKNOWN`。 |
| `snapshot_hash`        | 快照头和明细的稳定摘要。                    |
| `wms_snapshot_version` | WMS 查询版本或时间。                    |

`BinContentSnapshotItem` 建议字段：

| 字段                 | 说明             |
| ------------------ | -------------- |
| `snapshot_id`      | 所属快照。          |
| `bin_slot_code`    | 料箱内部槽位。        |
| `pkg_code`         | 六合一码或物料身份展示字段。 |
| `material_code`    | 物料编码引用。        |
| `vendor_code`      | 供应商引用。         |
| `lot_code`         | 批次展示字段。        |
| `date_code`        | Date Code。     |
| `qty_snapshot`     | 当时执行过程看到的数量。   |
| `thickness_mm`     | 厚度。            |
| `dims_json`        | 尺寸。            |
| `wms_inventory_id` | WMS 库存记录引用。    |

设计说明：

- `qty_snapshot` 只用于解释当时 WES 为什么这样决策。
- 发料、预留、可用库存仍必须查询 WMS。
- 快照可按 TTL 或归档策略保留；不能因 WMS 后续库存变化而改写历史快照。
- `RackReleaseBinSnapshot` 应引用 `snapshot_id`，并保留快照完整性状态，避免把部分扫描结果当作完整 4 箱事实。

### 6. 满箱交换任务

`FullBoxExchangeTask` 是满箱交换插件之外的过程聚合，建议作为资源模型的一部分，而不是只依赖 Session context。

建议字段：

| 字段                           | 说明                                                                                                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `exchange_request_code`      | WES 请求编码，与 Outbox dispatch\_key 同源。                                                                                                                                                        |
| `rack_release_id`            | 来源释放周期。                                                                                                                                                                                    |
| `session_id`                 | WorklineSession。                                                                                                                                                                           |
| `outbox_id`                  | EXTERNAL\_HTTP Outbox。                                                                                                                                                                     |
| `exchange_status`            | `REQUESTED`、`ACCEPTED`、`QUEUED`、`IN_PROGRESS`、`PHYSICAL_COMPLETED`、`RESOURCE_PROJECTED`、`WMS_CONFIRMED`、`BUSINESS_COMPLETED`、`WMS_REJECTED`、`REJECTED`、`FAILED`、`CANCELLED`、`RECONCILING`。 |
| `exchange_area_code`         | 满箱交换区。                                                                                                                                                                                     |
| `requested_bins_json`        | 建议交换的料箱槽位。                                                                                                                                                                                 |
| `wms_rcs_task_id`            | WMS/RCS 任务 ID。                                                                                                                                                                             |
| `wms_rcs_event_id`           | 最近一次 WMS/RCS 事件 ID。                                                                                                                                                                        |
| `queue_position`             | 排队位置。                                                                                                                                                                                      |
| `eta_seconds`                | 预计等待或完成时间。                                                                                                                                                                                 |
| `failure_code`               | 失败或拒绝原因。                                                                                                                                                                                   |
| `failure_message`            | 失败或拒绝描述。                                                                                                                                                                                   |
| `request_payload_hash`       | 请求摘要。                                                                                                                                                                                      |
| `last_callback_payload_hash` | 最近回调摘要。                                                                                                                                                                                    |
| `writeback_evidence_id`      | 关联 WMS 回写证据。                                                                                                                                                                               |

设计说明：

- Runtime Session 仍是流程主线，拥有 `WAITING_EXTERNAL`、deadline、timeout、重试和人工阻断；`FullBoxExchangeTask` 是面向查询和对账的业务过程表。
- 插件不能直接创建外部请求或直接写该表；应通过 runtime-owned `RuntimeIntent.external_request(...)`，由 Runtime 在同一写锁内创建 `WorklineOutbox(EXTERNAL_HTTP)`、任务证据和 Timeline。
- 不在该表中写库存变动结果，只写 WMS/RCS 任务和 WMS 确认引用。
- 若第一版为了收敛范围不建该表，至少必须把这些字段稳定写入 Session context；但后续看板和对账会更困难。

`WmsWritebackEvidence` 用于证明 WES 是否已向 WMS 提交，以及 WMS 是否确认。

建议字段：

| 字段                  | 说明                 |
| ------------------- | ------------------ |
| `evidence_code`     | 证据编码。              |
| `request_id`        | WES 请求 ID。         |
| `idempotency_key`   | WMS 回写幂等键。         |
| `dispatch_key`      | Outbox 派发键。        |
| `endpoint`          | WMS 接口或回调类型。       |
| `request_hash`      | 脱敏请求摘要。            |
| `response_hash`     | 脱敏响应摘要。            |
| `http_status`       | HTTP 状态。           |
| `wms_document_id`   | WMS 单据、任务或库存确认引用。  |
| `inventory_version` | WMS 确认后的库存版本或业务版本。 |
| `confirmed_at`      | WMS 确认时间。          |
| `retry_count`       | 重试次数。              |
| `failure_code`      | 失败原因。              |

### 7. 资源事实流

`ResourceStateEvent` 是资源模型的 append-only 事实日志。

建议字段：

| 字段                | 说明                                                                                                                            |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `event_code`      | 资源事件唯一编码。                                                                                                                     |
| `event_type`      | `RACK_ARRIVED`、`RACK_DEPARTED`、`BIN_MOUNTED`、`BIN_UNMOUNTED`、`RACK_RELEASED`、`EXCHANGE_STATUS_UPDATED`、`RESOURCE_RECONCILED`。 |
| `resource_type`   | `WORKLINE`、`DEVICE`、`RACK`、`BIN`、`MATERIAL`、`LOCATION`、`RACK_RELEASE`、`EXCHANGE_TASK`。                                        |
| `resource_code`   | 资源编码。                                                                                                                         |
| `source_system`   | `WMS`、`RCS`、`ECS`、`WES_RUNTIME`、`MANUAL`。                                                                                     |
| `source_event_id` | 来源事件 ID。                                                                                                                      |
| `source_version`  | 来源单调版本；旧版本只能归档为 evidence，不能覆盖当前投影。                                                                                            |
| `trace_id`        | WorkLine trace。                                                                                                               |
| `session_id`      | 可选 Session。                                                                                                                   |
| `payload_json`    | 事件事实。                                                                                                                         |
| `occurred_at`     | 事实发生时间。                                                                                                                       |
| `received_at`     | WES 接收时间。                                                                                                                     |

用途：

- 资源状态回放。
- 对账证据。
- 迟到回调处理。
- 报表和运营查询的事实源。
- 资源域建议使用独立 `ResourceSourceSystem`，或明确迁移现有 Inbox `SourceSystem`；外部回调不能统一写成 `SYSTEM` 后丢失 WMS/RCS/ECS 来源。
- 每类资源必须定义 `source_version`、`occurred_at/received_at`、来源优先级和乱序处理规则；迟到事实只追加，不覆盖当前投影。

### 8. 物料流转与执行分析事实

SMT 的入库、上架、出库发料、生产退料都会发生物料或容器的物理流转。统计分析不应由各插件私自累计，而应从统一事实层异步聚合。

本节是远期目标模型。首版资源模型只保留事件命名、字段扩展点和 trace 关联，不落完整 `MaterialFlowRun/Event`、`FlowStageMetric`、`FlowAnalyticsProjection` 表，不把生产发料/生产退料的完整业务模型纳入首批交付。

核心原则：

> 执行链路只写事实；统计分析从事实流、过程快照和 Timeline 中异步聚合，不能反向影响执行事务。

`MaterialFlowRun` 表示一次可追踪的物料或容器流转主线。它不是库存主账，而是执行分析主线。

建议字段：

| 字段                       | 说明                                                         |
| ------------------------ | ---------------------------------------------------------- |
| `flow_run_code`          | 流转主线编码，业务唯一。                                               |
| `flow_type`              | `INBOUND`、`PUTAWAY`、`ISSUE`、`RETURN`、`REWORK`、`TRANSFER`。  |
| `flow_status`            | `RUNNING`、`COMPLETED`、`BLOCKED`、`RECONCILING`、`CANCELLED`。 |
| `business_key`           | 业务键，可为 `rack_release_id`、波次号、退料单号、PKG key。                 |
| `trace_id`               | Runtime trace。                                             |
| `session_id`             | 当前主 Session。                                               |
| `workline_code`          | 当前 WorkLine。                                               |
| `source_location_code`   | 起点。                                                        |
| `target_location_code`   | 目标点。                                                       |
| `started_at`             | 开始时间。                                                      |
| `ended_at`               | 结束时间。                                                      |
| `wms_document_refs_json` | WMS 单据、预留、库存确认引用。                                          |
| `summary_json`           | 只读摘要，用于查询展示。                                               |

`MaterialFlowEvent` 表示一次流转事实。

建议字段：

| 字段                | 说明                                                                                                                                                                                  |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `flow_event_code` | 事件唯一编码。                                                                                                                                                                             |
| `flow_run_code`   | 所属流转主线。                                                                                                                                                                             |
| `event_type`      | `MATERIAL_SCANNED`、`BIN_SELECTED`、`PLACED_TO_BIN`、`RACK_RELEASED`、`EXCHANGE_REQUESTED`、`PUTAWAY_COMPLETED`、`ISSUE_PICKED`、`RETURN_RECEIVED`、`WMS_CONFIRMED`、`BLOCKED`、`RECONCILED`。 |
| `stage`           | `CLASSIFY`、`FULL_BOX_EXCHANGE`、`SORTER_PUTAWAY`、`PIPELINE_PICKING`、`PRODUCTION_ISSUE`、`PRODUCTION_RETURN`、`WMS_CONFIRMATION`。                                                       |
| `result`          | `SUCCESS`、`SKIPPED`、`BUSINESS_NG`、`RESOURCE_REJECTED`、`DEVICE_FAILED`、`TIMEOUT`、`MANUAL_RESOLVED`。                                                                                  |
| `pkg_code`        | PKG 展示字段，可为空。                                                                                                                                                                       |
| `material_code`   | 物料编码引用。                                                                                                                                                                             |
| `lot_code`        | 批次展示字段。                                                                                                                                                                             |
| `vendor_code`     | 供应商引用。                                                                                                                                                                              |
| `rack_code`       | 关联货架。                                                                                                                                                                               |
| `bin_code`        | 关联料箱。                                                                                                                                                                               |
| `location_code`   | 关联地码。                                                                                                                                                                               |
| `workline_code`   | 关联 WorkLine。                                                                                                                                                                        |
| `device_code`     | 关联设备。                                                                                                                                                                               |
| `session_id`      | 关联 Session。                                                                                                                                                                         |
| `outbox_id`       | 关联 Outbox。                                                                                                                                                                          |
| `wms_task_id`     | WMS/RCS 任务引用。                                                                                                                                                                       |
| `payload_json`    | 事件证据。                                                                                                                                                                               |
| `occurred_at`     | 事实发生时间。                                                                                                                                                                             |

`FlowStageMetric` 是可选阶段聚合，不进入执行主事务；由异步任务或查询投影生成。

建议字段：

| 字段              | 说明         |
| --------------- | ---------- |
| `flow_run_code` | 流转主线。      |
| `stage`         | 阶段。        |
| `started_at`    | 阶段开始。      |
| `ended_at`      | 阶段结束。      |
| `duration_ms`   | 阶段耗时。      |
| `wait_ms`       | 等待耗时。      |
| `queue_ms`      | 排队耗时。      |
| `execution_ms`  | 设备或外部执行耗时。 |
| `result`        | 阶段结果。      |
| `failure_code`  | 异常原因。      |

`FlowAnalyticsProjection` 是面向报表的投影，可按小时、班次、日维度异步聚合。

建议统计维度：

| 维度     | 示例                                                         |
| ------ | ---------------------------------------------------------- |
| 流程     | 入库、上架、生产发料、生产退料、返工。                                        |
| 阶段     | 粗分、满箱交换、分拣机上架、流水线拣选、产线交接、退料复核、WMS 确认。                      |
| 工件/物料  | `pkg_code`、`material_code`、`lot_code`、`vendor_code`、尺寸、厚度。 |
| 载体     | `rack_code`、`rack_type`、`bin_code`、`bin_type`、`slot_code`。 |
| 资源     | WorkLine、设备、交换区、地码、WMS/RCS task。                           |
| 时间     | 小时、班次、日、释放时间、请求时间、完成时间。                                    |
| 结果     | 成功、无需处理、业务 NG、资源拒绝、设备失败、超时、人工对账。                           |
| WMS 协同 | WMS 单据、预留号、库存确认号、接口耗时、确认状态。                                |

建议核心指标：

- 入库吞吐：每小时 PKG 数、料箱数、货架释放数、上架完成数。
- 满箱交换效率：释放到请求耗时、排队时长、交换执行时长、闭环总时长、ETA 偏差。
- 分拣/上架效率：上架成功率、单箱处理时长、异常率。
- 发料效率：波次准时率、缺料率、拣选成功率、产线交接时长。
- 退料效率：退料接收时长、退料复核时长、退库确认时长、二次处理率。
- 资源效率：货架周转率、料箱周转率、交换区占用率、设备忙闲比。
- 质量异常：NG 原因分布、扫码失败、尺寸/厚度异常、设备失败、对账次数。
- WMS 协同：接口耗时、超时率、确认失败率、迟到回调率、重复回调率。

### 9. 全流程覆盖关系

| 流程        | 主要资源事实                                                       | 主要分析事实                                                                                     |
| --------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| 入库粗分      | `BinContentSnapshot`、`RackBinMount`、`ResourceStateEvent`     | `MaterialFlowEvent(MATERIAL_SCANNED, PLACED_TO_BIN)`、粗分吞吐、NG 率、单件处理耗时。                     |
| 满箱交换上架    | `RackRelease`、`RackReleaseBinSnapshot`、`FullBoxExchangeTask` | `MaterialFlowEvent(RACK_RELEASED, EXCHANGE_REQUESTED, PUTAWAY_COMPLETED)`、排队时长、交换时长、资源拒绝率。 |
| 分拣机/流水线上架 | `RackPlacement`、`RackBinMount`、`BinContentSnapshot`          | `MaterialFlowEvent(PICKING_STARTED, PUTAWAY_COMPLETED)`、上架成功率、单箱处理时长。                      |
| 生产发料      | `RackPlacement`、`RackBinMount` 或 `RackMaterialMount`、WMS 预留引用 | `MaterialFlowEvent(ISSUE_RESERVED, ISSUE_PICKED, LINE_DELIVERED)`、波次准时率、缺料率、拣选耗时。          |
| 生产退料      | `RackMaterialMount`、退料快照、WMS 确认引用                         | `MaterialFlowEvent(RETURN_RECEIVED, RETURN_SORTED, WMS_CONFIRMED)`、退料闭环时长、复核异常率。           |
| 异常与对账     | `RuntimeHold`、`ResourceStateEvent(RESOURCE_RECONCILED)`      | `MaterialFlowEvent(BLOCKED, RECONCILED)`、阻断恢复时长、原因分布。                                      |

## 核心关系

```text
RackType 1 --- N RackSlotTemplate
RackType 1 --- N Rack
Rack 1 --- N RackPlacement
ExecutionLocation 1 --- N RackPlacement

Rack 1 --- N RackBinMount
RackSlotTemplate 1 --- N RackBinMount
Bin 1 --- N RackBinMount
BinType 1 --- N Bin
BinType 1 --- N BinSlotTemplate

Rack 1 --- N RackMaterialMount
RackSlotTemplate 1 --- N RackMaterialMount
RackMaterialMount N --- 0..1 wms_inventory_refs_json(value object)

Rack 1 --- N RackRelease
RackRelease 1 --- 4 RackReleaseBinSnapshot
RackRelease 1 --- 0..N FullBoxExchangeTask
WorklineSession 1 --- 0..1 RackRelease
WorklineSession 1 --- 0..N FullBoxExchangeTask
WorklineOutbox 1 --- 0..1 FullBoxExchangeTask

MaterialFlowRun 1 --- N MaterialFlowEvent
MaterialFlowRun 1 --- N FlowStageMetric
MaterialFlowEvent N --- 0..1 WorklineSession
MaterialFlowEvent N --- 0..1 RackRelease
MaterialFlowEvent N --- 0..1 FullBoxExchangeTask
```

## 状态规则

### Rack

| 状态            | 含义                 |
| ------------- | ------------------ |
| `AVAILABLE`   | 可参与执行。             |
| `LOCKED`      | 被任务锁定，不得分配给新流程。    |
| `IN_TRANSIT`  | 搬运中，位置未闭环。         |
| `AT_WORKLINE` | 已到达某 WorkLine 工作位。 |
| `IN_EXCHANGE` | 正在交换。              |
| `EXCEPTION`   | 关系冲突、现场异常或需人工确认。   |
| `DISABLED`    | 停用。                |
| `UNKNOWN`     | 状态未知，不能自动调度。       |

### Bin

| 状态               | 含义                |
| ---------------- | ----------------- |
| `EMPTY_VERIFIED` | 现场确认空箱。           |
| `IN_USE`         | 执行过程中已承载物料或正在被操作。 |
| `LOCKED`         | 被任务锁定。            |
| `FULL_SNAPSHOT`  | 最近一次过程快照达到满箱策略。   |
| `EXCEPTION`      | 料箱状态冲突或现场异常。      |
| `DISABLED`       | 停用。               |
| `UNKNOWN`        | 状态未知。             |

### RackRelease

| 状态                   | 含义                          |
| -------------------- | --------------------------- |
| `CANDIDATE`          | 发现候选释放事实，尚未入 Inbox。         |
| `INBOX_CREATED`      | 已创建标准 WorklineInbox。        |
| `SESSION_STARTED`    | 已进入 WorkLine Session。       |
| `EXCHANGE_REQUESTED` | 已请求 WMS/RCS 满箱交换。           |
| `PHYSICAL_COMPLETED` | 外部物理动作完成，但 WMS 账务或资源投影未必完成。 |
| `RESOURCE_PROJECTED` | WES 已根据可信事件完成资源当前投影更新。      |
| `WMS_CONFIRMED`      | WMS 已确认库存、单据或任务结果。          |
| `BUSINESS_COMPLETED` | 物理动作、资源投影和 WMS 确认均已闭环。      |
| `BLOCKED`            | 业务或资源失败阻断。                  |
| `RECONCILING`        | 物理状态未知，等待对账。                |
| `CANCELLED`          | 被取消。                        |

## 流程落点

### 空架补给

1. WES 根据 WorkLine 需要向 WMS/RCS 提交空架补给请求。
2. WMS/RCS 完成搬运后回调货架到达。
3. WES 更新 `RackPlacement`，并写 `ResourceStateEvent(RACK_ARRIVED)`。
4. ECS 扫描并验证 4 个料箱。
5. WES 更新 `RackBinMount` 和 `Bin.status=EMPTY_VERIFIED`。

### 粗分机装箱

1. 设备扫描物料，WES 插件决策目标料箱。
2. 设备执行放入后回传成功。
3. WES 写 `BinContentSnapshot` 或更新当前过程摘要。
4. WMS 库存确认仍由 WMS 接口完成；WES 只保留确认请求和响应引用。

### 单层货架释放

1. 粗分机整架完成且货架已移出。
2. 候选服务从 active `RackBinMount` 和过程摘要生成 `RackRelease`。
3. 生成 4 条 `RackReleaseBinSnapshot`。
4. 幂等创建 `SINGLE_LAYER_RACK_RELEASED` Inbox。

### 满箱交换

1. 插件消费 `RackRelease` 对应事件。
2. 需要交换时，插件返回 `RuntimeIntent.external_request(...)`，由 Runtime 创建 `WorklineOutbox(EXTERNAL_HTTP)`、`FullBoxExchangeTask`、Timeline 和 `WAITING_EXTERNAL` 等待状态。
3. 外部请求 wait token 使用 `dispatch_key/exchange_request_code`，并记录 deadline；超时或派发状态未知必须进入 RuntimeHold。
4. WMS/RCS 回 `ACCEPTED`、`QUEUED`、`IN_PROGRESS` 时更新任务状态，但不完成 Session。
5. WMS/RCS 回 `PHYSICAL_COMPLETED` 后，WES 只在回调包含可信交换后关系时写 `ResourceStateEvent` 并更新 `RackBinMount` 投影。
6. WMS 回 `WMS_CONFIRMED` 后写 `WmsWritebackEvidence`，再推进到 `BUSINESS_COMPLETED`。
7. 若回调未提供足够关系证据，任务进入 `RECONCILING`，不能直接更新 mount。
8. 库存账务变动以 WMS 确认结果为准；WES 只存引用和快照。

### 零散拣选与发料

资源模型按货架槽位承载对象选择关系投影：

- 单层/五层货架上的物料仍通过料箱承载，复用 `RackBinMount`、`BinContentSnapshot`。
- 运转货架或其他卡槽式货架直接承载物料时，使用 `RackMaterialMount`。
- 卡槽式物料占用必须携带稳定 `material_identity_key` 和 WMS 拆分/确认引用；缺失时进入 RuntimeHold/资源对账。
- 库存可用性、预留和扣减必须调用 WMS，不能从 WES 快照推导。

### 退料与运转货架

退料货架和运转货架是卡槽式结构，槽位类型为 `MATERIAL_SLOT`。WES 可通过 `RackMaterialMount` 追踪 PKG/物料与卡槽的过程关系，但退料入库确认、库存属性和数量仍由 WMS 负责。

## 对账与冲突处理

进入资源对账的情况：

- 同一个料箱同时出现在两个 active rack slot。
- 同一个 rack slot 同时挂载两个料箱。
- 同一个卡槽式 rack slot 同时承载多个 active 物料关系。
- 同一个 rack slot 同时存在 active `RackBinMount` 和 active `RackMaterialMount`。
- WMS 不允许拆分的同一 `material_identity_key` 同时出现在多个 active material slot。
- `material_identity_key` 缺失、模糊、多候选，或 WMS split policy 为 `UNKNOWN`。
- WMS/RCS 回调的交换后关系与 WES 释放快照冲突。
- 设备扫描发现未知货架或未知料箱。
- 货架 active placement 与 WMS/RCS 搬运完成地码不一致。
- `RackRelease` 的 4 箱快照不完整。

处理规则：

- 不覆盖冲突记录。
- 相关 Rack/Bin/Release/Task 标记为 `EXCEPTION` 或 `RECONCILING`。
- 创建诊断、`ResourceStateEvent` 和 RuntimeHold 或资源对账项。
- 后续必须通过 `RuntimeHoldReleaseService` 或资源对账服务人工确认后修复，并写 `RESOURCE_RECONCILED` 事实。
- 手工修复与迟到回调同时到达时，以单调 `source_version`、人工确认版本和来源优先级处理；迟到事实只归档，不能覆盖已确认投影。

## 与 WMS 的同步策略

### 初始化

- 货架、料箱、地码可从 WMS 拉取或现场导入。
- 所有导入记录必须保留 `wms_*_id`、`source_system`、`source_version`。
- 未在 WMS 存在但现场扫描到的资源，可先建 `UNKNOWN/PROVISIONAL` 状态，必须对账后才能参与自动调度。

### 查询缓存

- 库存查询结果 TTL 不超过 30 秒。
- 货架/料箱状态查询结果 TTL 建议不超过 10 秒。
- 缓存只服务一次决策，不改写历史快照。

### WMS 断连

| 动作类型                | 是否允许继续 | 处理规则                                                           |
| ------------------- | ------ | -------------------------------------------------------------- |
| 涉及库存预留、扣减、入库确认、发料确认 | 不允许    | 阻断任务，创建 RuntimeHold，等待 WMS 恢复或人工授权。                            |
| 满箱交换资源授权、五层货架空位判断   | 不允许    | WES 不本地替代 WMS/RCS 判断，保持 `QUEUED` 或 `WMS_CONFIRMATION_PENDING`。 |
| 纯物理状态恢复、设备安全停靠      | 允许     | 只写现场事实和诊断，不产生库存确认。                                             |
| 已知空箱回流且不改变库存主账      | 条件允许   | 必须有 SOP 白名单和可审计操作人，仍需后续 WMS 补确认。                               |
| 外部回调迟到或重复           | 允许接收   | 只追加 evidence，经幂等和版本校验后决定是否推进投影。                                |

资源模型可以记录现场事实，但所有库存相关状态标记为 `WMS_CONFIRMATION_PENDING` 或保留 WMS 引用空值。

## 模块边界建议

新增领域模块建议为 `src/app/resource/`，因为资源会被多个 WorkLine 插件复用，不应放进 `smt_full_box_exchange` 插件目录。

建议结构：

```text
src/app/resource/
  models/
  repositories/
  services/
  v1/
```

职责边界：

- `models`: 执行资源、关系、快照、任务状态模型。
- `repositories`: 只做查询和持久化，不写业务决策。
- `services`: 资源状态变更、快照生成、对账、WMS 同步适配。
- `v1`: 管理、查询、对账 API；API 不直接访问数据库。
- WorkLine 插件只能通过 Service 或 Runtime context 使用资源事实，不能直接写资源表。
- WorkLine、Device 主数据仍由现有模块负责；资源模块只保存 `workline_code/device_code` 引用、运行时证据和跨资源查询投影。
- WMS/RCS 执行类回调入口已由 ADR 锁定为 `/callback/external`，并支持 `exchange_request_code/dispatch_key/wms_rcs_task_id` 恢复任务；同一运行时任务不得并行使用虚拟 Device 和 `/callback/external` 两套入口。

## 推荐落地顺序

1. 第零阶段：ADR/SRS 与 WMS/RCS 合同门禁
   - 已新增 `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`，并修订 SRS 中 WES 锁空箱、交换库存属性、自动扣减库存等旧口径。
   - 已明确 WMS/RCS 执行类回调入口为 `/api/v1/callback/external`；实现前仍需按接口需求文档落签名、幂等、版本、交换后关系、WMS 确认字段测试。
   - 明确 `MATERIAL_SLOT` 的 WMS 物料身份、库存版本、分拆/合并策略和确认字段；未明确前不得实现可信 `RackMaterialMount` active 投影。
   - 明确 `RuntimeIntent.external_request(...)`、`WAITING_EXTERNAL`、deadline、late callback 规则。
2. 第一阶段：WES 运行时资源底座
   - 建立 Resource Registry 或资源引用规范，覆盖 `WORKLINE/DEVICE/RACK/BIN/MATERIAL/LOCATION/EXCHANGE_TASK`。
   - WorkLine/Device 复用现有表，资源域只做引用和事实关联。
   - 落最小 Zone、Location、RackType、RackSlotTemplate、Rack、BinType、BinSlotTemplate、Bin。
   - `RackSlotTemplate.slot_kind` 必须区分 `BIN_SLOT` 与 `MATERIAL_SLOT`。
3. 第二阶段：资源事实账本与当前投影
   - `ResourceStateEvent` 作为 append-only 事实账本。
   - `RackPlacement`、`RackBinMount`、`RackMaterialMount` 作为当前关系投影。
   - `ResourceRelationService` 统一处理资源锁、容量、幂等、乱序、迟到和投影更新。
4. 第三阶段：最小对账与 RuntimeHold
   - 冲突检测、`UNKNOWN/RECONCILING`、人工确认、证据记录和审计前移。
   - 资源冲突必须创建或复用 RuntimeHold，释放必须走 `RuntimeHoldReleaseService` 或资源对账服务。
5. 第四阶段：WMS 回写与确认证据
   - `WmsWritebackEvidence` 覆盖请求、响应、确认、重试、失败和脱敏摘要。
   - WMS 断连动作矩阵、重放防护、幂等回放和补偿任务落地。
6. 第五阶段：释放快照与满箱交换闭环
   - `RackRelease`、`RackReleaseBinSnapshot`、`BinContentSnapshot` header/item。
   - `FullBoxExchangeTask` 与 Runtime 外部请求联动，支持 `PHYSICAL_COMPLETED/RESOURCE_PROJECTED/WMS_CONFIRMED/BUSINESS_COMPLETED`。
7. 第六阶段：后续分析事实与跨流程扩展
   - `MaterialFlowRun/Event`、生产发料、生产退料、完整 BI/OEE 投影后置。
   - 首版只保留事件命名、trace/session/resource 关联和扩展字段。

## 测试策略

### 合同与门禁测试

- ADR/SRS 修订后，所有实现计划只引用新边界：WES 不锁五层空箱、不交换库存属性、不自动扣减库存。
- WMS/RCS 回调 schema 校验覆盖 `exchange_request_code`、`dispatch_key`、`wms_rcs_task_id`、`source_event_id`、`source_version`、`occurred_at`。
- `MATERIAL_SLOT` 合同校验覆盖 `material_identity_key`、`wms_inventory_id`、`wms_inventory_version`、`wms_split_policy`、`wms_confirmation_status`。
- WMS/RCS 执行类回调入口固定为 `/callback/external`，不允许虚拟 Device 与 `/callback/external` 两个入口并行承载同一任务。
- 签名、时间窗、request\_id 唯一约束、payload canonical hash 覆盖重放和篡改测试。

### 模型测试

- 单层货架类型必须生成 4 个 `BIN_SLOT`。
- 五层货架类型必须支持 20 个料箱位和 A/B 面。
- 单层货架和五层货架的所有货位必须是 `BIN_SLOT`。
- 退料货架和运转货架的卡槽货位必须是 `MATERIAL_SLOT`。
- 生产货架必须按 `RackSlotTemplate.slot_kind` 判断承载对象，不能从 `RackType=PRODUCTION` 推断。
- active `RackPlacement` 唯一性。
- active `RackBinMount` 对 rack slot 和 bin 的唯一性。
- active `RackMaterialMount` 对 material slot 的唯一性。
- 不允许不兼容料箱挂载到槽位。
- 不允许在 `MATERIAL_SLOT` 上创建 `RackBinMount`。
- 不允许在 `BIN_SLOT` 上创建 `RackMaterialMount`。
- `RackMaterialMount.material_identity_key` 缺失、模糊或多候选时不能创建可信 active 投影。
- `ResourceStateEvent.resource_type` 覆盖 `WORKLINE/DEVICE/RACK/BIN/MATERIAL/LOCATION/EXCHANGE_TASK`。
- `BinContentSnapshot` header/item 能表达多 PKG、多料箱槽位和 `COMPLETE/PARTIAL/UNKNOWN`。

### 服务测试

- WMS/RCS 货架到达回调更新 placement。
- ECS 验空生成 4 个 active mount。
- 粗分机释放生成唯一 `RackRelease` 和 4 个 bin snapshot。
- 同一 rack 连续两次释放不会命中旧终态 Session，释放幂等键包含 release cycle 和 snapshot hash。
- CTU 物理完成回调先写 `PHYSICAL_COMPLETED`，可信交换后关系才能更新 mount 投影。
- WMS 确认回调写 `WmsWritebackEvidence` 后才能进入 `BUSINESS_COMPLETED`。
- 回调缺失交换后关系时进入 `RECONCILING`。
- 退料/运转卡槽扫描后写 `RackMaterialMount`，不创建料箱挂载关系。
- 同一 `material_identity_key` 跨多个卡槽时按 WMS `wms_split_policy` 判定：`SPLITTABLE` 可保留多 active，`NOT_SPLITTABLE` 进入对账，`UNKNOWN` 阻断。
- 外部请求由 Runtime 创建 `WorklineOutbox(EXTERNAL_HTTP)`、`WAITING_EXTERNAL` deadline、Timeline 和任务证据。
- 入库、满箱交换、生产发料、生产退料的分析事实首版只验证事件命名和扩展字段，不要求完整 `MaterialFlowEvent` 表。

### 边界测试

- 库存查询结果只写 snapshot/ref，不更新本地库存主账。
- WMS 断连时涉及库存变动的服务返回阻断。
- 未知资源不能自动参与调度。
- 同一 bin 出现在两个 active mount 时创建对账诊断。
- 同一卡槽出现两个 active material mount 时创建对账诊断。
- 同一 rack slot 同时出现 active bin mount 与 active material mount 时创建对账诊断。
- `qty_snapshot`、`pkg_code`、`material_code`、`lot_code`、`vendor_code` 只能作为展示和证据，不能驱动库存可用、拆分合法性或出入库确认。
- 统计聚合缺失或失败不能回滚现场执行事务。
- 插件不得直接累计 KPI 或写分析投影。
- 乱序 `source_version` 和迟到 `occurred_at` 只追加 evidence，不覆盖当前投影。
- 手工修复完成后再到达旧回调，不得回滚人工确认结果。

### 并发与约束测试

- 两个回调同时更新同一 `rack_code/location_code`，服务层使用行锁或 advisory lock 串行化。
- 两个回调同时把不同 bin 挂到同一 slot，失败分支进入 `RECONCILING`，不留下半写 active mount。
- 两个回调同时把不同物料写入同一卡槽，失败分支进入 `RECONCILING`，不留下半写 active material mount。
- `ResourceRelationService` 不得在持有 active 投影锁时等待 WMS/RCS HTTP；外部响应必须先落 evidence，再进入短事务投影更新。
- 交换区容量冲突不依赖数据库唯一约束抛错，应返回可审计诊断。
- 重复 WMS 回调、重复 HTTP 重试、Outbox 重放必须保持任务状态幂等。

### 集成测试

- 空架补给 -> ECS 验空 -> 粗分机装箱 -> RackRelease -> 满箱交换请求。
- 满箱交换 `QUEUED` 不完成 Session。
- 满箱交换 `PHYSICAL_COMPLETED` 更新物理证据，`WMS_CONFIRMED` 后才允许业务闭环。
- RuntimeHold 在资源冲突时创建，人工释放后相关 Outbox 或 Session 才能继续。
- 入库 -> 满箱交换/上架 -> 生产发料 -> 生产退料在首版能按 trace/session/resource 追踪扩展点，完整 flow 查询后置。

## 与满箱交换插件计划的关系

`docs/superpowers/plans/2026-05-13-smt-full-box-exchange.md` 依赖本资源模型提供以下前置能力：

- 稳定 `rack_release_id`。
- 单层货架 4 箱快照。
- `RackBinMount` 当前关系。
- `RackRelease` 与 `RackReleaseBinSnapshot` 幂等。
- 满箱交换任务状态与 WMS/RCS 回调证据。

如果资源模型暂不实现，满箱交换插件仍可把快照放在 Session context 中完成最小闭环，但会牺牲长期对账、重复释放、运营查询和跨流程复用能力。

## 风险

- WES 是否锁定五层空箱资源的口径差异已由 ADR/SRS 修订处理；后续实现不得重新引入本地空箱锁定、库存属性交换或自动扣减库存。
- 如果 WMS 无法提供稳定 rack/bin/location 主数据，WES 需要增加导入和对账流程。
- 如果现场设备扫描到的物理关系与 WMS 长期不一致，必须优先建设资源对账，不应继续扩大自动调度。
- 资源模型表较多，建议分阶段落地，避免一次性阻塞满箱交换插件。

## 验收标准

- ADR/SRS 已修订并通过评审，WES/WMS/RCS 权责边界与实现计划一致；权威 ADR 为 `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`。
- WMS/RCS 回调入口、幂等键、签名、版本、乱序、迟到和交换后关系合同已明确。
- `MATERIAL_SLOT` 的 WMS 物料身份、库存版本、分拆/合并策略和确认字段已明确。
- Runtime 支持外部请求意图、`WAITING_EXTERNAL`、deadline、超时 RuntimeHold 和 late callback 对账规则。
- 可以表达单层货架 4 箱、五层货架 20 箱和生产/退货货架料盘槽位。
- 可以区分货架槽位承载对象：单层/五层货架承载料箱，退料/运转卡槽货架直接承载物料。
- 可以统一引用 `WORKLINE/DEVICE/RACK/BIN/MATERIAL/LOCATION/EXCHANGE_TASK` 运行时资源。
- 可以追踪一个料箱当前挂载在哪个货架槽位，并保留历史。
- 可以追踪一个物料/PKG 当前占用哪个卡槽式货架槽位，并保留历史。
- 可以通过稳定 `material_identity_key` 和 WMS split policy 判断同一物料跨多个卡槽是合法拆分还是对账异常。
- 可以证明 `ResourceStateEvent` 是事实账本，`RackPlacement/RackBinMount/RackMaterialMount` 是当前投影，并能从事件追溯投影来源。
- 可以为一次单层货架释放生成稳定 `RackRelease` 和 4 个料箱快照。
- 可以在不维护本地库存主账的前提下保存 WMS 库存引用快照。
- 可以记录满箱交换请求、排队、物理完成、资源投影、WMS 确认、业务闭环、失败、迟到回调和对账证据。
- 可以记录 WMS 回写请求、响应、确认、失败、重试和脱敏摘要。
- 可以清楚区分 WES 执行事实与 WMS 库存事实。
- 可以证明 WES 没有用 `qty_snapshot` 或展示字段做库存可用、拆分合法性或库存确认判断。
- 可以为入库、上架、生产发料、生产退料和异常对账保留统一事件命名与扩展点；完整统计事实表和 BI 投影后置。
- 可以按流程、阶段、物料、货架、料箱、WorkLine、设备、时间和 WMS 引用设计统计聚合口径，但首版不要求交付看板。
- 统计事实写入失败不得影响已完成的设备/外部系统执行事务；失败应进入诊断和补偿任务。

***

## GSTACK AUTOPLAN REVIEW REPORT

### Phase 1: CEO Review

#### Plan Summary

本设计试图为 SMT 入库、满箱交换、分拣上架、生产发料和生产退料建立统一执行资源模型。评审结论是：问题方向成立，但当前文档已经从“满箱交换所需执行证据模型”扩展为“WES 资源事实平台”，且隐含修订了 WES/WMS/RCS 权责边界。

#### Premise Challenge

| 前提                                           | 评审                                                    | 处理建议                                                                  |
| -------------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------- |
| WES 可以长期持久化执行事实，但不拥有库存事实                     | 基本成立，但需要明确“执行事实”不是现场调度真相，也不是库存可用真相。                   | 增加 ADR/SRS 修订：Session runtime 可瞬态，审计/对账事实可持久化，所有库存和资源可用决策仍以 WMS 授权为准。 |
| WMS/RCS 能提供稳定 rack/bin/location 主数据、版本和交换后关系 | 当前只是默认假设，是 P0 风险。                                     | 在实施前补 WMS/RCS 最小合同验收，明确事件 ID、幂等键、版本语义、交换后关系字段、缺字段阻断规则。                |
| 统一资源模型应覆盖入库、上架、发料、退料和统计分析                    | 作为远期架构可以成立，作为第一版会放大工程范围。                              | v1 收敛到满箱交换证据、当前关系投影和最小对账；全流程统计事实先保留命名和边界，不进入首批实现。                     |
| SRS 与本设计边界一致                                 | 不成立。SRS 中存在 WES 锁定五层空箱、交换库存属性、Pick\_Fail 后自动扣减库存等旧口径。 | 必须把这些口径冲突写成前置架构决策，或同步修订 SRS。                                          |
| 对账 API 可以最后实现                                | 不成立。active placement/mount 一旦落库，没有最小对账能力就会积累脏状态。      | 最小对账能力前移：冲突检测、UNKNOWN/RECONCILING、人工确认、证据记录。                          |

#### Existing Code Leverage

| 子问题       | 可复用资产                                                                                             | 评审结论                                                             |
| --------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| 外部请求与回调   | `WorklineOutbox(EXTERNAL_HTTP)`、`InboxKind.EXTERNAL_HTTP`、`WAITING_EXTERNAL`、external callback 入口 | 满箱交换应优先复用 Runtime 外部请求主链路，不创建插件私有轮询流程。                           |
| 运行时追溯     | `WorklineSession`、`WorklineTimeline`、`TraceQueryService`、diagnostic/runtime hold 体系               | `ResourceStateEvent` 应补齐资源事实，不替代 timeline；两者通过 trace/session 关联。 |
| 异常与人工恢复   | `RuntimeHold`、`NgReturnItem`、`BLOCKED_RESOURCE` outbox 释放路径                                       | 资源冲突应进入 Runtime Hold 或资源对账服务，不能直接覆盖 active 关系。                   |
| 插件边界      | `docs/plugin_development_guide.md` 中插件不直接写 DB、不维护物料当前位置                                           | 满箱交换插件只能产生命令/外部请求/阻断意图，资源状态变更必须走 Service。                        |
| 现有轻量物料流模型 | `src/workline_runtime/material_run.py`、`material_flow_engine.py`、`runtime_event.py`               | 当前是内存运行态和事件模型，不能直接等同为持久 `MaterialFlowRun` 平台。                    |

#### Dream State Mapping

```text
CURRENT
  SMT 粗分机已有 WorkLine Runtime、Inbox/Outbox/Timeline/RuntimeHold，
  但货架、料箱、释放快照和满箱交换证据仍缺少稳定资源事实。

THIS PLAN
  建立资源、关系、释放快照、交换任务、事实流和统计事实模型，
  支撑满箱交换、分拣上架、发料、退料和后续分析。

12-MONTH IDEAL
  WES 拥有可信执行证据和对账能力；
  WMS 仍拥有库存、预留、账务和资源授权；
  插件只描述业务意图；
  运营可以从统一事实链路追溯每次物料流转、异常和恢复。
```

Dream state delta：当前计划朝 12 个月理想形态走，但首版范围过宽。最短路径不是一次性建设完整资源平台，而是先打穿“满箱交换证据账本 + WMS/RCS 合同 + 最小对账”。

#### Implementation Alternatives

| 方案                      | 范围                                                                                    | 优点              | 风险                       | 评审结论                 |
| ----------------------- | ------------------------------------------------------------------------------------- | --------------- | ------------------------ | -------------------- |
| A. 完整资源平台               | 一次性落 `ExecutionZone` 到 `FlowAnalyticsProjection`                                      | 远期一致性最好，跨流程复用清晰 | 首版慢，WMS/RCS 边界未定时会固化错误事实 | 不建议作为 v1。            |
| B. 满箱交换证据模型             | `RackRelease`、4 箱快照、`FullBoxExchangeTask`、最小 `RackBinMount`、`ResourceStateEvent`、最小对账 | 直接支撑当前插件，风险闭环更快 | 后续发料/退料仍需二次扩展            | 推荐作为 v1。             |
| C. Session context 最小闭环 | 不建资源域，只把快照写入 Session context                                                          | 最快验证插件流程        | 对账、重复释放、运营查询和跨流程复用弱      | 仅适合临时 spike，不适合生产计划。 |
| D. 事件账本 + 当前关系投影        | `ResourceStateEvent` 为源，当前关系表为投影                                                      | 可恢复、可回放、冲突处理清楚  | 实现复杂度高于 C，需要投影规则         | 推荐作为 B 的结构化实现方式。     |

#### Mode-Specific Analysis

Autoplan 采用 `SELECTIVE_EXPANSION`：只扩展本计划直接影响满箱交换可信闭环的内容，拒绝把完整 BI、发料、退料质量域、生产货架全生命周期作为首版实现范围。

#### Temporal Interrogation

| 时间点     | 风险                                                  |
| ------- | --------------------------------------------------- |
| Hour 1  | 开发者会不清楚应先建全量主数据，还是先打通满箱交换证据闭环。                      |
| Day 1   | 如果没有 WMS/RCS 合同，`RackBinMount` 更新路径无法判断何时可信。        |
| Week 1  | 统计事实和资源事实同时推进，会挤压满箱交换插件主链路实现。                       |
| Month 1 | 对账 API 后置会让 active 关系积累冲突，现场恢复依赖人工查日志。              |
| Month 6 | 如果不修 SRS，团队会分别引用“WES 锁定空箱”和“WMS 负责资源授权”两种口径，造成实现分裂。 |

#### CEO Dual Voices Consensus Table

| Dimension                  | Claude Subagent                        | Codex CLI               | Consensus        |
| -------------------------- | -------------------------------------- | ----------------------- | ---------------- |
| Premises valid?            | 部分成立，但 WMS/RCS 稳定合同是假设                 | 部分成立，执行事实边界没有真正成立       | DISAGREE/CONCERN |
| Right problem to solve?    | 应聚焦可恢复、可对账、可幂等的执行证据                    | 应聚焦满箱交换证据账本和 WMS/RCS 合同 | CONFIRMED        |
| Scope calibration correct? | 范围过大，应收敛 v1                            | 范围过大，暂停平台化统计和生产/退料货架    | CONFIRMED        |
| Alternatives explored?     | 不足，需比较 Session context、事件账本、只读 WMS 适配器 | 不足，10x 方案是证据账本 + 合同     | CONFIRMED        |
| WES/WMS/RCS risk covered?  | SRS 冲突未显式修订                            | SRS 冲突不能只作为风险备注         | CONFIRMED        |
| 6-month trajectory sound?  | 若先建全平台会积累半可信事实                         | 若不收敛会形成双真相源             | CONFIRMED        |

#### Section Review

1. Architecture Review：模型分层方向正确，但源事实与投影关系要写清楚。建议 `ResourceStateEvent` 作为资源事实账本，`RackPlacement/RackBinMount` 作为当前关系投影，而不是并列真相源。
2. Error & Rescue Map：文档列出了冲突条件，但救援能力后置。最小救援必须包含冲突检测、UNKNOWN/RECONCILING 状态、人工确认入口和 evidence 记录。
3. Security & Threat Model：未发现新增外部安全面，但 WMS/RCS 回调必须有签名、来源校验、事件幂等和重放防护；否则资源关系可能被错误回调污染。
4. Data Flow & Interaction Edge Cases：`COMPLETED` 语义不足。满箱交换至少拆分为 `PHYSICAL_COMPLETED`、`WMS_CONFIRMED`、`BUSINESS_COMPLETED`，防止物理动作完成被误认为库存账务完成。
5. Code Quality Review：计划模块边界符合 API -> Service -> Repository -> Database，但需要明确插件不得直接写资源表，候选扫描服务和回调处理也必须走 Service。
6. Test Review：测试方向覆盖了模型、服务、边界、集成，但缺少 WMS/RCS 合同失败测试、回调字段缺失测试、SRS 冲突口径回归测试。
7. Performance Review：当前表数量和事实量对单流程不是主要瓶颈，风险在统计投影过早进入执行事务。统计聚合必须异步，事实写入失败必须进入诊断/补偿。
8. Observability & Debuggability Review：需要把 `ResourceStateEvent`、`FullBoxExchangeTask`、`RackRelease` 与 trace/session/timeline 的查询入口写成验收条件。
9. Deployment & Rollout Review：首版必须支持灰度：只对满箱交换启用资源事实写入；生产/退料和 BI 投影不应随资源 v1 一起上线。
10. Long-Term Trajectory Review：统一资源模型是合理远期方向，但如果现在不收敛，将导致 `resource` 模块承载库存影子、执行状态、统计事实和质量域，边界会变脆。

#### Error & Rescue Registry

| Error / Risk          | User Impact     | Rescue Path                                       |
| --------------------- | --------------- | ------------------------------------------------- |
| WMS/RCS 回调缺少交换后关系     | WES 无法可信更新料箱挂载  | 任务进入 `RECONCILING`，不更新 active mount，要求人工或 WMS 补证。 |
| 同一料箱出现在两个 active slot | 查询结果误导现场操作      | 创建资源对账项，锁定相关 Rack/Bin，保留冲突前后证据。                   |
| SRS 旧口径被实现引用          | WES 可能越权锁空箱或扣库存 | 出 ADR/SRS 修订，实施计划引用新边界。                           |
| 统计事实写入失败              | 报表缺口，但现场动作可能已完成 | 不回滚执行事务，写诊断并允许异步补偿。                               |
| WMS 断连时继续“纯物理动作”边界不清  | 现场可能执行影响库存的动作   | 增加 SOP 和白名单动作，所有库存相关动作强制阻断。                       |

#### Failure Modes Registry

| Failure Mode                 | Severity | Detection         | Required Handling                                   |
| ---------------------------- | -------- | ----------------- | --------------------------------------------------- |
| WMS/RCS 合同字段不稳定              | Critical | 合同测试、回调 schema 校验 | 阻断实施或降级为 Session context 证据，不写 active 关系。           |
| 执行事实被当作库存事实                  | High     | API 命名、查询响应、代码审查  | 所有查询返回 `authority=WMS` / `snapshot_stale`，禁止本地库存决策。 |
| 对账能力后置                       | High     | 冲突测试、现场回调演练       | 最小对账前移到物理关系阶段。                                      |
| `COMPLETED` 误导业务闭环           | High     | 状态机测试             | 拆分物理完成、WMS 确认、业务完成。                                 |
| 过早建设 `MaterialFlowRun/Event` | Medium   | 计划审查              | v1 只定义事件命名，不建设完整聚合平台。                               |

#### NOT In Scope After CEO Review

以下内容不进入第一版实现，除非前提门禁明确推翻本评审建议：

- 完整 `FlowAnalyticsProjection`、OEE/BI 看板、班次级经营分析。
- 生产/退货货架完整生命周期、飞达/MSD/LCR/X-Ray/重贴标质量域模型。
- WES 自主锁定五层空箱、库位分配、库存扣减或库存属性交换。
- AGV/CTU/RCS 路径、坐标、调度算法建模。

#### What Already Exists

- WorkLine Runtime 已有 Inbox/Outbox/Session/Timeline 主链路。
- `EXTERNAL_HTTP` 派发类型和外部回调入口已存在。
- Runtime Hold 已能表达物理状态未知、通信接受状态未知、人工恢复和 NG 去向。
- 插件开发指南已明确插件不维护物料位置、不直接写 DB、不处理命令幂等和 Timeout。
- WMS/RCS 接口需求文档已有 WMS 库存真相源、RCS 由 WMS 调度、WES 按需查询主数据的边界。

#### CEO Completion Summary

| Item               | Status                  |
| ------------------ | ----------------------- |
| Strategic validity | 方向成立，但首版范围需收敛。          |
| Main blocker       | SRS/WMS/RCS 权责冲突必须前置解决。 |
| Recommended v1     | 满箱交换执行证据与对账模型。          |
| User challenge     | 是否接受将完整资源平台收敛为 v1 证据模型。 |
| Phase result       | 前提门禁已通过：平台目标保留，实施顺序需收敛。 |

### Decision Audit Trail

| #  | Phase | Decision                                                           | Classification | Principle            | Rationale                                                       | Rejected          |
| -- | ----- | ------------------------------------------------------------------ | -------------- | -------------------- | --------------------------------------------------------------- | ----------------- |
| 1  | CEO   | 采用 `SELECTIVE_EXPANSION` 评审模式                                      | Mechanical     | Bias toward action   | 当前文档是资源模型 spec，不是空白产品发现；应评审直接影响实施的架构边界。                         | 重新跑 office-hours。 |
| 2  | CEO   | Design 阶段暂不运行                                                      | Mechanical     | Explicit over clever | 文档没有前端交付面，UI 命中来自“看板”等远期词，不是本轮设计任务。                             | 把 BI/看板当 UI 计划评审。 |
| 3  | CEO   | DX 阶段保留                                                            | Mechanical     | Completeness         | 该 spec 会影响插件开发者、接口合同、sandbox 和测试路径，属于开发者体验范围。                   | 只做战略和工程评审。        |
| 4  | CEO   | 将 SRS 口径冲突升级为前置架构决策                                                | User Challenge | Explicit over clever | 两个独立声音都认为不能只作为风险备注，否则后续实现会引用不同权威。                               | 继续按当前 spec 直接实现。  |
| 5  | CEO   | 推荐 v1 收敛为满箱交换证据与对账模型                                               | User Challenge | Pragmatic            | 当前业务目标是满箱交换插件闭环，完整资源平台会拖慢交付并固化未确认合同。                            | v1 一次性落完整资源平台。    |
| 6  | CEO   | 推荐把最小对账能力前移                                                        | Mechanical     | Completeness         | active 关系写入前必须有冲突处理，否则脏状态会先于救援能力产生。                             | 第六阶段再做对账 API。     |
| 7  | CEO   | 推荐拆分 `PHYSICAL_COMPLETED` / `WMS_CONFIRMED` / `BUSINESS_COMPLETED` | Mechanical     | Explicit over clever | 库存确认和物理完成不是同一个事实，状态混用会误导调度。                                     | 单一 `COMPLETED`。   |
| 8  | CEO   | 用户确认资源模型必须服务整个 WES 运行时资源层                                          | User Override  | User context         | 用户明确要求覆盖 WorkLine、Device、Rack、Bin、Material 等资源，并支撑向 WMS 回写执行数据。 | 只做满箱交换局部模型。       |
| 9  | CEO   | 接受 ADR/SRS 修订前置                                                    | User Approved  | Explicit over clever | 先统一 WES 不越权锁空箱、不交换库存属性、不自动扣减库存的边界。                              | 在实现阶段再处理口径冲突。     |
| 10 | CEO   | 接受分析事实、生产/退料完整模型和 BI 投影后置                                          | User Approved  | Pragmatic            | 平台目标保留，但首版先完成运行时资源底座和回写证据。                                      | 首版同时交付完整分析平台。     |
| 11 | Domain | 区分货架槽位承载对象                                                      | User Fact      | Explicit over clever | 单层/五层货架槽位承载料箱，退料/运转卡槽货架槽位直接承载物料，必须对应不同当前投影。                 | 用 `RackBinMount` 覆盖所有货架槽位。 |

### Premise Gate Decision

用户已确认以下前提，后续 Eng/DX 评审按此执行：

1. 资源模型应服务整个 WES 的运行时资源事实层，覆盖 WorkLine、Device、Rack、Bin、Material 等执行资源，并支撑回调 WMS 做数据回写。
2. 接受先补 ADR/SRS 修订，明确 WES 不锁定五层空箱、不交换库存属性、不自动扣减库存；这些由 WMS 决策或确认。
3. 接受 `MaterialFlowRun/Event`、生产/退货货架完整模型和 BI 投影作为远期边界；首版先保留事件命名、统计口径和必要扩展点。

执行调整：

- 平台目标保留，不把资源模型降级为满箱交换私有模型。
- 实施顺序调整为：WES 运行时资源底座 -> 物理关系与资源事实账本 -> 最小对账 -> WMS 回写证据 -> 满箱交换闭环 -> 后续分析事实与跨流程扩展。
- 如后续 Eng/DX 评审发现边界或实现顺序仍有高风险，应再次中断并与用户确认。

### Phase 2: Engineering Review

#### Engineering Summary

工程评审接受用户确认的 WES-wide 资源平台目标，但要求把实施路径改为“先建运行时资源事实底座，再打满箱交换闭环”。当前文档不能直接进入代码实现，必须先通过 ADR/SRS、WMS/RCS 合同、Runtime 外部请求和最小对账四个工程门禁。

#### Existing Runtime Alignment

| 现有能力                                              | 约束                                                                    | 计划落点                                                                     |
| ------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `WorklineInbox` / `WorklineOutbox(EXTERNAL_HTTP)` | 已有回调与派发主链路，但外部请求不是插件私有写表行为。                                           | 满箱交换请求应通过 RuntimeIntent 生成 Outbox 和等待状态。                                 |
| `WorklineSession` / `Timeline`                    | Session 拥有等待、超时、重试、闭环和 trace。                                         | `FullBoxExchangeTask` 只做业务证据和查询镜像，不抢 Session 状态机。                        |
| `/callback/event` / `/callback/external`          | `/callback/event` 当前偏设备上下文；`/callback/external` 当前按 trace/session 归因。 | ADR 必须选择入口，并补 `exchange_request_code/dispatch_key/wms_rcs_task_id` 归因能力。 |
| `RuntimeHold`                                     | 已支持人工恢复、迟到 callback、物理状态未知等场景。                                        | 资源冲突、外部请求超时、WMS 断连必须创建或复用 RuntimeHold。                                   |
| 轻量 material runtime                               | 当前是运行态对象，不是持久统计平台。                                                    | 首版只保留 MaterialFlow 扩展点，完整分析事实后置。                                         |

#### Required Architecture Shape

```text
WorkLine / Device / WMS-RCS callback
    -> WorklineInbox
    -> Runtime / Plugin decision
    -> RuntimeIntent.external_request(...)
    -> WorklineOutbox(EXTERNAL_HTTP)
    -> WorklineSession(WAITING_EXTERNAL + deadline)
    -> ResourceStateEvent(append-only evidence)
    -> RackPlacement / RackBinMount / RackMaterialMount(current projection)
    -> RackRelease / FullBoxExchangeTask(process evidence)
    -> RuntimeHold / ResourceReconciliation
    -> WmsWritebackEvidence / WMS confirmation refs
```

#### Engineering Findings

| #  | 严重性      | 发现                                                           | 处理要求                                                                          |
| -- | -------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| 1  | Critical | ADR/SRS 边界不一致会让 WES 越权承担 WMS 资源授权或库存确认。                      | 第零阶段先修订 ADR/SRS，未通过不得建 migration。                                             |
| 2  | High     | `ResourceStateEvent`、`RackPlacement`、`RackBinMount` 的权威关系不清。 | 事件为 append-only 事实账本，placement/mount 为当前投影。                                   |
| 3  | High     | 回调入口和现有 callback 代码假设不一致。                                    | 在 ADR 中二选一：虚拟 `WMS_RCS` Device，或正式增强 `/callback/external`。                    |
| 4  | High     | 当前 RuntimeIntent 不足以表达满箱交换外部请求。                              | 新增 runtime-owned external request 意图，由 Runtime 创建 Outbox、Task、Timeline 和等待状态。 |
| 5  | High     | `WAITING_EXTERNAL`、deadline、timeout、late callback 规则缺失。      | 外部请求必须以 `dispatch_key/exchange_request_code` 作为 wait token。                   |
| 6  | High     | `COMPLETED` 混淆物理完成、资源投影和 WMS 确认。                             | 拆为 `PHYSICAL_COMPLETED/RESOURCE_PROJECTED/WMS_CONFIRMED/BUSINESS_COMPLETED`。  |
| 7  | High     | WMS 写回证据不足以证明账务已提交或确认。                                       | 增加 `WmsWritebackEvidence`，保存脱敏请求、响应、确认、失败和重试摘要。                               |
| 8  | High     | active placement/mount 并发冲突不能只靠唯一约束。                         | `ResourceRelationService` 在锁内先写事实再更新投影，冲突进入 `RECONCILING`。                    |
| 9  | Medium   | 乱序、迟到、重复回调缺少覆盖规则。                                            | 定义 `source_version`、`occurred_at/received_at`、来源优先级和幂等规则。                     |
| 10 | Medium   | `BinContentSnapshot` 单行模型不能表达多 PKG 和部分快照。                    | 拆为 header/item，并让释放快照引用 snapshot header。                                      |
| 11 | Medium   | 退料/运转卡槽货架直接承载物料，不能复用料箱挂载模型。                                | 增加 `RackMaterialMount`，并用 `slot_kind` 强制区分 `BIN_SLOT` 与 `MATERIAL_SLOT`。          |
| 12 | High     | `RackMaterialMount` 缺稳定物料身份和 WMS 拆分策略会导致重复 PKG 对账歧义。             | 增加 `material_identity_key`、`wms_split_policy`、`wms_inventory_version`，并把 WMS split/merge 合同作为门禁。 |
| 13 | Medium   | WMS/RCS 安全与重放防护未进入计划。                                        | 增加 HMAC/mTLS/IP allowlist、timestamp skew、request\_id 唯一和 canonical hash。      |
| 14 | Medium   | 事件和快照表高增长，索引和归档策略不足。                                         | 定义 trace/session/source/resource/time 索引，事件表按时间分区或归档。                         |

#### Engineering Test Plan Summary

- 合同测试：ADR/SRS 口径、WMS/RCS schema、签名、幂等、回调入口选择。
- 状态机测试：外部请求发起、`WAITING_EXTERNAL`、超时 RuntimeHold、late callback、`BUSINESS_COMPLETED` 条件。
- 事务测试：重复释放、乱序回调、并发 mount、容量冲突、人工修复后迟到回调。
- 集成测试：Inbox -> Runtime -> Outbox -> external callback -> ResourceStateEvent -> current projection -> WMS confirmation。
- 诊断测试：资源冲突、WMS 断连、缺少交换后关系、安全校验失败均有可查询证据。

#### Performance And Data Retention

| 对象                     | 必要索引或策略                                                                                                  |
| ---------------------- | -------------------------------------------------------------------------------------------------------- |
| `ResourceStateEvent`   | `resource_type/resource_code/occurred_at`、`trace_id/session_id`、`source_system/source_event_id`、时间分区或归档。 |
| `RackPlacement`        | active `rack_code` 唯一投影，`location_code + active` 容量查询索引。                                                 |
| `RackBinMount`         | active `rack_code + rack_slot_code` 唯一投影，active `bin_code` 唯一投影。                                         |
| `RackMaterialMount`    | active `rack_code + rack_slot_code` 唯一投影，`material_identity_key/wms_inventory_id/wms_inventory_version` 查询索引；`NOT_SPLITTABLE` 身份不得多 active。 |
| `FullBoxExchangeTask`  | `exchange_request_code`、`dispatch_key/outbox_id`、`wms_rcs_task_id`、`exchange_status`。                    |
| `WmsWritebackEvidence` | `idempotency_key`、`request_id`、`dispatch_key`、`wms_document_id`。                                         |

#### Engineering Gate

进入编码前必须满足：

1. ADR/SRS 边界已落文档，且本计划引用新边界。
2. WMS/RCS 回调入口和字段合同已确定，执行类回调统一走 `/api/v1/callback/external`。
3. Runtime 外部请求意图和 `WAITING_EXTERNAL` 规则已进入实施计划；代码实现仍需在第一批 Runtime 任务中完成。
4. 最小资源对账和 RuntimeHold 联动不再后置。
5. MaterialFlow、生产/退料完整模型、BI 投影只作为 later-scope backlog。

### Phase 3: DX Review

#### Developer Persona

目标开发者是 WES 后端和 WorkLine 插件工程师。他们需要在不破坏现有 Runtime、Callback、Device、WMS 边界的前提下，实现 `src/app/resource/`、SMT 满箱交换插件和 WMS/RCS 合同测试。

#### DX Findings

| 问题           | 影响                                                      | 文档要求                                                                                                                     |
| ------------ | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 首次阅读路径不清楚    | 开发者可能先建完整主数据或先做 BI，偏离满箱交换闭环。                            | 在计划开头标明实施顺序：ADR/SRS -> Runtime resource base -> event/projection -> reconciliation -> WMS evidence -> full-box exchange。 |
| 回调入口不明确      | 开发者会在 `/callback/event`、`/callback/external` 和设备回调之间分叉。 | 增加 ADR gate，不允许实现阶段自行选择入口。                                                                                               |
| 状态名语义过载      | 调试时无法判断物理动作、WES 投影、WMS 确认谁已完成。                          | 所有 API、日志、测试用例使用拆分状态名，不再用单一 `COMPLETED` 表达闭环。                                                                            |
| 资源冲突缺少本地演练方式 | 并发和迟到回调风险只能到现场暴露。                                       | 补 sandbox fixtures：重复释放、迟到 WMS 回调、交换后关系缺失、容量冲突、手工修复。                                                                     |
| WMS 回写证据不可见  | 开发者无法快速定位“已发、已收、已确认、已失败”。                               | Trace 查询和对账 API 必须展示 Outbox、callback、writeback evidence 和 ResourceStateEvent 链路。                                         |

#### Recommended Developer Journey

1. 阅读 ADR/SRS 修订，确认 WES/WMS/RCS 权责边界。
2. 阅读本 spec 的“推荐落地顺序”和“Engineering Gate”。
3. 先写资源事实账本和当前投影的模型/服务测试。
4. 再接 Runtime 外部请求和 callback 归因测试。
5. 最后接满箱交换插件 happy path、失败 path、对账 path。

#### Local Verification Expectations

- 提供最小 WMS/RCS mock 或 fixtures，能复现 `ACCEPTED -> QUEUED -> PHYSICAL_COMPLETED -> WMS_CONFIRMED`。
- 提供 sandbox 入口模拟外部结果，不要求连接真实 AGV/CTU。
- 每个关键失败场景都有可读诊断：错误码、trace\_id、session\_id、dispatch\_key、resource event、WMS evidence。
- 文档里的字段名、状态名、接口名必须和测试 fixture 保持一致。

#### DX Gate

DX 通过条件：

- 新开发者可以从文档判断第一批要做什么、不能做什么、哪些必须等 ADR/SRS。
- 不需要读完整 callback/runtime 源码也能知道 WMS/RCS 回调应该走哪条入口。
- 本地测试能覆盖一次完整满箱交换和至少三个失败恢复场景。
- 对账查询能从一个 `exchange_request_code` 追到 Session、Outbox、callback、ResourceStateEvent、当前投影和 WMS evidence。
