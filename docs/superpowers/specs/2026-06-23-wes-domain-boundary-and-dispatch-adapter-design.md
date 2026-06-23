---
status: Draft for review
created_at: 2026-06-23
scope: WES 顶层领域边界、WorkLine/Handling/Resource 职责、外部调度 Adapter
---

# WES 顶层领域边界与调度 Adapter 设计

## 核心结论

WMS 是货架、料箱、库位、库存规划和搬运调度决策的权威系统。WES 不维护货架/料箱/库位基础资料，只维护当前作业过程所需的运行投影、请求生命周期、设备事件和可追溯证据。

WES 内部按以下职责拆分：

- `workline`：工作线运行容器，拥有滚筒线、队列、入口/出口、设备角色、插件合同和会话运行状态。
- `handling`：搬运意图与请求生命周期，负责通过调度 Adapter 请求外部系统搬货架或料箱，并记录幂等、回调、失败和对账证据。
- `resource`：WES 运行投影，不是货架/料箱主数据；只表达当前作业线可见的货架位、料箱、格位、料盘占用事实和证据。
- `device/runtime`：本地设备事件、命令、执行结果回调和设备级运行证据。

调度层必须设计为 Adapter。首版 Adapter 通过 WMS 间接调度 AGV/CTU；后续可替换或扩展为直接调度 AGV/CTU，而不改 WorkLine、Handling 和插件的上层业务语义。

## 系统边界

| 对象 | 权威系统 | WES 是否维护基础资料 | WES 维护内容 |
| --- | --- | --- | --- |
| 货架 | WMS | 否 | 当前作业线上的货架投影、到位/移出 evidence |
| 料箱 | WMS | 否 | 当前作业线上的料箱投影、滚筒线队列 membership、搬运 evidence |
| 库位 | WMS | 否 | 外部引用编码、调度请求目标或回调 evidence |
| 货架位/工作位 | WES 配置 + WMS 回调 | 不作为 WMS 主数据复制 | WorkLine 内的可作业位置、入口/出口、运行投影 |
| 料盘/物料处理单元 | WES | 是，作业期根实体 | `material_units` 状态、当前位置、当前 Session |
| 滚筒线/队列 | WES | 是，属于 WorkLine 配置 | 队列声明、容量/排序策略、active membership |
| 设备 | WES 配置 + 设备运行时 | 是，作为接入点和角色绑定 | EVENT、COMMAND、RESULT、诊断状态 |
| AGV/CTU 调度能力 | Adapter 背后的调度系统 | 否 | 请求、幂等键、回调、失败和对账证据 |

## 领域模型

### WorkLine

`WorkLine` 是工作线运行容器，不是库存或搬运主数据系统。它下设：

- `ConveyorLine`：滚筒线/流水线，表示 WorkLine 内的物理输送系统。
- `PipelineQueue`：滚筒线上的逻辑队列，例如入口缓冲、扫码队列、工位活动区、出口队列、异常队列。
- `EntryPoint`：外部系统向滚筒线投入料箱的入口，例如 CTU 将料箱送入分拣机入口。
- `ExitPoint`：外部系统从滚筒线取走料箱的出口，例如 CTU 从分拣机出口取箱。
- `Device`：机械臂、扫码器、传感器、挡停器、滚筒线控制器等。

队列是动态配置，不允许建成系统级固定枚举。队列编码来自插件 manifest 或 WorkLine 运行配置，C0 后续应使用字符串 `queue_code` 加配置校验，而不是 `BinTransitQueue` enum。

### Conveyor Queue Membership

料箱在滚筒线队列中的当前位置应归 `workline`，不是 `handling`。

推荐投影命名：

- `WorklineQueueMembership`
- 或 `ConveyorQueueOccupancy`

投影表达：

- 哪个对象在队列中：`object_type=BIN|PLACEHOLDER`，`object_key=bin_code|placeholder_key`
- 属于哪条线：`workline_id/workline_code`
- 属于哪条滚筒线：`conveyor_code`
- 当前队列：`queue_code`，可附带 `queue_role` 快照
- 状态：`ACTIVE/LEFT/RECONCILING`
- 证据：`handling_operation_id/handling_move_id/trace_id/workline_session_id/evidence_json`

`HandlingMove` 可以触发队列变化，但不承载当前队列状态。队列变化统一写入 `object_transition_events`，用于 Trace、current activity 和后续 C1 强校验。

### Handling

`Handling` 是搬运意图与请求生命周期，不拥有 AGV、CTU、货架、料箱或队列的当前权威状态。

它负责：

- 接收插件或 WorkLine runtime 的搬运意图。
- 通过调度 Adapter 发起外部搬运请求。
- 维护请求幂等、状态、回调、超时、失败、重试、对账。
- 将外部回调转换为 WES evidence 或 resource/workline projection 更新。

它不负责：

- 维护货架/料箱基础资料。
- 决定 WMS 该选择哪一个货架或库位。
- 维护 AGV/CTU 实时状态。
- 维护滚筒线队列的 active membership。

### Resource Projection

`resource` 只维护 WES 运行投影，不复制 WMS 主数据。

允许维护：

- 当前工作位上看到的货架、料箱、格位、料盘占用。
- 由 WMS 回调、设备事件、扫码、机械臂动作推导出的 active projection。
- `ResourceStateEvent` 和 projection transition evidence。
- 与 WES 自有实体的关联，例如 `workline_session_id`、`material_unit_id`。

不允许维护：

- 货架主数据表。
- 料箱主数据表。
- 库位主数据表。
- 以本地 FK 约束外部货架/料箱/库位编码。

因此 C0 中“active projection 默认补 SQL FK”的规则必须收敛为：只对 WES 自有实体补 FK；外部对象使用 `rack_code/bin_code/location_code` 等外部引用编码和 evidence 校验。

### Device Runtime

设备负责两类交互：

- 上报 `EVENT`：扫码完成、到位信号、传感器信号、异常事件等。
- 接收 `COMMAND` 并回调 `RESULT`：机械臂取放、滚筒线动作、扫码触发、挡停控制等。

设备事件和命令结果是 WorkLine runtime 的输入证据，不应被建成库存事实或搬运主数据。

## WorkLine 平面态势图

当前阶段只做 **WorkLine 平面态势图**，不做完整数字孪生界面。

目标是让前端能用一个最小平面视图展示某条 WorkLine 的滚筒线、队列、入口/出口、设备、当前料箱/料盘和搬运中对象。它是运行态展示能力，不是新的配置事实源。

### 范围边界

本阶段做：

- 平面展示，不做 3D。
- 单条 WorkLine 视角，不做全仓库可视化。
- manifest 派生场景，不另建 `TwinSceneDefinition` 配置源。
- 当前态快照优先，实时事件流可后续补。
- 简单布局 hint，支持前端先画出可读的线体态势。

本阶段不做：

- 不做布局编辑器。
- 不做复杂动画。
- 不做完整历史回放。
- 不展示 WMS 全局库存、全仓货架路径或 AGV/CTU 调度细节。
- 不让前端直接理解插件 YAML 或自行拼 `session/context/resource/handling` 散表。

### Manifest 是平面场景真源

`WorkLine` 的 manifest 是拓扑、能力、队列、设备角色和资源边界的配置真源。平面态势图只消费后端派生后的 `PlaneSceneView`。

```text
WorkLine manifest + WorkLine runtime_config_json/diagnostic_profile
  -> PlaneSceneView
  -> 前端平面态势图
```

`PlaneSceneView` 不拥有业务状态，也不替代 manifest。它只是把 manifest/config 转成前端更容易消费的节点、边、队列和布局 hint。

### 当前 manifest 缺口

当前 `WorklinePluginManifest` 已有：

- `device_roles`
- `rack_positions`
- `topology.flow_edges`
- `resource_boundaries`
- `session_subject`
- `state_machines`
- `pipeline_queues`

但它还不能直接派生最小平面态势图，因为缺少：

- `conveyor_lines`：滚筒线声明。
- `entry_points` / `exit_points`：外部投箱/取箱接口。
- 队列与滚筒线的归属关系，例如 `pipeline_queues[].conveyor_code`。
- 队列与拓扑节点的关系，例如入口队列连接哪个 entry point、工作队列对应哪个 device role。
- 最小布局 hint，例如 `row/col/order/label/icon_hint`。

因此后续 manifest 扩展应补这些字段，但仍保持 manifest 作为唯一配置真源。

### 最小 Manifest 扩展示意

规划口径如下，具体字段名可在实施计划中收敛：

```yaml
conveyor_lines:
  - code: MAIN_CONVEYOR
    label: 主滚筒线
    layout:
      row: 1
      col: 1
      direction: LEFT_TO_RIGHT

entry_points:
  - code: CTU_INFEED
    conveyor_code: MAIN_CONVEYOR
    queue_code: INFEED_BUFFER_QUEUE
    external_handler: CTU
    layout:
      order: 10

exit_points:
  - code: CTU_OUTFEED
    conveyor_code: MAIN_CONVEYOR
    queue_code: EXIT_ROUTING_SCAN_QUEUE
    external_handler: CTU
    layout:
      order: 90

pipeline_queues:
  - code: INFEED_BUFFER_QUEUE
    conveyor_code: MAIN_CONVEYOR
    role: BUFFER
    capacity: MANY
    order_policy: FIFO
    layout:
      order: 20
      label: 入料缓存
```

`layout` 只提供最小平面 hint。现场坐标、显示名称或折叠策略可由 `WorkLine.runtime_config_json` 或 `diagnostic_profile` 覆盖，但覆盖层不能新增业务事实。

### PlaneSceneView

推荐后端派生读模型：

```text
PlaneSceneView
  workline_id
  workline_code
  nodes[]
    node_id
    node_type = CONVEYOR | QUEUE | ENTRY_POINT | EXIT_POINT | DEVICE | RACK_POSITION
    ref_code
    label
    role
    layout
    capacity
    order_policy
  edges[]
    from_node_id
    to_node_id
    edge_type = MATERIAL_FLOW | OPERATION | QUEUE_FLOW | EXTERNAL_TRANSFER
  warnings[]
    code
    message
    evidence
```

`PlaneSceneView` 的节点来自 manifest；当前状态不放在 scene 内，避免 scene 和 snapshot 双写。

### PlaneSnapshot

推荐后端派生当前态快照：

```text
PlaneSnapshot
  workline_id
  active_material_units[]
  active_bins[]
  queue_memberships[]
  devices[]
  resource_projections[]
  in_transfer[]
  conflicts[]
```

来源对应：

- `active_material_units`：`material_units` + active `workline_sessions`
- `active_bins` / `queue_memberships`：WorkLine queue membership
- `devices`：device/runtime 诊断状态和最近 event/result
- `resource_projections`：resource active projection
- `in_transfer`：non-terminal handling/dispatch request
- `conflicts`：投影冲突、重复 active、location drift、RECONCILING evidence

### 最小 API

当前阶段可只提供一个聚合接口：

```text
GET /worklines/{id}/plane
  -> scene + snapshot
```

后续需要性能或实时刷新时，再拆分为：

```text
GET /worklines/{id}/plane/scene
GET /worklines/{id}/plane/snapshot
GET /worklines/{id}/plane/events
```

`events` 可以后续基于 `object_transition_events`、device event/result 和 handling request status changes 做 SSE/WebSocket；当前阶段不强制实现。

## WorkLine 当前作业对象

顶层设计必须能回答：“某一条 WorkLine 上正在工作的料箱和料盘有哪些？”

答案不来自 WMS 主数据，也不来自单一业务表，而是一个派生读模型。推荐命名为 `WorklineActiveObjects` 或 `WorklineCurrentWorkView`。它只做查询拼装，不成为新的状态源。

### 查询口径

| 查询对象 | 权威来源 | 含义 |
| --- | --- | --- |
| 正在处理的料盘 | `material_units` + active `workline_sessions` | 当前 WorkLine 正在处理的 WES 料盘根实体 |
| 滚筒线内的料箱 | `WorklineQueueMembership` / `ConveyorQueueOccupancy` | 当前 active 于某个滚筒线队列的料箱或 placeholder |
| 工作位上的料箱/货架 | `resource` active projection | 当前 WorkLine 工作位可见的外部对象投影，不是 WMS 主数据 |
| 正在外部搬运中的对象 | non-terminal `HandlingOperation` / dispatch request | 已请求 WMS/CTU/AGV 搬运，尚未完成并落回投影的对象 |

### “正在工作的料盘”

料盘由 WES 拥有根实体。某条 WorkLine 当前正在工作的料盘应按以下顺序查询：

1. 找到该 WorkLine 下未终结的 `WorklineSession`。
2. 通过 session 的当前料盘引用或 `material_units.current_session_id` 找到 `material_units`。
3. 仅返回未终结的料盘状态，例如 `IN_TRANSIT`、`STORED`、`NG`、`RECONCILING`；`COMPLETED` 默认不算正在工作，除非查询显式要求包含刚完成未归档对象。

该结果回答“WES 当前正在处理哪些料盘”，不回答“WMS 库存中有哪些料盘”。

### “正在工作的料箱”

料箱基础资料不在 WES 内。某条 WorkLine 当前相关的料箱需要分三类展示：

| 分类 | 查询来源 | 示例 |
| --- | --- | --- |
| `ON_CONVEYOR` | active queue membership | 料箱在入口队列、扫码队列、工位队列、出口队列 |
| `AT_WORK_POSITION` | resource active projection | 料箱位于当前 WorkLine 工作位货架或格位 |
| `IN_TRANSFER` | non-terminal handling/dispatch request | CTU 正在把料箱送入入口或从出口取回 |

其中 `ON_CONVEYOR` 是 WorkLine 的队列投影权威；`AT_WORK_POSITION` 是 WES 运行投影；`IN_TRANSFER` 是外部搬运请求状态。三者可以合并展示，但必须保留分类，避免把“正在搬运中”误读成“已经在滚筒线内”。

### 读模型字段

推荐读模型输出：

```text
workline_id
workline_code
active_material_units[]
  material_unit_id
  pkg_code
  status
  current_location
  workline_session_id
  trace_id
active_bins[]
  bin_code 或 placeholder_key
  presence_type = ON_CONVEYOR | AT_WORK_POSITION | IN_TRANSFER
  conveyor_code
  queue_code
  queue_role
  work_position_code
  dispatch_request_id
  handling_operation_id
  workline_session_id
  trace_id
  evidence_json
```

该读模型的排序建议：

1. `ON_CONVEYOR` 按 queue order 和 entered_at 排序。
2. `AT_WORK_POSITION` 按工作位配置顺序排序。
3. `IN_TRANSFER` 按请求创建时间排序。

### 一致性要求

- `WorklineActiveObjects` 不写业务状态，只拼装已有状态源。
- 如果同一 `bin_code` 同时出现在 `ON_CONVEYOR` 和 `AT_WORK_POSITION`，应返回冲突 evidence，并触发 RECONCILING 或诊断。
- 如果同一料盘有多个 active session，应返回冲突 evidence，并触发 RECONCILING 或诊断。
- 查询结果必须暴露 `trace_id` 和 evidence，方便从当前态追溯到设备事件、WMS 回调或 handling 请求。

## 物料定位查询

顶层设计还必须能回答：“某个物料或某类物料现在在哪里？”

本系统只回答 **WES 作业期定位**：查询 WES 已扫码、正在处理、暂存、NG 或刚完成的料盘/物料处理单元。

WMS 全局库存定位不在本系统规划内。WES 可以保留外部引用和请求证据，但不规划 WMS 应提供哪些查询能力，也不能用本地投影冒充 WMS 全局库存。

### 查询入口

推荐提供 `MaterialLocationQuery` 读模型，支持以下入口：

| 查询入口 | 示例 | WES 回答范围 |
| --- | --- | --- |
| 单盘物理唯一 ID | `pkg_code` | 精确查某一盘料 |
| 物料属性键 | `material_identity_key` | 查某一类物料，例如料号+供应商+日期+批次 |
| 料号/批次组合 | `material_code/lot_code/date_code/vendor_code` | 查一类物料，按已知字段过滤 |
| 当前 WorkLine | `workline_id/workline_code` | 限定只查某条线当前相关物料 |
| 状态 | `IN_TRANSIT/STORED/NG/COMPLETED/RECONCILING` | 限定作业期状态 |

### 位置来源

WES 内部查询按优先级拼装位置：

1. `material_units.current_location`：料盘当前业务位置摘要，适合快速回答。
2. `resource` active projection：如果料盘在料箱格位内，以 `BinMaterialMount/BinCellOccupancy` 为格位容量和放置 evidence。
3. `WorklineQueueMembership`：如果料盘随料箱在滚筒线中，位置应显示为料箱所在 `queue_code`，并关联 `bin_code`。
4. non-terminal `HandlingOperation` / dispatch request：如果料盘或承载它的料箱/货架正在外部搬运中，位置显示为 `IN_TRANSFER`，并给出 source/target 和 request 状态。
5. `object_transition_events` / `ResourceStateEvent`：用于追溯位置变化历史，不作为当前态第一来源。

### 读模型字段

推荐输出：

```text
query_scope = WES_ACTIVE
items[]
  material_unit_id
  pkg_code
  material_identity_key
  material_code
  vendor_code
  lot_code
  date_code
  status
  location_type = WORKLINE_QUEUE | BIN_CELL | WORK_POSITION | IN_TRANSFER | NG_AREA | COMPLETED | UNKNOWN
  location_code
  workline_id
  workline_code
  workline_session_id
  bin_code
  cell_code
  conveyor_code
  queue_code
  handling_operation_id
  dispatch_request_id
  trace_id
  evidence_json
```

### 查询语义

- 查 `pkg_code`：返回一条当前 WES 作业期记录；如果不存在，返回 `NOT_IN_WES_ACTIVE_SCOPE`。
- 查 `material_identity_key` 或物料属性组合：返回 WES 当前知道的多条料盘记录，默认不代表 WMS 全部库存。
- 查 WorkLine 上某类物料：在 `MaterialLocationQuery` 上叠加 `workline_id` 过滤，结果只包含当前 WorkLine 相关 active/in-transfer 对象。
- 查全局库存位置：不属于本系统查询能力。调用方需要依赖外部 WMS 能力时，应走独立集成需求和外部合同，不纳入本设计。

### 冲突和降级

- 如果 `material_units.current_location` 与 resource active projection 不一致，返回冲突 evidence，并触发 material location drift 诊断。
- 如果同一 `pkg_code` 有多个 active mount 或多个 active session，返回 `RECONCILING` 视图，不静默选择一个。
- WES 查询结果只能声明本地作业期范围，不能降级表达 WMS 全局库存。

## 调度 Adapter

### 设计目标

调度调用方只依赖 WES 内部 port，不直接依赖 WMS HTTP client 或未来 AGV/CTU SDK。

首版实现：

```text
Handling / WorkLine Runtime
  -> DispatchAdapter Port
  -> ExternalDispatchAdapter
  -> 外部 WMS 调度接口
```

后续可扩展：

```text
Handling / WorkLine Runtime
  -> DispatchAdapter Port
  -> DirectAgvAdapter / DirectCtuAdapter / HybridDispatchRouter
  -> AGV/CTU 调度系统
```

Adapter 替换后，上层仍提交相同的搬运意图和幂等键，下层负责转换成目标系统协议。

### 标准调度意图

| 意图 | 当前执行方式 | 说明 |
| --- | --- | --- |
| `SUPPLY_EMPTY_SINGLE_LAYER_RACK` | 请求外部 WMS 调度接口 | 为粗分机补充带空料箱的单层货架 |
| `REMOVE_LOADED_SINGLE_LAYER_RACK` | 请求外部 WMS 调度接口 | 将粗分机上已载有料盘/物料的单层货架移出 |
| `POSITION_FIVE_LAYER_RACK` | 请求外部 WMS 调度接口 | 将五层货架从原料仓移动到分拣机工作位，或从工作位移出 |
| `CHANGE_RACK_FACE` | 请求外部 WMS 调度接口 | 请求货架原地换面 |
| `MOVE_BIN_TO_CONVEYOR_ENTRY` | 请求外部 WMS 调度接口 | 从工作位货架取指定料箱，送入分拣机入口 |
| `MOVE_BIN_FROM_CONVEYOR_EXIT` | 请求外部 WMS 调度接口 | 从分拣机出口取料箱，送回工作位货架指定位置 |

WES 只定义本系统侧的调度意图、请求字段、幂等证据、回调接收和状态处理。外部 WMS 如何选择货架、计算箱位、规划库位或调度 AGV/CTU，不在本系统规划内。

### 请求生命周期

推荐统一状态：

```text
REQUESTED -> SENT -> ACCEPTED -> RUNNING -> SUCCEEDED
                         │          │
                         │          └── FAILED
                         └── REJECTED

任意非终态 -> CANCELLED
不可信/证据冲突 -> RECONCILING
```

状态含义：

- `REQUESTED`：WES 已生成搬运意图，尚未成功发出。
- `SENT`：已调用 Adapter，下游响应未定。
- `ACCEPTED`：下游接受请求。
- `RUNNING`：下游已开始执行。
- `SUCCEEDED`：下游确认完成。
- `REJECTED`：下游业务拒绝，例如无可用货架、无空箱位、目标不合法。
- `FAILED`：执行失败、超时或技术错误。
- `CANCELLED`：WES 或下游取消。
- `RECONCILING`：WES evidence、WMS 回调或现场投影冲突，状态不可信。

### Adapter 合同约束

- 所有请求必须带 `idempotency_key`。
- 所有请求和回调必须带 `trace_id` 或可关联的 correlation key。
- Adapter 返回值必须区分业务拒绝、技术失败和已接收异步执行。
- WES 不从 Adapter 响应中推断库存真相，只更新请求状态和运行投影 evidence。
- 外部调度 Adapter 与未来直连 Adapter 的差异只能存在于协议转换层，不向 WorkLine 插件泄漏。

## 典型流程

### 粗分机补空单层货架

```text
WorkLine 发现粗分机需要空箱资源
  -> Handling 创建 SUPPLY_EMPTY_SINGLE_LAYER_RACK 请求
  -> DispatchAdapter 调 WMS
  -> 外部 WMS 调度接口异步处理
  -> WES 接收外部到位回调
  -> Resource Projection 记录当前工作位货架/料箱投影
  -> WorkLine 继续扫码、取放、投箱流程
```

### 分拣机入口投箱

```text
WorkLine 需要目标料箱进入分拣机
  -> Handling 创建 MOVE_BIN_TO_CONVEYOR_ENTRY 请求
  -> DispatchAdapter 调 WMS
  -> 外部 WMS 调度接口异步处理
  -> WES 接收外部完成回调或入口设备事件
  -> WorkLine QueueMembership 进入入口队列
  -> ObjectTransitionEvent 记录队列变化
```

### 分拣机出口取箱回货架

```text
Conveyor QueueMembership 到达 ExitPoint
  -> Handling 创建 MOVE_BIN_FROM_CONVEYOR_EXIT 请求
  -> DispatchAdapter 调 WMS
  -> 外部 WMS 调度接口异步处理
  -> WES 接收外部完成回调
  -> QueueMembership 离开滚筒线
  -> Resource Projection 更新工作位料箱/格位投影 evidence
```

## 对当前 C0 设计的修正

1. `BinTransitQueue` 不应存在为系统级 enum。队列编码应来自 WorkLine/插件配置。
2. `BinTransitMembership` 不应归 `handling`。队列 active/history 投影应迁到 `workline`，并重命名为 `WorklineQueueMembership` 或 `ConveyorQueueOccupancy`。
3. `Handling queue membership transition event` 应改为 `WorkLine conveyor queue membership transition event`。
4. `Resource Projection` 的 FK 策略必须区分 WES 自有实体和 WMS 外部对象；不能给货架/料箱/库位主数据补本地 FK。
5. `HandlingOperation/Move/Step` 应调整为外部搬运请求和执行证据，不再暗含 AGV/CTU/货架/料箱状态所有权。
6. 调度能力必须先通过 `DispatchAdapter` port 接入 WMS，后续直连 AGV/CTU 通过 Adapter 实现替换。
7. WorkLine 当前作业对象必须通过 `WorklineActiveObjects` 读模型回答，不能让前端或调用方自行拼 `session/context/resource/handling` 散表。
8. 物料位置查询只回答 WES 作业期定位；WMS 全局库存定位不在本系统规划内，不能用 WES active projection 冒充全局库存事实。
9. 当前阶段只做 WorkLine 平面态势图。`PlaneSceneView` 必须由 manifest/config 派生，不能另建与 manifest 平行的 twin scene 配置事实源。

## 后续任务

这些任务不直接塞入当前顶层设计实现，需进入 TODO 或后续计划：

- 重写 C0 计划文档，替换 `BinTransitMembership` 为 WorkLine conveyor queue projection。
- 删除或迁移当前 handling 下的 `BinTransitQueue/BinTransitMembership` 模型、repository、service、migration 和测试。
- 新增 `DispatchAdapter` port 与外部 WMS 调度 Adapter，把货架/料箱搬运请求统一从 Adapter 发出；只规划 WES 侧 port 和 evidence，不规划 WMS 内部能力。
- 重新审计 `resource` FK 设计，只保留 WES 自有实体 FK，外部对象使用引用编码和 evidence 校验。
- 为滚筒线补 `ConveyorLine/EntryPoint/ExitPoint` 配置合同，先从 manifest 支持，必要时再落库。
- 为未来直连 AGV/CTU 增加 Adapter 能力矩阵和混合路由策略。
- 新增 `WorklineActiveObjects` / `WorklineCurrentWorkView` 查询服务，用统一读模型回答某条 WorkLine 当前正在工作的料箱和料盘。
- 新增 `MaterialLocationQuery` 查询服务，统一回答某个或某类物料在 WES 作业期的位置。
- 扩展 manifest 平面展示最小合同：`conveyor_lines`、`entry_points`、`exit_points`、queue-to-conveyor binding 和 layout hints。
- 新增 `GET /worklines/{id}/plane` 聚合接口，返回 manifest 派生的 `PlaneSceneView` 与当前态 `PlaneSnapshot`。
