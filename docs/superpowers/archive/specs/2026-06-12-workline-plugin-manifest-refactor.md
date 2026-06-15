# Workline Plugin Manifest 重构

> **日期**: 2026-06-12
> **状态**: 已实现并归档；后续 `RackPosition` 公开命名由 2026-06-14 rename 计划继续收口
> **类型**: 破坏性重构（未发布系统）
> **影响范围**: 后端插件系统 + 前端类型定义

---

## Context

当前 `WorklinePluginManifest` 混合了契约数据和实现代码，导致：
1. **不可序列化**：callable 字段无法通过 API 完整返回给前端
2. **运行时入口分散**：business key、result classifier、material identity、NG reason、context model 和 event source 的调用路径混在 manifest 上
3. **拓扑隐含**：设备、货位、命令和物料流之间的关系需要从 `event_source_roles` / `command_target_roles` / `single_layer_boundaries` 间接推导
4. **资源边界语义被误认为拓扑字段**：单层货架的 WMS operation、snapshot、lease 等元数据不能从 position role 安全推导

本次重构从第一性原理出发，将 manifest 回归为**纯数据契约**。

> 2026-06-15 归档审计：本 SPEC 的后端/前端合同、测试清理和模板同步已由 `aeeb9d48 v0.6.1.0 feat(workline): 重构插件 manifest 合同 (#33)` 与 `abe0e750 v0.6.2.0 fix(workline): 重命名 manifest 货架位合同 (#34)` 落地。下方任务勾选按当前实现、focused backend pytest、frontend Vitest/type/contract test 和清理门禁确认。

---

## 设计决策

| # | 决策 | 理由 |
|---|------|------|
| 1 | Manifest = 纯数据契约，代码归 Plugin 类 | callable 不可序列化，应属于实现 |
| 2 | 拓扑显式声明 `flow_edges`，节点使用 typed `NodeRef` | 前端和后端都不能靠裸字符串判断设备角色还是货位 |
| 3 | 货位是独立拓扑节点 | 货位是物料流转的位置，不是设备属性 |
| 4 | 命令绑定用 `CommandBinding(command, target_device_role, position_args[])` 声明 | 命令只在涉及 WES 货架位/资源位置时声明 `PositionArg`；硬件闭环内部点位留在 command payload 或插件业务逻辑 |
| 5 | 货位之间的物料流动用 `flow_edges` 显式声明 | 前端可直接消费，无需推导 |
| 6 | 触发约束不放在 manifest 里 | 属于业务逻辑，不是契约 |
| 7 | 事件能力用 `EventBinding(event, source_device_roles, category, payload_schema_ref?)` 声明 | 事件来源是静态能力合同，前端和后端校验都应直接消费纯数据 |
| 8 | registry 统一提供 plugin singleton/helper | callable、`context_model`、material identity、NG reason 从 registry helper 进入；event source 留在 `EventBinding` 静态合同 |
| 9 | 设备 EVENT/COMMAND 能力从 `EventBinding` / `CommandBinding` 派生 | 避免在 device capability 和 event/command binding 中重复维护同一事实 |
| 10 | rack 资源编排元数据放入独立 `resource_boundaries` | rack kind、WMS operation、snapshot kind、lease scope 等不能从 topology/position 安全推导 |
| 11 | `ResourceBoundary.position_code` 必须在 `positions` 中可解析 | 引用完整性校验，防止运行时断裂 |
| 12 | `NodeRef.ref` 必须在 devices/positions 中可解析 | typed 引用的核心价值就是可验证 |
| 13 | `FlowEdge.type` 是枚举（MATERIAL_FLOW / OPERATION） | 前端可安全消费，不支持自定义类型 |
| 14 | `ResourceBoundary` 不重复保存 `station_code` / `station_role` | 资源边界以 `position_code` 锚定，站点信息从 `Position` 派生，避免同一事实写两份 |
| 15 | 不保留旧 manifest 兼容层 | 系统未发布，破坏性重构后应删除旧字段、旧导出、旧测试和旧模板，保持代码干净 |
| 16 | 插件实例生命周期只归 registry 管理 | 删除旧 `_plugin_instance_cache`，所有 runtime/helper 通过 `WorklinePluginDefinition.plugin_instance` 获取实例 |
| 17 | 设备是操作工位，货位是承载工位 | 设备负责过站、发事件和执行命令；货位是稳定承载位置，不随货架流转改变身份 |
| 18 | Manifest 只描述静态拓扑，运行时承载状态按 `position_code` 覆盖 | 当前货架、箱体、物料、占用状态属于运行时投影，不能写进静态 manifest |
| 19 | 货位承载能力用 `PositionCarrierCapability` 结构化声明 | 单层/五层货架、容量约束和槽位约束是插件要求，不应塞进泛化字符串能力 |
| 20 | `topology` 是必填字段 | manifest 必须能完整渲染设备/货位拓扑，不允许空拓扑回退到旧隐式推导 |
| 21 | Manifest 保存承载约束，Workline 配置保存实际值 | 避免 `PositionCarrierCapability` 与 `WorklineRackPosition.allowed_rack_kind/capacity` 双写同一事实 |
| 22 | 动作位置来源用 `PositionArgSource` 结构化声明 | 避免 `runtime_source` 字符串黑盒，明确 SOURCE/TARGET/WORK/BIN/NG 从事件、上下文、命令 payload 或资源投影取得 |
| 23 | `EventBinding.category` 使用 `EventCategory` 受控枚举 | 区分入口设备事件、内部事件、命令结果、人工事件和安全事件，避免事件过滤继续靠命名推导 |
| 24 | 命令结果能力用 `CommandResultBinding` 集合声明 | 一个命令可能有成功、失败、业务拒绝、硬件失败、超时等结果路径，不能压成单个 `result_event` |
| 25 | 静态 manifest 与运行时 overlay 分离 | manifest 按 `plugin_key + contract_version` 缓存；runtime polling 只返回当前货架/箱体/物料/占用状态 |
| 26 | `WorkLinePluginOption` 只做插件选择摘要 | 完整 devices/events/commands/topology 能力统一从 manifest 详情读取，避免 options API 继续暴露旧字段或双写合同事实 |
| 27 | `PositionArg.position_ref` 与 `PositionArg.source` 互斥 | 固定位置和运行时解析路径不能双写；`PositionArgSource.kind` 不提供 `STATIC` |
| 28 | `positions` 只声明 WES 管理的货架停靠位/库存事实锚点 | 扫码台、输送线内部点、机器人中转位属于设备/硬件闭环，不进入资源拓扑；`MATERIAL_FLOW` 只表达货架位之间的库存/物料流 |

---

## 当前状态

### 字段清单（16 个）

| 字段 | 类型 | 问题 |
|------|------|------|
| `plugin_key` | `str` | ✅ 保留 |
| `contract_version` | `str` | ✅ 保留 |
| `required_device_roles` | `tuple[DeviceRoleRequirement, ...]` | ⚠️ 重命名为 `devices`，仅保留 role/count/硬件约束 |
| `business_key_resolver` | `Callable` | ❌ 移除 → Plugin 类 |
| `result_classifier` | `Callable` | ❌ 移除 → Plugin 类 |
| `context_model` | `type` | ❌ 移除 → Plugin 类 + registry helper |
| `event_source_roles` | `Mapping` | ❌ 移除 → `events: list[EventBinding]` |
| `command_target_roles` | `Mapping` | ❌ 替换为 `commands: list[CommandBinding]` |
| `supported_events` | `frozenset[str]` | ⚠️ 保留语义，改为结构化 `events: list[EventBinding]` |
| `supported_commands` | `frozenset[str]` | ⚠️ 保留语义，改为结构化 `commands` |
| `capabilities` | `frozenset[str]` | ❌ 插件级隐式推导移除；设备 EVENT/COMMAND 能力从 bindings 派生，货位承载能力放入 `PositionCarrierCapability` |
| `resource_kinds` | `frozenset[str]` | ❌ 移除 → `resource_boundaries.rack_kind` |
| `requires_single_layer_boundary` | `bool` | ❌ 移除 → 是否声明 `resource_boundaries` |
| `single_layer_boundaries` | `Sequence[SingleLayerRackBoundary]` | ⚠️ 拆分为 `positions` + `resource_boundaries` |
| `material_identity_resolver` | `Callable` | ❌ 移除 → Plugin 类 + registry helper |
| `ng_reason_catalog` | `Sequence[NgReasonDefinition]` | ❌ 移除 → Plugin 类 + registry helper |

### 旧 `SingleLayerRackBoundary` 字段分析（8 个）

| 字段 | 最终归属 | 说明 |
|------|----------|------|
| `station_code` | `Position.station_code` | 资源边界通过 `position_code` 派生 station 标识，不重复保存 |
| `position_code` | `Position.code` + `ResourceBoundary.position_code` | 拓扑节点和资源边界都以 position 为锚点 |
| `rack_kind` | `ResourceBoundary.rack_kind` | 不能从 position role 推导，保留显式声明 |
| `station_role` | `Position.role` | 资源边界通过 `position_code` 派生 station role，不重复保存 |
| `business_demand_type` | `ResourceBoundary.business_demand_type` | WMS/资源编排合同，不能删除 |
| `wms_operation_type` | `ResourceBoundary.wms_operation_type` | WMS 合同，不能删除 |
| `snapshot_kind` | `ResourceBoundary.snapshot_kind` | runtime active snapshot 查询需要 |
| `lease_scope` | `ResourceBoundary.lease_scope` | station lease 语义需要 |

**结论**：`SingleLayerRackBoundary` 不再作为 manifest 顶层对象存在，只作为旧合同迁移来源；新 `resource_boundaries` 是通用 rack resource boundary，必须能按 `rack_kind` 覆盖 `SINGLE_LAYER`、`FIVE_LAYER` 和未来 rack kind 的 WMS/snapshot/lease 编排语义。

---

## Proposed Change

### 新 Manifest 结构

| 字段 | 类型 | 语义 |
|------|------|------|
| `plugin_key` | `str` | 插件标识 |
| `contract_version` | `str` | 插件合同版本 |
| `devices` | `list[DeviceRequirement]` | 操作工位角色、数量和硬件约束 |
| `positions` | `list[Position]` | WES 管理的货架停靠位/库存事实锚点和承载能力 |
| `topology` | `TopologySpec` | 必填；typed `NodeRef` 组成的物料/操作流 |
| `commands` | `list[CommandBinding]` | command 到操作设备角色的绑定，包含结构化位置参数和结果路径 |
| `events` | `list[EventBinding]` | event 到来源设备角色的绑定 |
| `resource_boundaries` | `list[ResourceBoundary]` | rack/WMS/snapshot/lease 等资源编排边界 |

### 数据结构合同

| 结构 | 字段 | 约定 |
|------|------|------|
| `DeviceRequirement` | `role`, `min_count`, `max_count`, `hardware_capabilities` | 设备是操作工位；不重复声明 EVENT/COMMAND，事件和命令能力从 bindings 派生 |
| `Position` | `code`, `role`, `station_code`, `carrier_capability` | 货架停靠位是库存事实、资源边界和 runtime overlay 的锚点；扫码台、传感器、输送线内部点和机器人临时中转位不得声明为 `Position` |
| `PositionCarrierCapability` | `allowed_rack_kinds`, `min_capacity`, `max_capacity`, `allowed_slot_kinds` | 声明插件对货位承载对象的约束；实际 `allowed_rack_kind` / `capacity` 仍由 Workline 现场配置保存 |
| `NodeRef` | `kind`, `ref` | `kind` 仅允许 `DEVICE_ROLE` 或 `POSITION`；`ref` 必须能在 devices/positions 中解析 |
| `FlowEdge` | `from_node`, `to_node`, `type` | `from_node`/`to_node` 使用 `NodeRef`；`type` 仅允许 `MATERIAL_FLOW` 或 `OPERATION`；`MATERIAL_FLOW` 必须是 `POSITION -> POSITION`，设备参与货架位动作只能用 `OPERATION` |
| `EventBinding` | `event`, `source_device_roles`, `category`, `payload_schema_ref` | 定义事件来源和事件类别；source role 必须在 `devices` 中存在；`category` 必须使用 `EventCategory` |
| `EventCategory` | `ENTRY_DEVICE`, `INTERNAL`, `COMMAND_RESULT`, `OPERATOR`, `SAFETY` | 入口过滤只消费 `ENTRY_DEVICE`；内部驱动、命令结果、人工和安全事件不得混入 entry admission |
| `CommandBinding` | `command`, `target_device_role`, `position_args`, `payload_schema_ref`, `result_bindings` | 定义命令目标设备、动作参数和结果路径；target role 必须在 `devices` 中存在 |
| `CommandResultBinding` | `result`, `event`, `category`, `classification`, `terminal`, `next_event` | `category` 必须为 `COMMAND_RESULT`；表达 SUCCESS/FAILED/ERROR/TIMEOUT/业务分类与后续事件 |
| `PositionArg` | `name`, `role`, `required`, `position_ref`, `source` | 定义命令所需位置参数；role 允许 `SOURCE` / `TARGET` / `WORK` / `BIN` / `NG` 等受控枚举；`position_ref` 表达固定位置，`source` 表达运行时来源；required 参数必须二选一，optional 参数可不填，但任何参数都不得同时设置二者 |
| `PositionArgSource` | `kind`, `path`, `fallback_position_ref` | `kind` 允许 `EVENT_PAYLOAD` / `SESSION_CONTEXT` / `COMMAND_PAYLOAD` / `RESOURCE_OVERLAY`；不提供 `STATIC`，静态固定位置统一走 `PositionArg.position_ref`；`path` 为受校验字段路径 |
| `ResourceBoundary` | `position_code`, `rack_kind`, `business_demand_type`, `wms_operation_type`, `snapshot_kind`, `lease_scope` | 通用 rack resource boundary；通过 `position_code` 引用 `Position` 并派生 station 信息，不限于旧单层货架 |

### 静态拓扑与运行时覆盖

Manifest 只描述不会随业务批次变化的事实：

```text
DeviceRequirement(操作工位)
  -- OPERATION -->
Position(货架停靠位 / 库存事实锚点)
  -- MATERIAL_FLOW -->
Position(货架停靠位 / 库存事实锚点)

Runtime resource/detail projection
  -- keyed by position_code -->
当前货架 / 箱体 / 物料 / 占用状态
```

- 设备节点负责过站、改变对象状态、上报 EVENT、接收 COMMAND。
- 货位节点负责承载对象，货位本身不随业务变化；货位上的货架、箱体、物料和占用状态来自运行时查询。
- 前端先用 manifest 渲染完整静态拓扑，再用运行时投影按 `position_code` 覆盖当前承载状态。

### 字段对比

| 指标 | 当前 | 重构后 | 变化 |
|------|------|--------|------|
| Manifest 字段数 | 16 | 8 | -50% |
| Position 字段数 | 旧边界 8 | `Position` 4 + `PositionCarrierCapability` 4 + `ResourceBoundary` 6 | 拓扑语义、承载约束和资源编排语义拆分；站点事实不重复 |
| Callable 字段 | 4 | 0 | -100% |
| 可序列化 | 否 | 是 | callable/type 均迁出 manifest |
| 拓扑节点 | 裸字符串/隐式推导 | typed `NodeRef` | 可验证、可前端直消费 |
| 事件能力 | `supported_events` + `event_source_roles` | `EventBinding` | 支持按设备角色渲染 EVENT 能力 |
| 命令能力 | `supported_commands` + `command_target_roles` | `CommandBinding` + `PositionArg` + `CommandResultBinding` | 支持 SOURCE/TARGET/WORK/BIN/NG 等动作参数和多结果路径 |

---

## Implementation Details

### 1. 后端改动

| 区域 | 变更 |
|------|------|
| Manifest dataclass | 移除 callable/type 字段；新增 `NodeRef`, `EventCategory`, `EventBinding`, `CommandBinding`, `CommandResultBinding`, `PositionArg`, `PositionArgSource`, `PositionCarrierCapability`, `ResourceBoundary` 等纯数据结构；删除旧类型别名和旧导出 |
| Topology validation | 校验 `NodeRef.kind/ref`、event source role、event category、command target role、position arg source、command result binding、flow edge、carrier capability 约束和 resource boundary 引用 |
| Plugin classes | manifest 只声明数据；business key、result classifier、context model、material identity、NG reason 暴露为 Plugin 类能力 |
| Registry | `WorklinePluginDefinition.plugin_instance` 成为唯一插件实例入口；删除旧 `_plugin_instance_cache`；registry helper 统一封装所有 Plugin runtime 能力 |
| Workline service | summary 构建、设备/货位/事件/命令/承载能力/资源边界校验改为读取新数据结构 |
| Runtime query | 旧 `single_layer_boundaries` 消费点迁移到 `resource_boundaries` |
| Inbox batch | 入口事件来源从 `events` 中 `category=ENTRY_DEVICE` 的 `EventBinding` 读取，不再依赖旧 `event_source_roles` |
| Sandbox event template | 事件模板过滤从结构化 `EventBinding` 读取，不再依赖旧 `event_source_roles` 或事件名推导 |
| Hold release / NG return | material identity 和 NG reason 不再读 manifest callable，改为 registry helper |
| Plugin SDK | 清理 `result_classifier.py`、`input_normalizer.py` 中的旧类型和消费点 |
| Session resolver | business_key 解析改为 registry helper |
| Legacy cleanup | 删除旧 manifest 字段、旧 shape check、旧测试 fixture、旧模板和旧文档示例，不做兼容分支 |

### 2. Plugin Runtime Interface

| 能力 | 新入口 | 主要调用方 |
|------|--------|------------|
| business key | registry helper 调用 Plugin 实例 | inbox / runtime routing |
| result classification | registry helper 调用 Plugin 实例 | callback / command result handling |
| context model | registry helper 读取 Plugin 类/实例 | handoff/context validation |
| material identity | registry helper 调用 Plugin 实例 | hold release / NG return |
| NG reason catalog | registry helper 读取 Plugin 实例 | hold release / NG return |

### 2.1 Registry Helper 语义

| Helper 能力 | 缺省语义 | 约束 |
|-------------|----------|------|
| business key | 未注册插件返回 `None`；已注册插件必须通过 Plugin runtime 能力解析 | 调用方不得访问 `manifest.resolve_business_key` |
| result classification | 未声明能力返回 `None` | 调用方不得访问 `manifest.classify_result` |
| context model | 未声明能力返回 `None` | type/model 不进入 manifest |
| material identity | 未声明能力返回现有 `MISSING` 身份语义 | hold release / NG return 只调用 registry helper |
| NG reason catalog | 未声明能力返回空 tuple | 不在 manifest 暴露 catalog |

### 3. API 和前端合同

| 区域 | 变更 |
|------|------|
| Backend API model | `WorkLinePluginManifestSummary` 与新 manifest 字段保持一致；`WorkLinePluginOption` 只保留 selector 字段，不再暴露旧角色/事件/命令字段 |
| OpenAPI | 后端模型生成新的 schema，作为前端类型唯一来源 |
| Static manifest cache | manifest API 可按 `plugin_key + contract_version` 缓存；contract version 变化才需要刷新静态拓扑 |
| Frontend generated types | 在前端仓库运行 `pnpm generate:types` 和 `pnpm generate:zod` |
| Frontend runtime aliases | 只保留 generated schema 的类型别名，不手写接口 |
| Frontend config page | 插件下拉只消费 `WorkLinePluginOption` selector 字段；角色覆盖、事件和命令展示按选中 `plugin_key` 拉取 manifest 详情 |
| `runtime-scene.ts` | 从 cached manifest 消费 `devices` / `positions` / `topology.flow_edges` / `events` / `resource_boundaries` 静态场景数据；`commands.position_args` 只可用于命令详情/能力展示，不得用于边界或拓扑推导；runtime polling 只按 `position_code` 叠加当前承载状态 |

### 4. 受影响文件清单

| 文件/区域 | 改动类型 | 工作量 |
|-----------|----------|--------|
| `src/workline_runtime/plugin_manifest.py` | 重写 manifest 数据合同 | 大 |
| `src/workline_runtime/topology.py` | 校验 typed topology / event category / command binding / position arg source / command result binding / resource boundary | 中 |
| `src/workline_plugin_registry.py` | 新增 plugin singleton 和 runtime helper | 中 |
| `src/workline_plugins/rough_sorter/plugin.py` | manifest 数据声明 + Plugin runtime 能力迁移 | 中 |
| `src/workline_plugins/smt_sorting_inbound/plugin.py` | manifest 数据声明 + Plugin runtime 能力迁移 | 中 |
| Workline service/runtime consumers | 迁移旧字段消费点 | 大 |
| `src/app/workline/models/workline.py` | API summary schema | 中 |
| 前端 generated contract | OpenAPI / zod 生成 | 中 |
| 前端 runtime scene | 新 manifest 数据消费 | 小 |
| 插件模板/指南 | 同步新合同 | 小 |
| 测试 | 后端、前端、合同、集成测试补齐 | 大 |

---

## Acceptance Criteria

1. `WorklinePluginManifest` 只包含纯数据字段：`plugin_key`, `contract_version`, `devices`, `positions`, `topology`, `commands`, `events`, `resource_boundaries`
2. manifest 内没有 callable、model type 或其它不可序列化字段
3. `devices` 能完整定义操作工位：role、min/max count、硬件约束；设备 EVENT/COMMAND 能力必须从 `EventBinding` / `CommandBinding` 派生，不在 device 上重复维护
4. `positions` 能完整定义 WES 管理的货架停靠位/库存事实锚点：code、role、station_code、`PositionCarrierCapability`；货位身份稳定，当前承载货架/箱体/物料不进入 manifest；扫码台、传感器、输送线内部点和机器人临时中转位不得进入 `positions`
5. `PositionCarrierCapability` 能表达插件承载约束：`allowed_rack_kinds`、`min_capacity`、`max_capacity`、可选 `allowed_slot_kinds`；实际 workline 位置配置必须满足这些约束
6. `EventBinding` 能定义设备事件能力：event、source device roles、`EventCategory`、payload schema ref；validator 能拒绝不存在的 source role 和非法 category；entry admission 只消费 `ENTRY_DEVICE`
7. `CommandBinding` 能定义设备命令能力：command、target device role、`position_args`、payload schema ref、`result_bindings`；validator 能拒绝不存在的 target role 和非法结果绑定
8. `PositionArg` 能表达动作参数：SOURCE、TARGET、WORK、BIN、NG 等语义位置；固定位置使用 `position_ref`，运行时来源使用结构化 `PositionArgSource`；required 参数必须且只能设置二者之一，optional 参数可不设置但不能同时设置；`PositionArgSource.kind` 不允许 `STATIC`
9. `topology` 必填，不能为 `None`；`FlowEdge` 端点使用 typed `NodeRef`，validator 能拒绝不存在的 device role / position code；`FlowEdge.type` 仅允许 `MATERIAL_FLOW` 或 `OPERATION`；`MATERIAL_FLOW` 必须拒绝任何 device-role 端点，设备到货架位关系使用 `OPERATION`
10. `ResourceBoundary.position_code` 必须在 `positions` 中存在；`ResourceBoundary.rack_kind` 必须包含在对应 `Position.carrier_capability.allowed_rack_kinds` 中；`ResourceBoundary` 不包含 `station_code` / `station_role`，相关值必须通过 `position_code -> Position` 派生；`rack_kind` 必须作为通用资源边界维度覆盖单层、五层和未来 rack kind，不允许实现只按旧 `SingleLayerRackBoundary` 语义测试
11. 前端能先用 manifest 渲染完整静态拓扑，再用运行时详情按 `position_code` 覆盖当前承载状态
12. manifest 静态合同按 `plugin_key + contract_version` 缓存或等价复用；runtime polling 不重复返回完整 manifest，只返回按 `position_code` 关联的当前承载状态
13. business key、result classification、context model、material identity、NG reason 全部通过 registry helper 调用 Plugin runtime 能力，并遵循统一缺省语义
14. registry 是唯一插件实例生命周期管理入口；旧 `_plugin_instance_cache` 删除
15. 两个插件（`rough_sorter`, `smt_sorting_inbound`）可正常实例化并导出新 manifest
16. API `/api/v1/workline/plugins/{plugin_key}/manifest` 返回完整可序列化 JSON，并生成正确 OpenAPI schema
17. 插件 options API 只返回插件选择所需字段：`plugin_key`, `label`, `contract_versions`, `default_contract_version`；不得继续暴露 `required_device_roles`、`supported_events`、`supported_commands` 或新 manifest 能力副本
18. 前端配置页选择插件后通过 manifest 详情读取 `devices` / `events` / `commands`，不得继续把 `WorkLinePluginOption` 当能力合同使用
19. 前端通过生成链路获取新类型和 zod schema，不手写 generated contract
20. runtime scene、插件模板/指南和相关测试均同步到新 manifest 合同
21. 旧字段、旧类型、旧 helper 调用路径和旧 generated contract 在 `src/`、`tests/`、插件模板和前端源码/测试中清零

---

## Testing Plan

| Layer | What | Required Scenarios |
|-------|------|--------------------|
| Backend unit | Manifest 数据合同 | 正常字段、缺失必填、空字符串、重复 code、不可序列化字段不再存在 |
| Backend unit | Device operation capability | `EventBinding` / `CommandBinding` 派生设备 EVENT/COMMAND 能力；未知 source/target role 失败 |
| Backend unit | Position carrier capability | 单层/五层货架约束、min/max capacity、slot kind、空约束、非法 rack kind，以及 Workline 实际配置满足/不满足约束 |
| Backend unit | Topology | topology 必填、typed `NodeRef` 成功/失败、未知 role、未知 position、非法 edge type、`MATERIAL_FLOW` 必须 position-to-position、device-role 端点只能用于 `OPERATION` |
| Backend unit | Command binding | 无位置参数命令、多位置参数命令、SOURCE/TARGET/WORK/BIN/NG、未知 `position_ref`、`position_ref` 与 `source` 同时设置失败、required 参数二者都缺失失败、optional 参数二者都缺失成功、非法 `PositionArgSource.kind/path/fallback_position_ref`、`STATIC` kind 禁用、多结果绑定、非法 result category |
| Backend unit | Event binding | 多 source role、未知 source role、payload schema ref、`EventCategory` 枚举、ENTRY_DEVICE/internal/command-result 过滤边界 |
| Backend unit | Resource boundary | 通用 rack resource boundary 完整性、单层/五层/混合 rack kind、未知 position、station 派生、rack/WMS/snapshot/lease 字段校验 |
| Backend unit | Registry helper | business key、result、context model、material identity、NG reasons、缺省语义、唯一插件实例 |
| Backend unit | Real plugin manifest golden samples | `rough_sorter` 和 `smt_sorting_inbound` 导出完整新 manifest；断言 8 个顶层字段、必填 topology、EventCategory、CommandResultBinding、PositionArgSource、PositionCarrierCapability、rack-position-only `positions`、position-to-position `MATERIAL_FLOW` 和旧字段不存在 |
| Backend service/API | Workline manifest summary | 两个插件 summary、OpenAPI schema、旧字段不再返回 |
| Backend service/API | Workline plugin options | options API 只返回 selector 字段；旧 role/event/command 字段不再进入 OpenAPI/generated contract |
| Backend integration | Runtime consumers | inbox entry filtering、sandbox event template、runtime resource lookup、hold release、NG return |
| Frontend contract | OpenAPI / zod | `pnpm generate:types`、`pnpm generate:zod`、contract verify |
| Frontend unit | WorkLine config page | 插件下拉使用 selector options；角色覆盖、事件和命令展示从 manifest 详情读取，旧 option 字段不存在时页面仍可工作 |
| Frontend unit | Runtime scene | cached manifest 的 devices/positions/topology/resource boundaries 场景消费、contract version 变化刷新、runtime polling 只叠加 overlay、不重复依赖完整 manifest；断言 `commands.position_args` 为空不会影响边界或拓扑渲染 |
| Docs/templates | Plugin developer assets | 模板、指南、fixture 和资产测试同步 |
| Cleanup gate | 旧合同清零 | 以下符号不得出现在新 `src/`、`tests/`、插件模板、活跃开发者指南、前端源码/测试和 generated contract 中：`required_device_roles`、`DeviceRoleRequirement`、`event_source_roles`、`command_target_roles`、`supported_events`、`supported_commands`、`capabilities`(manifest级)、旧 `DeviceRequirement.capabilities`、旧 `Position.capabilities`、旧 `PositionCarrierCapability.capacity`、扁平 `CommandBinding.position_ref`、旧 `CommandBinding.result_event`、旧 `PositionArg.runtime_source`、`TopologySpec \| None`、`resource_kinds`、`requires_single_layer_boundary`、`single_layer_boundaries`、`SingleLayerRackBoundary`、`business_key_resolver`、`result_classifier`、`context_model`、`material_identity_resolver`、`ng_reason_catalog`、`BusinessKeyResolver`(类型别名)、`ResultClassifier`(类型别名)、`_looks_like_manifest`、`_ALLOWED_SINGLE_LAYER_*` 常量、`_requires_single_layer_boundaries`、旧 `__all__` 导出项；本迁移 SPEC、历史归档文档和 review report 可保留旧符号作为迁移说明 |

---

## Rollback Plan

由于是破坏性重构，rollback 需要：
1. 恢复 `plugin_manifest.py` 到重构前版本
2. 恢复 registry helper 调用路径到旧 manifest callable
3. 恢复两个插件的 manifest 数据声明和 runtime 能力位置
4. 恢复 API 响应模型和 OpenAPI schema
5. 前端同步回滚 generated types/zod schemas 和 runtime scene 使用方

**风险**：如果前端已经依赖新字段，rollback 会导致前端报错。

**缓解**：该系统仍按未发布破坏性重构处理，本次不做旧字段兼容；必须前后端同分支同步验证后再合入。

---

## Effort Estimate

| Component | Effort |
|-----------|--------|
| 计划文档合同化 | 1h |
| 后端 manifest/topology 合同重写 | 5h |
| registry runtime helper | 3h |
| 插件迁移（2 个） | 3h |
| Workline runtime/service 消费方迁移 | 4h |
| API summary schema | 1h |
| 前端生成链路 + runtime scene 适配 | 2h |
| 测试矩阵补齐 | 5h |
| 旧合同清理门禁 | 2h |
| 插件模板/指南同步 | 1h |
| **总计** | **27h** |

---

## Files Reference

| File | Change |
|------|--------|
| `src/workline_runtime/plugin_manifest.py` | 重写 manifest 定义 |
| `src/workline_runtime/topology.py` | typed topology / command binding / resource boundary validation |
| `src/workline_plugin_registry.py` | plugin singleton 和 runtime helper |
| `src/workline_runtime/orchestrator.py` | 删除旧 `_plugin_instance_cache` 并改用 registry 实例入口 |
| `src/workline_runtime/__init__.py` | 删除旧 manifest 类型导出 |
| `src/workline_plugins/rough_sorter/plugin.py:159-195` | 调整实例化 |
| `src/workline_plugins/smt_sorting_inbound/plugin.py:115-130` | 调整实例化 |
| `src/app/workline/models/workline.py:271-283` | API 响应模型 |
| `src/app/workline/models/workline.py:259-268` | `WorkLinePluginOption` selector-only schema |
| `src/app/workline/services/workline_service.py:214-252` | options selector 和 manifest summary 构建逻辑 |
| `src/app/workline/services/runtime_query_service.py:1420-1500` | 边界查询 |
| `src/app/workline/services/inbox_batch_processor.py` | entry event type 来源迁移 |
| `src/app/workline/services/operation_service.py` | sandbox event template 来源迁移 |
| `src/app/workline/services/runtime_hold_release_service.py` | material identity / NG reason 来源迁移 |
| `src/app/workline/services/ng_return_item_service.py` | material identity / NG reason 来源迁移 |
| `src/workline_runtime/plugin_sdk/classifiers/result_classifier.py` | 清理旧 result_classifier 类型和消费点 |
| `src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py` | 清理旧 input normalizer 消费点 |
| `src/workline_runtime/session_resolver.py` | 迁移 business_key 解析调用 |
| 前端 generated OpenAPI/zod | 类型定义生成 |
| 前端 `src/views/admin/worklines/config/WorkLineConfigPage.vue` | 插件 options selector 与 manifest 详情消费拆分 |
| 前端 `src/utils/runtime-scene.ts:345-425` | 使用方 |
| 插件模板/指南 | 新 manifest 合同同步 |

---

## Out of Scope

- 触发约束声明（`position_triggers`）— 属于业务逻辑，不放在 manifest
- 前端复杂布局和视觉优化 — 本次确保数据合同支持完整渲染，不扩展 UI 交互和布局算法
- 旧 manifest 字段兼容层 — 未发布系统，按破坏性重构处理

---

## Related

- `docs/architecture/SRS.md` — 系统需求规格
- `docs/business/rough_sorter_runtime_flow.md` — 粗分机流程
- `docs/business/smt_sorter_inbound_workflow_guide.md` — 分拣机流程

---

## Engineering Review Findings

### Decisions Locked

| # | Finding | Decision | Confidence |
|---|---------|----------|------------|
| D1 | 未发布系统不需要旧合同兼容 | 做完整破坏性清理，删除旧 manifest 字段、旧导出、旧 fixture、旧模板和旧 generated contract | 10/10 |
| D2 | `event_source_roles` 有 inbox batch 和 sandbox event template 消费者 | 用结构化 `EventBinding` 替换旧映射，事件来源成为可序列化的静态能力合同 | 10/10 |
| D3 | callable 从 manifest 删除后仍需要稳定业务入口 | registry helper 覆盖 business key、result、context model、material identity、NG reasons，并定义缺省语义 | 10/10 |
| D4 | `FlowEdge` 裸字符串无法区分 device role 和 position | 使用 typed `NodeRef(kind, ref)`，validator 校验引用存在 | 10/10 |
| D5 | 命令不总是绑定单一货位 | 使用 `CommandBinding(command, target_device_role, position_args[])`；位置参数按 SOURCE/TARGET/WORK/BIN/NG 等语义声明 | 10/10 |
| D6 | 设备操作能力和货位承载能力不是同一语义 | 设备 EVENT/COMMAND 从 bindings 派生；货位承载能力用 `PositionCarrierCapability` 显式声明 | 10/10 |
| D7 | rack 资源编排元数据不能从拓扑推导 | 用纯数据 `resource_boundaries` 承载 rack/WMS/snapshot/lease 元数据 | 10/10 |
| D8 | `ResourceBoundary` 重复保存 station 字段会制造漂移 | 仅保存 `position_code` 和资源字段，`station_code` / `station_role` 从 `Position` 派生 | 10/10 |
| D9 | `ResourceBoundary.position_code` / `rack_kind`、`NodeRef.ref`、`FlowEdge.type` 需要硬校验 | validator 必须拒绝未知 position/device role、非法 edge type，以及不在对应 `PositionCarrierCapability.allowed_rack_kinds` 中的 boundary rack kind | 10/10 |
| D10 | 前端合同来自 OpenAPI 生成链路 | 更新后端 schema 后运行前端 `generate:types`、`generate:zod`、contract verify，不手写 generated 文件 | 10/10 |
| D11 | 旧 public exports 会把过期合同继续暴露给调用方 | 删除 `DeviceRoleRequirement` 等旧导出、旧 `__all__` 项和旧 `_looks_like_manifest` callable 检查 | 10/10 |
| D12 | 现有测试大量覆盖旧 manifest 形状 | 删除或改写旧测试，新增清理型 `rg` 门禁，旧字段不得在新源码/测试/模板/generated contract 中残留 | 10/10 |
| D13 | runtime 已有 `_plugin_instance_cache`，再在 registry 加缓存会产生双实例生命周期 | registry `plugin_instance` 成为唯一实例入口，旧 orchestrator cache 删除 | 10/10 |
| D14 | 计划文档不能包含完整实现代码 | 保留字段合同、任务边界、验收和验证命令，不粘贴完整类/函数实现 | 10/10 |
| D15 | 重复 `GSTACK REVIEW REPORT` 破坏评审格式约束 | 合并所有 finding，保证最后一个 `##` 标题精确为 `## GSTACK REVIEW REPORT` | 10/10 |
| D16 | 原 SPEC 对用户四项能力只“部分支持” | 补齐静态拓扑、设备 EVENT/COMMAND、货位承载能力、动作位置参数四类合同 | 10/10 |
| D17 | 货位上的货架会随业务变化 | Manifest 只保存静态 position；当前货架/箱体/物料由运行时投影按 `position_code` 覆盖 | 10/10 |
| D18 | 现有 `WorkLineRackPosition` 已有 `allowed_rack_kind` 和 `capacity` | 新 manifest 定义插件约束，Workline 配置继续保存实际值，validator 校验实际值满足约束 | 10/10 |
| D19 | 现有 rough sorter action/payload 同时包含 WES 货架位和硬件内部物理点 | `PositionArg` 只表达 WES 货架位/资源位置；扫码点、输送线入口/出口和 NG 物理点保留为 command payload / 插件业务逻辑 | 10/10 |
| D20 | 计划影响超过 8 个文件/区域，触发范围挑战 | 用户确认保持完整破坏性重构；manifest 是中心合同，不拆成半套旧/新合同 | 10/10 |
| D21 | `PositionCarrierCapability.capacity` 与 `WorklineRackPosition.capacity` 容易双写 | 改为 `min_capacity` / `max_capacity` 约束，实际容量仍归 Workline 配置 | 10/10 |
| D22 | `PositionArg.runtime_source` 会变成新的字符串黑盒 | 改为结构化 `PositionArgSource(kind, path, fallback_position_ref)`，kind 使用受控枚举 | 10/10 |
| D23 | `EventBinding.category` 未定义受控枚举会让事件过滤继续靠字符串约定 | 新增 `EventCategory`，entry admission 只消费 `ENTRY_DEVICE`，其它事件类别不得混入入口过滤 | 10/10 |
| D24 | `CommandBinding.result_event` 只能表达单一结果路径 | 改为 `result_bindings: list[CommandResultBinding]`，覆盖成功、失败、超时、业务分类和后续事件 | 10/10 |
| D25 | Cleanup gate 写成全 docs 清零会误伤本迁移 SPEC | 限定扫描活跃代码、测试、模板、开发者指南和 generated contract；迁移 SPEC/历史归档/review report 可保留旧符号说明 | 10/10 |
| D26 | 字段级 manifest 单测不能证明真实插件迁移完整 | 增加 `rough_sorter` / `smt_sorting_inbound` 新 manifest 金样例测试，锁定真实插件合同完整性 | 10/10 |
| D27 | 高频 runtime polling 不应重复传输静态 manifest | manifest 按 `plugin_key + contract_version` 缓存或等价复用；runtime polling 只返回当前 overlay | 10/10 |
| D28 | `resource_boundaries` 仍按单层货架描述会让五层资源编排漏测 | 将其定义为通用 rack resource boundary；旧 `SingleLayerRackBoundary` 仅作为迁移来源，测试覆盖单层、五层和混合 rack kind | 10/10 |
| D29 | sandbox event template 的旧 manifest 消费点实际在 workline operation service | 将文件清单修正为 `src/app/workline/services/operation_service.py`，避免实现阶段误改 rack operation service 而漏掉真实模板生成流程 | 10/10 |
| D30 | `WorkLinePluginOption` 也是公开合同面，保留旧字段会让 generated contract 清理失败 | options API 改为 selector-only；配置页按选中插件加载 manifest 详情，不在 option 和 manifest 双写能力事实 | 10/10 |
| D31 | `PositionArg` 同时支持 `position_ref` 和 `STATIC` source 会制造两条静态表达路径 | 保留 `position_ref` 作为唯一静态固定位置表达；`PositionArgSource.kind` 移除 `STATIC`；validator 强制 required 参数 `position_ref` XOR `source` | 10/10 |

### What Already Exists

- 后端 manifest 和 topology 合同位于 `src/workline_runtime/plugin_manifest.py` 与 `src/workline_runtime/topology.py`。
- 运行时消费者已经在 registry helper、workline validation、runtime query、inbox batch entry filtering、sandbox event template、hold/release 和 NG return 流程中读取旧 manifest 字段。
- `src/app/workline/models/rack_position.py` 已有 `allowed_rack_kind` 和 `capacity`，说明货位承载约束是现有领域事实。
- `src/app/workline/services/rack_position_service.py` 已校验货位启用状态、rack kind 和容量，manifest 应对齐该语义而不是新增另一套能力解释。
- `src/app/workline/services/station_lease_service.py` 按 `position_code` 和运行时 active rack/outbox/session 状态处理占用，说明当前承载对象必须是运行时 overlay。
- `src/workline_plugins/smt_sorting_inbound/plugin.py` 已声明 `SINGLE_LAYER` / `FIVE_LAYER` 两类资源，但旧 boundary 只覆盖 `SingleLayerRackBoundary`，说明新 `resource_boundaries` 必须按 `rack_kind` 泛化校验。
- `src/workline_plugins/rough_sorter/contract.py` 与插件 action 生成逻辑已经使用 source/target/bin/NG/work 参数；其中只有 WES 货架位/资源位置进入 `PositionArg`，扫码点、输送线入口/出口和 NG 物理点留在 payload/business logic。
- `src/workline_runtime/__init__.py`、插件实现、模板、文档和测试仍暴露或引用旧字段和旧类型。
- `src/workline_runtime/orchestrator.py` 已有插件实例缓存；重构后需要迁移到 registry 单一入口。
- `src/app/workline/services/operation_service.py` 的 sandbox event template 仍从 `supported_events` / `event_source_roles` 生成，必须迁移到结构化 `EventBinding`。
- `WorkLinePluginOption` 当前仍通过 options API 和前端配置页暴露旧角色/事件/命令字段；重构后应只作为插件 selector，完整能力读取 manifest 详情。
- 前端 runtime 类型来自 OpenAPI 生成产物；runtime scene/config 逻辑当前仍消费旧 manifest 字段。
- 现有测试大量覆盖旧 manifest 形状；新合同覆盖率需要删除或改写旧测试后重新建立。

### Data Flow

```text
Plugin class
  -> pure-data manifest:
       devices(operation stations)
       positions(carrier stations + carrier capability)
       topology(flow edges)
       events(EventBinding)
       commands(CommandBinding + PositionArg + CommandResultBinding)
       resource_boundaries
  -> registry plugin_instance: unique plugin lifecycle
  -> registry helpers: callable runtime behavior and defaults
  -> backend service summary and OpenAPI schema
  -> static manifest cache keyed by plugin_key + contract_version
  -> generated frontend types and zod schemas
  -> runtime scene/config consumers
  -> runtime overlay by position_code: current rack / bin / material / occupancy
```

### Failure Modes

- 删除 `event_source_roles` 但没有 `EventBinding` 替代，会破坏 inbox batch 入口事件分类和 sandbox event template 生成。
- 用泛化 `capabilities` 表达 EVENT/COMMAND，会和 `EventBinding` / `CommandBinding` 形成重复事实，后续必然漂移。
- 用泛化 `capabilities` 表达货位承载约束，会丢失 `allowed_rack_kinds`、`min_capacity`、`max_capacity`、slot kind 这类可校验字段。
- 把 rough sorter 的扫码点、输送线入口/出口或 NG 物理点声明成 `PositionArg.position_ref`，会重新污染 `positions` 的货架位/库存事实锚点语义。
- 把当前货架或物料状态写进 manifest，会把运行时状态误当静态拓扑，导致前端显示和实际占用不一致。
- 保留裸字符串 topology，会让 device role 与 position 的错误引用延迟到运行时才暴露。
- 删除 rack 资源边界元数据，会丢失 WMS operation、snapshot kind、lease scope 和 allocation context。
- 只按旧单层货架语义测试 `resource_boundaries`，会让五层货架虽通过承载约束却缺失 WMS/snapshot/lease 编排合同。
- 在 `Position` 和 `ResourceBoundary` 两处保存 `station_code` / `station_role`，会制造配置漂移。
- 同时保留 registry 和 orchestrator 插件实例缓存，会拆分运行时状态并隐藏生命周期问题。
- 保留旧 public exports 或 generated 字段，会让新代码继续依赖已删除合同。
- 只清理 manifest summary 而不清理 `WorkLinePluginOption`，旧字段会通过 options API 和 generated contract 重新进入前端。
- 允许 `PositionArg.position_ref` 与 `PositionArg.source` 双写或都缺失，会让 validator、runtime resolver 和前端展示对 WES 货架位/资源位置得出不同结论。
- 手写前端类型会造成后端 OpenAPI 与 zod 生成产物漂移。
- 高频 runtime polling 如果重复返回完整 manifest，会把静态合同和动态状态重新耦合，增加 payload 和前端重复解析成本。

### Implementation Tasks

每个任务都来自上面的锁定决策。实现时需要先按项目规则对要修改的函数/类/方法运行 GitNexus impact analysis。

- [x] **T1 (P1, human: ~1h / CC: ~10min)** — spec — 保持计划文档为字段合同、任务边界和验收标准
  - 来源: D14/D15/D16
  - 文件: `docs/superpowers/specs/2026-06-12-workline-plugin-manifest-refactor.md`
  - 验证: 无完整类/函数实现代码块；最后一个报告标题和哨兵有效
- [x] **T2 (P1, human: ~5h / CC: ~60min)** — manifest-contract — 重写 `WorklinePluginManifest` 为纯数据合同并删除旧导出
  - 来源: D1/D2/D4/D5/D6/D7/D8/D9/D11/D16/D17/D18/D19/D20/D21/D22/D23/D24/D28/D31
  - 文件: `src/workline_runtime/plugin_manifest.py`, `src/workline_runtime/topology.py`, `src/workline_runtime/__init__.py`
  - 验证: manifest 单测覆盖有效/无效合同、必填 topology、`EventCategory`、`EventBinding`、`CommandBinding.position_args`、`PositionArg.position_ref` 与 `source` 互斥、`PositionArgSource` 不含 `STATIC`、`CommandResultBinding`、`PositionCarrierCapability`；旧 manifest 符号不存在
- [x] **T3 (P1, human: ~3h / CC: ~35min)** — plugin-registry — 建立 registry 唯一插件实例和 runtime helper
  - 来源: D3/D13
  - 文件: `src/workline_plugin_registry.py`, `src/workline_runtime/orchestrator.py` 和当前 workline plugins
  - 验证: helper 测试覆盖缺省语义、context model、material identity、NG reasons、result classification、business key 和单实例行为
- [x] **T4 (P1, human: ~4h / CC: ~50min)** — runtime-services — 迁移 Workline 运行时消费方到新合同
  - 来源: D2/D5/D7/D8/D12/D17/D18/D19/D21/D22/D23/D24/D28/D29
  - 文件: workline validation、runtime query、inbox batch processor、`src/app/workline/services/operation_service.py` sandbox event template、hold/release、NG return services
  - 验证: 定向后端测试覆盖 topology validation、event category filtering、command position args、command result bindings、carrier capability 约束、resource boundary lookup、runtime overlay、station derivation 和 NG/material flows
- [x] **T5 (P1, human: ~2h / CC: ~25min)** — frontend-contract — 通过 OpenAPI 生成链路更新前端类型并适配 runtime scene
  - 来源: D10/D12/D16/D17/D27/D30
  - 文件: 前端 generated types/zod schemas、metadata、WorkLine config page、runtime scene 和 config consumers
  - 验证: 前端类型生成、合同校验、plugin options selector-only、配置页按 manifest 详情展示角色/事件/命令、cached manifest 静态拓扑渲染、contract version 变化刷新、按 `position_code` runtime overlay、runtime scene Vitest 和旧字段 `rg` 门禁通过
- [x] **T6 (P1, human: ~4h / CC: ~45min)** — tests-cleanup — 删除或改写旧测试，补齐后端、前端、合同与集成测试矩阵
  - 来源: D12/D25/D26/D28
  - 文件: 后端 workline runtime/service/API tests 和前端 runtime-scene tests
  - 验证: focused pytest + Vitest suites 通过；真实插件 manifest 金样例、失败路径和可执行清理门禁被覆盖
- [x] **T7 (P2, human: ~1h / CC: ~15min)** — plugin-docs — 同步插件模板和开发者指南到新 manifest 契约
  - 来源: D1/D12
  - 文件: plugin template assets、developer docs、template asset tests
  - 验证: plugin template asset tests 通过

### Test Plan

- Backend unit: manifest field normalization, required topology, typed `NodeRef`, `EventCategory`, `EventBinding`, `CommandBinding.position_args`, `CommandBinding.result_bindings`, `PositionArgSource`, `CommandResultBinding`, `PositionCarrierCapability`, reserved event/command validation, and generic `resource_boundaries` for single-layer, five-layer, and mixed rack kinds.
- Backend unit: `PositionArg` static/dynamic source validation, including `position_ref` only, structured `source` only, both-set failure, required both-missing failure, optional both-missing success, and rejected `PositionArgSource.kind=STATIC`.
- Backend unit: real plugin manifest golden samples for `rough_sorter` and `smt_sorting_inbound`, including required topology and absence of old fields.
- Backend unit: registry helper defaults and single plugin instance lifecycle.
- Backend service/API: plugin summary schema, workline validation checks, topology validation, carrier capability checks, runtime boundary/resource lookup, station derivation, sandbox event template generation, inbox entry event filtering, NG/material helper paths.
- Frontend contract: regenerate OpenAPI and zod schemas, then update runtime type aliases, metadata, config page, plugin option selector usage, and runtime scene tests.
- Frontend unit: verify WorkLine config page no longer reads `required_device_roles` / `supported_events` / `supported_commands` from `WorkLinePluginOption`; it must load selected manifest details for role coverage and event/command display.
- Frontend unit: verify runtime scene can render cached static device/position topology first, refresh static manifest only when `contract_version` changes, then overlay current carrier state by `position_code`.
- Integration/E2E: cover rough sorter and SMT inbound happy paths plus missing role, missing event binding source role, invalid command position arg, invalid carrier capability, and missing resource-boundary failures.
- Cleanup gate: run `rg` checks for removed fields/symbols in backend `src/`, `tests/`, plugin templates, active docs, frontend `src/`, frontend `tests/`, and generated contract outputs.

### Test Coverage Diagram

```text
CODE PATHS                                                USER FLOWS
[GAP] src/workline_runtime/plugin_manifest.py             [GAP] Workline manifest page/API
  ├── [→UNIT] required topology                             ├── [→API] rough_sorter returns full manifest
  ├── [→UNIT] EventCategory validation                      ├── [→API] smt_sorting_inbound returns full manifest
  ├── [→UNIT] PositionArg XOR/source validation             └── [→API] old fields are absent
  ├── [→UNIT] CommandResultBinding validation
  └── [→UNIT] PositionCarrierCapability constraints        [GAP] Runtime scene rendering

[GAP] src/workline_runtime/topology.py                       ├── [→FRONTEND] render static topology first
  ├── [→UNIT] unknown device role / position                 ├── [→FRONTEND] refresh static manifest on contract_version change
  ├── [→UNIT] illegal edge type                              └── [→FRONTEND] overlay current rack/bin/material by position_code
  └── [→UNIT] Workline actual config satisfies constraints

[GAP] real plugin manifests                                [GAP] Runtime consumers
  ├── [→UNIT] rough_sorter golden sample                     ├── [→INTEGRATION] ENTRY_DEVICE event filtering
  └── [→UNIT] smt_sorting_inbound golden sample              ├── [→INTEGRATION] command result binding paths
                                                              └── [→INTEGRATION] resource boundary lookup

[GAP] WorkLine plugin options                               [GAP] Config page plugin selection
  ├── [→API] selector-only options schema                     ├── [→FRONTEND] select plugin via options
  └── [→API] old option fields absent                         └── [→FRONTEND] display role/event/command via manifest detail

COVERAGE TARGET: 0 shortcuts accepted; all listed GAP paths must become tests in this branch.
```

### Worktree / Parallelization

- Use a dedicated feature branch from `develop`; worktree is optional but recommended if this interrupts other local work.
- Lane 1 is sequential: backend manifest/topology contract, old exports cleanup, then registry singleton/helper.
- Lane 2 can run after Lane 1: runtime service migration and plugin migration in parallel if symbol impact is checked before edits.
- Lane 3 can run after backend schema compiles: frontend generated contract, config page, metadata, and runtime scene adaptation.
- Lane 4 follows contract stabilization: old-field cleanup gates, focused tests, then full quality gate.

### Completion Summary

- Step 0: Scope Challenge - full destructive cleanup accepted by user
- Architecture Review: manifest/data/runtime/resource boundaries and command position-source invariants resolved into D1-D24/D28/D31
- Code Quality Review: old exports, executable cleanup gates, duplicate reports, planning-doc rules, implementation file path accuracy, and options API contract cleanup resolved into D10-D15/D25/D29/D30
- Test Review: cleanup gates, generated-contract verification, real plugin golden samples, carrier capability, generic rack resource boundary, PositionArg XOR validation, plugin options contract and runtime overlay scenarios accepted
- Performance Review: registry-only plugin instance lifecycle and static manifest cache boundary accepted; old `_plugin_instance_cache` removed from plan
- Outside voice: prior Claude outside voice findings are covered by D2/D3/D5/D7/D8；本轮 investigate 发现的四项 manifest 能力缺口已覆盖到 D16-D19
- NOT in scope: trigger constraint declaration and complex frontend layout/visual optimization remain out of scope
- What already exists: written
- TODOS.md updates: no separate TODO file change required; tasks are embedded in this spec
- Failure modes: 15 implementation risks documented, 0 unresolved gaps
- Parallelization: 4 lanes, 2 parallel after contract stabilization
- Lake Score: 10/10 decisions chose complete cleanup options

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | no fresh run |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | not run |
| Eng Review | `/plan-eng-review` | Architecture, code quality, tests, performance | 8 | CLEAR | 31 decisions, 0 unresolved gaps |
| Investigate | `/investigate` | Verify manifest can express topology, EVENT/COMMAND, carrier constraints, action params | 1 | CLEAR | original spec was partial; updated contract now covers all 4 requested capabilities |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | no fresh run |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | no fresh run |

- **CROSS-MODEL:** 已记录的 Claude outside voice 风险均已并入 D2/D3/D5/D7/D8；本轮新增的 manifest 能力缺口、必填 topology、静态拓扑/运行时 overlay、结构化 EVENT/COMMAND、货位承载约束、通用 rack resource boundary、动作位置参数及其静态/动态来源互斥、plugin options selector-only 合同、真实插件金样例、破坏性清理、静态 manifest cache 和 registry-only lifecycle 已写入验收标准。
- **VERDICT:** ENG CLEARED - spec body now supports complete workline topology rendering, device EVENT/COMMAND capability, position carrier capability, generic rack resource boundaries, selector-only plugin options, unambiguous command action parameters, static manifest caching, and clean breaking migration.

NO UNRESOLVED DECISIONS
