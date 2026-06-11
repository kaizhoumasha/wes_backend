# Workline Plugin Manifest 重构

> **日期**: 2026-06-12
> **状态**: Draft
> **类型**: 破坏性重构（未发布系统）
> **影响范围**: 后端插件系统 + 前端类型定义

---

## Context

当前 `WorklinePluginManifest` 混合了契约数据和实现代码，导致：
1. **不可序列化**：callable 字段无法通过 API 完整返回给前端
2. **字段冗余**：`SingleLayerRackBoundary` 有 8 个字段，其中 2 个永远固定值
3. **拓扑隐含**：设备与货位的关系需要从 `command_target_roles` 间接推导

本次重构从第一性原理出发，将 manifest 回归为**纯数据契约**。

---

## 设计决策

| # | 决策 | 理由 |
|---|------|------|
| 1 | Manifest = 纯数据契约，代码归 Plugin 类 | callable 不可序列化，应属于实现 |
| 2 | 拓扑需要显式声明 `flow_edges` | 前端无法从命令推导拓扑 |
| 3 | 货位是独立拓扑节点 | 货位是物料流转的位置，不是设备属性 |
| 4 | 设备与货位的关系用 `position_bindings` 单独声明 | 与 flow_edges 职责分离 |
| 5 | 货位之间的物料流动用 `flow_edges` 显式声明 | 前端可直接消费，无需推导 |
| 6 | 触发约束不放在 manifest 里 | 属于业务逻辑，不是契约 |

---

## 当前状态

### 字段清单（16 个）

| 字段 | 类型 | 问题 |
|------|------|------|
| `plugin_key` | `str` | ✅ 保留 |
| `contract_version` | `str` | ✅ 保留 |
| `required_device_roles` | `tuple[DeviceRoleRequirement, ...]` | ⚠️ 重命名为 `devices` |
| `business_key_resolver` | `Callable` | ❌ 移除 → Plugin 类 |
| `result_classifier` | `Callable` | ❌ 移除 → Plugin 类 |
| `context_model` | `type` | ❌ 移除 → Plugin 类 |
| `event_source_roles` | `Mapping` | ❌ 移除 → 从代码推导 |
| `command_target_roles` | `Mapping` | ❌ 拆分到 `position_bindings` |
| `supported_events` | `frozenset[str]` | ✅ 保留，重命名为 `events` |
| `supported_commands` | `frozenset[str]` | ✅ 保留，重命名为 `commands` |
| `capabilities` | `frozenset[str]` | ❌ 移除 → 从 `positions` 推导 |
| `resource_kinds` | `frozenset[str]` | ❌ 移除 → 从 `positions` 推导 |
| `requires_single_layer_boundary` | `bool` | ❌ 移除 → 从 `positions` 推导 |
| `single_layer_boundaries` | `Sequence[SingleLayerRackBoundary]` | ️ 简化为 `positions` |
| `material_identity_resolver` | `Callable` | ❌ 移除 → Plugin 类 |
| `ng_reason_catalog` | `Sequence[NgReasonDefinition]` | ❌ 移除 → Plugin 类 |

### `SingleLayerRackBoundary` 字段分析（8 个）

| 字段 | 值域 | 问题 |
|------|------|------|
| `station_code` | 任意字符串 | ✅ 保留 |
| `position_code` | 任意字符串 | ✅ 保留，重命名为 `code` |
| `rack_kind` | 永远是 `"SINGLE_LAYER"` | ❌ 冗余 |
| `station_role` | `SOURCE/TARGET/CLASSIFIER_WORK` | ✅ 保留，重命名为 `role` |
| `business_demand_type` | 3 个枚举值 |  可推导 |
| `wms_operation_type` | 3 个枚举值 | ❌ 可推导 |
| `snapshot_kind` | 3 个枚举值 | ❌ 可推导 |
| `lease_scope` | 永远是 `"STATION"` | ❌ 冗余 |

**结论**：8 个字段中只有 3 个是必要的（`station_code`, `position_code`, `station_role`）。

---

##  Proposed Change

### 新 Manifest 结构

```python
@dataclass(frozen=True)
class WorklinePluginManifest:
    """最小契约：我需要什么、我在哪操作、我能做什么"""
    
    # 身份
    plugin_key: str
    contract_version: str
    
    # 资源需求
    devices: list[DeviceRequirement]
    positions: list[Position]
    
    # 拓扑结构（可选，不声明则前端自行推导）
    topology: TopologySpec | None = None
    
    # 能力声明（前端 UI 需要）
    commands: list[str]
    events: list[str]


# === 数据结构 ===

@dataclass(frozen=True)
class DeviceRequirement:
    role: str
    min_count: int = 1
    max_count: int | None = None


@dataclass(frozen=True)
class Position:
    code: str              # position_code
    role: str              # SOURCE/TARGET/CLASSIFIER_WORK
    station_code: str


@dataclass(frozen=True)
class TopologySpec:
    flow: list[FlowEdge]
    bindings: list[PositionBinding]


@dataclass(frozen=True)
class FlowEdge:
    from_node: str         # 设备角色或位置 code
    to_node: str
    type: str              # MATERIAL_FLOW / OPERATION


@dataclass(frozen=True)
class PositionBinding:
    device_role: str
    command: str
    position: str          # position_code
```

### 字段对比

| 指标 | 当前 | 重构后 | 变化 |
|------|------|--------|------|
| Manifest 字段数 | 16 | 6 | -62% |
| Position 字段数 | 8 | 3 | -62% |
| Callable 字段 | 4 | 0 | -100% |
| 可序列化 |  | ✅ | |

---

## Implementation Details

### 1. 后端改动

#### `plugin_manifest.py`（重写）

```python
# 移除的类
- SingleLayerRackBoundary
- DeviceRoleRequirement（重命名并简化）
- BusinessKeyResolver, ResultClassifier 等类型别名

# 新增的类
+ DeviceRequirement
+ Position
+ TopologySpec
+ FlowEdge
+ PositionBinding

# 修改的类
- WorklinePluginManifest → 只保留 6 个字段
```

#### Plugin 类调整

```python
class RoughSorterPlugin:
    manifest = WorklinePluginManifest(
        plugin_key="rough_sorter",
        contract_version="v1",
        devices=[
            DeviceRequirement(role="INPUT_ARM", min_count=1, max_count=1),
            DeviceRequirement(role="CONVEYOR", min_count=1, max_count=1),
            DeviceRequirement(role="OUTPUT_ARM", min_count=1, max_count=1),
        ],
        positions=[
            Position(code="SINGLE_LAYER_A", role="CLASSIFIER_WORK", station_code="CLASSIFIER_WORK_POSITION"),
        ],
        topology=TopologySpec(
            flow=[
                FlowEdge(from_node="CLASSIFIER_WORK_POSITION", to_node="INPUT_ARM", type="OPERATION"),
                FlowEdge(from_node="INPUT_ARM", to_node="CONVEYOR", type="MATERIAL_FLOW"),
                FlowEdge(from_node="CONVEYOR", to_node="OUTPUT_ARM", type="MATERIAL_FLOW"),
                FlowEdge(from_node="OUTPUT_ARM", to_node="CLASSIFIER_WORK_POSITION", type="OPERATION"),
            ],
            bindings=[
                PositionBinding(device_role="INPUT_ARM", command="PICK_AND_PUT", position="SINGLE_LAYER_A"),
                PositionBinding(device_role="OUTPUT_ARM", command="PUT_TO_BIN", position="SINGLE_LAYER_A"),
            ],
        ),
        commands=["PICK_AND_PUT", "MOVE_FORWARD", "PUT_TO_BIN", "MOVE_TO_NG"],
        events=["SCAN_COMPLETED", "ROUGH_SORTER_STORAGE_RETRY"],
    )
    
    # 代码归 Plugin 类
    def resolve_business_key(self, payload): ...
    def classify_result(self, payload): ...
    def resolve_material_identity(self, input): ...
    ng_reason_catalog = [...]
```

#### API 响应模型（`workline.py`）

```python
class WorkLinePluginManifestSummary(BaseModel):
    plugin_key: str
    contract_version: str
    devices: list[DeviceRequirementOption]
    positions: list[PositionSummary]
    topology: TopologySpecSummary | None
    commands: list[str]
    events: list[str]
```

#### Service 层（`workline_service.py`）

```python
def get_plugin_manifest_summary(self, plugin_key: str) -> WorkLinePluginManifestSummary | None:
    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return None

    manifest = definition.manifest
    return WorkLinePluginManifestSummary(
        plugin_key=plugin_key,
        contract_version=manifest.contract_version,
        devices=[DeviceRequirementOption(...) for d in manifest.devices],
        positions=[PositionSummary(...) for p in manifest.positions],
        topology=self._build_topology_summary(manifest.topology) if manifest.topology else None,
        commands=list(manifest.commands),
        events=list(manifest.events),
    )
```

### 2. 前端改动

#### 类型定义（`runtime.ts`）

```typescript
export interface WorkLinePluginManifestSummary {
  plugin_key: string
  contract_version: string
  devices: DeviceRequirementOption[]
  positions: PositionSummary[]
  topology: TopologySpecSummary | null
  commands: string[]
  events: string[]
}

export interface PositionSummary {
  code: string
  role: string
  station_code: string
}

export interface TopologySpecSummary {
  flow: FlowEdge[]
  bindings: PositionBinding[]
}

export interface FlowEdge {
  from_node: string
  to_node: string
  type: 'MATERIAL_FLOW' | 'OPERATION'
}

export interface PositionBinding {
  device_role: string
  command: string
  position: string
}
```

#### 使用方（`runtime-scene.ts`）

```typescript
// 之前：从 single_layer_boundaries 推导
const manifestBoundaries = manifest?.single_layer_boundaries ?? []

// 之后：直接使用 positions
const positions = manifest?.positions ?? []
```

### 3. 受影响文件清单

| 文件 | 改动类型 | 工作量 |
|------|----------|--------|
| `src/workline_runtime/plugin_manifest.py` | 重写 | 大 |
| `src/workline_plugins/rough_sorter/plugin.py` | 调整实例化 | 中 |
| `src/workline_plugins/smt_sorting_inbound/plugin.py` | 调整实例化 | 中 |
| `src/app/workline/models/workline.py` | API 响应模型 | 中 |
| `src/app/workline/services/workline_service.py` | 构建逻辑 | 小 |
| `src/app/workline/services/runtime_query_service.py` | 边界查询 | 小 |
| 前端 `src/types/runtime.ts` | 类型定义 | 中 |
| 前端 `src/utils/runtime-scene.ts` | 使用方 | 小 |
| 测试文件（约 5 个） | 更新 mock | 小 |

---

## Acceptance Criteria

1. `WorklinePluginManifest` 只包含 6 个字段：`plugin_key`, `contract_version`, `devices`, `positions`, `topology`, `commands`, `events`
2. 所有 callable 字段已移至 Plugin 类
3. `Position` 只有 3 个字段：`code`, `role`, `station_code`
4. 两个插件（`rough_sorter`, `smt_sorting_inbound`）可正常实例化并启动
5. API `/api/v1/workline/plugins/{plugin_key}/manifest` 返回完整可序列化的 JSON
6. 前端可正确解析新的 manifest 结构
7. 所有现有测试通过

---

## Testing Plan

| Layer | What | Count |
|-------|------|-------|
| Unit | `WorklinePluginManifest` 字段验证 | +2 |
| Unit | `Position` 简化后字段验证 | +2 |
| Unit | `TopologySpec` 序列化/反序列化 | +2 |
| Integration | 插件实例化 + manifest 导出 | +2 |
| Integration | API 返回完整 JSON | +1 |
| E2E | 前端加载 manifest 并渲染拓扑 | +1 |

---

## Rollback Plan

由于是破坏性重构，rollback 需要：
1. 恢复 `plugin_manifest.py` 到重构前版本
2. 恢复两个插件的实例化代码
3. 恢复 API 响应模型
4. 前端同步回滚类型定义

**风险**：如果前端已经依赖新字段，rollback 会导致前端报错。

**缓解**：确保前后端同步发布，或前端做兼容处理。

---

## Effort Estimate

| Component | Effort |
|-----------|--------|
| 后端 manifest 重写 | 4h |
| 插件实例化调整（2 个） | 2h |
| API 响应模型 | 1h |
| Service 层调整 | 1h |
| 前端类型定义 | 1h |
| 测试更新 | 2h |
| **总计** | **11h** |

---

## Files Reference

| File | Change |
|------|--------|
| `src/workline_runtime/plugin_manifest.py` | 重写 manifest 定义 |
| `src/workline_plugins/rough_sorter/plugin.py:159-195` | 调整实例化 |
| `src/workline_plugins/smt_sorting_inbound/plugin.py:115-130` | 调整实例化 |
| `src/app/workline/models/workline.py:271-283` | API 响应模型 |
| `src/app/workline/services/workline_service.py:234-252` | 构建逻辑 |
| `src/app/workline/services/runtime_query_service.py:1420-1500` | 边界查询 |
| 前端 `src/types/runtime.ts` | 类型定义 |
| 前端 `src/utils/runtime-scene.ts:345-425` | 使用方 |

---

## Out of Scope

- 触发约束声明（`position_triggers`）— 属于业务逻辑，不放在 manifest
- 前端拓扑渲染优化 — 本次只改数据结构，不改渲染逻辑
- 其他插件迁移 — 当前只有 2 个插件，后续新增插件按新规范

---

## Related

- `docs/architecture/SRS.md` — 系统需求规格
- `docs/business/rough_sorter_runtime_flow.md` — 粗分机流程
- `docs/business/smt_sorter_inbound_workflow_guide.md` — 分拣机流程
