# 工作线货架位语义收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status:** Accepted as amendment to `docs/superpowers/plans/2026-06-12-workline-plugin-manifest-refactor.md`.
> Do not execute this as a standalone PR unless the main manifest refactor plan is explicitly split later. The canonical implementation tasks now live in the main plan and spec.

> **Superseded:** 本计划中“保持 `Position` / `NodeRefKind.POSITION` 公开合同名”的决策已被
> `docs/superpowers/plans/2026-06-14-workline-manifest-rack-position-rename.md` 反转。当前实现以
> `RackPosition` / `NodeRefKind.RACK_POSITION` / `rack_positions` 作为 manifest 静态合同名。

**Goal:** 将 WorkLine plugin manifest 中的 `positions` 从泛化物理位置收敛为 WES 管理的货架停靠位/库存事实锚点，避免扫码台、输送线内部点位和机器人中转位混入资源拓扑。

**Architecture:** 保持当前公开合同名 `Position` / `NodeRefKind.POSITION` 不做破坏性重命名，先通过注释、字段描述、validator 和真实插件测试收紧语义。`MATERIAL_FLOW` 表达货架位之间的库存/物料流向，`OPERATION` 表达设备角色与货架位的动作关系；硬件闭环内部节点只留在设备命令 payload 或插件业务逻辑中，不进入 `positions`。

**Tech Stack:** Python 3.13, dataclasses, Pydantic, pytest, ruff, GitNexus, uv。

---

## 背景与约定

外部设计文档 `/Users/kaizhou/.gemini/antigravity-cli/brain/4ce5f05b-47f1-4e17-b703-0d7be68deff7/outbound_rough_sorter_design.md` 的关键约定：

- WES 只做货架位资产拓扑建模与指令透传。
- 扫码台、防呆、防撞和机器人内部中转属于 PLC/硬件闭环，不定义成 `Position`。
- `positions` 只声明具有独立库存事实、需要 WMS 资源分配或 active snapshot 的货架位。
- `ResourceBoundary.position_code`、active snapshot、lease 和换托都以货架位为锚点。

本计划的实现边界：

- 本轮不做 API 字段级破坏性重命名，不把 `Position` 改名为 `RackPosition`。
- 本轮不实现新的 outbound rough sorter 插件，只把现有 manifest 合同和已实现插件调整到相同业务语义。
- 本轮不改变设备命令执行流程，只改变 manifest 的静态声明语义。

## 文件结构

- Modify: `src/workline_runtime/plugin_manifest.py`
  - 更新 `Position` / `PositionCarrierCapability` / `NodeRefKind.POSITION` / topology validator 的语义说明。
  - 增加 `MATERIAL_FLOW` 两端必须都是 `NodeRefKind.POSITION` 的合同校验。

- Modify: `src/app/workline/models/workline.py`
  - 更新 API manifest summary 的 Pydantic 描述，把“位置”明确为“货架停靠位/库存事实锚点”。

- Modify: `src/workline_plugins/rough_sorter/plugin.py`
  - 从 `positions` 移除扫码点、输送线入口、输送线出口、NG 物理位置。
  - 只保留 `SINGLE_LAYER_A` 这类真实货架资源位置。
  - 将内部点位参数从 `CommandBinding.position_args` 中剥离，保留由业务 payload 构建的命令细节。

- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
  - 调整 topology：`MATERIAL_FLOW` 只连接 position 到 position；设备到 position 的关系使用 `OPERATION`。
  - 保持现有五层架/单层架能力约束和 seed 兼容性。

- Modify: `docs/plugin_development_guide.md`
  - 明确 `positions` 是货架停靠位，不是所有物理位置。
  - 明确扫码台、传感器、输送线内部点、机器人临时中转位不得进入 `positions`。

- Modify: `docs/templates/workline_plugin/plugin.py.tmpl`
  - 模板示例使用 rack position 命名和注释。
  - 模板 topology 遵守 `MATERIAL_FLOW` position-to-position 规则。

- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
  - 新增合同测试：material flow 必须是 position-to-position。
  - 新增真实插件测试：`rough_sorter` 不再声明扫码/输送线内部 position。
  - 调整真实插件断言，允许内部动作命令没有 `position_args`。

- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
  - 增加 SMT topology 语义测试，防止 device-role 被误用为 `MATERIAL_FLOW` 端点。

- Modify: `tests/workline_plugins/test_plugin_template_assets.py`
  - 补充模板检查，防止模板重新引入内部物理点位作为 position。

## 风险与保护

- 这是 manifest 合同语义收紧，会影响插件 manifest、API summary 和开发模板。
- 修改前必须按项目规则运行 GitNexus impact analysis。
- 如果 GitNexus 对 `WorklinePluginManifest`、`RoughSorterPlugin`、`SmtSortingInboundPlugin`、`validate_topology_manifest` 任一返回 HIGH/CRITICAL，先停止并向用户汇报。

---

### Task 1: 锁定 rack-position-only 合同测试

**Files:**
- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`

- [ ] **Step 1: 运行 GitNexus impact analysis**

Call:

```text
gitnexus_impact({target: "WorklinePluginManifest", direction: "upstream"})
gitnexus_impact({target: "RoughSorterPlugin", direction: "upstream"})
gitnexus_impact({target: "SmtSortingInboundPlugin", direction: "upstream"})
```

Expected:

```text
risk <= MEDIUM
```

如果风险为 HIGH 或 CRITICAL，暂停实现并汇报影响面。

- [ ] **Step 2: 写失败测试：material flow 只能连接货架位**

在 `tests/workline_runtime/test_plugin_manifest_and_topology.py` 新增 `test_material_flow_edges_must_connect_positions`。

关键断言：当 `MATERIAL_FLOW` 的任一端点是 `DEVICE_ROLE` 时，manifest 构造必须失败，错误信息要包含 `MATERIAL_FLOW`。

Expected failure before implementation:

```text
Failed: DID NOT RAISE <class 'ValueError'>
```

- [ ] **Step 3: 写失败测试：rough_sorter 不声明内部物理点位**

同文件调整 `test_rough_sorter_real_manifest_declares_new_contract_shape`。

关键断言：rough sorter manifest `positions` 只包含 WES 货架资源位；`ROUGH_SORTER_SCAN_POINT`、`PIPELINE-IN-01`、`PIPELINE-OUT-01`、`NG-01` 不得出现在 `positions`。

Expected failure before implementation:

```text
AssertionError: Extra items in the left set
```

- [ ] **Step 4: 写失败测试：rough_sorter 内部动作命令不强制 position_args**

同文件调整 command 断言。

关键断言：`PICK_AND_PUT`、`MOVE_FORWARD`、`MOVE_TO_NG` 的 `position_args` 为空；`PUT_TO_BIN` 仍保留 WES 货架/资源位置参数。

Expected failure before implementation:

```text
AssertionError: left contains PositionArg(...)
```

- [ ] **Step 5: 运行目标测试，确认失败集中在新断言**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py -q
```

Expected:

```text
FAILED test_material_flow_edges_must_connect_positions
FAILED test_rough_sorter_real_manifest_declares_new_contract_shape
```

---

### Task 2: 收紧 manifest topology validator 和语义注释

**Files:**
- Modify: `src/workline_runtime/plugin_manifest.py`
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`

- [ ] **Step 1: 实现 topology 合同校验**

在 `WorklinePluginManifest._validate_topology_refs` 中保留现有引用完整性校验，并追加规则：`MATERIAL_FLOW` 的 `from_node` 和 `to_node` 都必须是 `NodeRefKind.POSITION`，否则抛出包含 `MATERIAL_FLOW` 的 `ValueError`。

- [ ] **Step 2: 更新 manifest 合同注释**

在 `src/workline_runtime/plugin_manifest.py` 中把核心注释调整为以下语义：`PositionCarrierCapability` 描述货架停靠位可承载能力；`Position` 是库存事实、资源边界和 runtime overlay 的锚点；`TopologySpec` 的 `MATERIAL_FLOW` 只表达货架位之间的物料流。

- [ ] **Step 3: 运行合同测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py::test_material_flow_edges_must_connect_positions -q
```

Expected:

```text
1 passed
```

---

### Task 3: 精简 rough_sorter manifest positions

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`

- [ ] **Step 1: 移除内部物理点位 positions**

在 `RoughSorterPlugin.manifest.positions` 中只保留货架资源位 `POSITION_WORK_SINGLE_LAYER`，其业务角色仍对应 `CLASSIFIER_WORK`。

同时保留常量 `DEFAULT_NG_LOCATION`、`DEFAULT_PIPELINE_INPUT_LOCATION`、`DEFAULT_PIPELINE_OUTPUT_LOCATION`，因为它们仍可能用于命令 payload 或插件业务逻辑；不要再把它们声明为 manifest position。

- [ ] **Step 2: 精简 rough_sorter topology**

将 topology 改成只表达设备对货架位的动作关系。关键目标：设备角色到 `POSITION_WORK_SINGLE_LAYER` 的关系使用 `FlowEdgeType.OPERATION`。

如果需要保留输入机械臂与货架资源位的关系，只能使用 `FlowEdgeType.OPERATION`，不能使用 `MATERIAL_FLOW` 连接 device-role。

- [ ] **Step 3: 精简 rough_sorter command position_args**

内部扫码点、输送线入口/出口、NG 点不再通过 `PositionArg.position_ref` 声明。关键目标：`ACTION_PICK_AND_PUT`、`ACTION_MOVE_FORWARD`、`ACTION_MOVE_TO_NG` 保留命令目标设备与结果绑定，但不声明内部物理点位参数。

`ACTION_PUT_TO_BIN` 继续保留目标货架位相关参数：`bin_location` 的 `TARGET` 语义来自资源投影路径，fallback 指向 `POSITION_WORK_SINGLE_LAYER`。

- [ ] **Step 4: 运行 rough_sorter 合同测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py::test_rough_sorter_real_manifest_declares_new_contract_shape -q
```

Expected:

```text
1 passed
```

---

### Task 4: 调整 SMT topology 以匹配 MATERIAL_FLOW 语义

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`

- [ ] **Step 1: 写 SMT topology 语义测试**

在 `tests/workline_runtime/test_smt_sorting_inbound_plugin.py` 新增 `test_smt_sorting_inbound_material_flow_edges_are_position_to_position`。

关键断言：SMT inbound manifest 中所有 `MATERIAL_FLOW` 边的两端都必须是 `POSITION`。

Expected failure before implementation:

```text
AssertionError: assert 'DEVICE_ROLE' == 'POSITION'
```

- [ ] **Step 2: 调整 SMT topology**

将设备到目标/NG 货架位的 `MATERIAL_FLOW` 改为 `OPERATION`，并补充 position-to-position 的业务流向。

关键目标：工作位到目标位、工作位到 NG 位的库存/物料流使用 `MATERIAL_FLOW`；设备角色到目标/NG 位的动作关系继续使用 `OPERATION`。

- [ ] **Step 3: 运行 SMT 插件测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_plugin_manifest_and_topology.py::test_smt_sorting_inbound_real_manifest_declares_new_contract_shape -q
```

Expected:

```text
all selected tests passed
```

---

### Task 5: 同步 API summary、开发指南和模板

**Files:**
- Modify: `src/app/workline/models/workline.py`
- Modify: `docs/plugin_development_guide.md`
- Modify: `docs/templates/workline_plugin/plugin.py.tmpl`
- Modify: `tests/workline_plugins/test_plugin_template_assets.py`

- [ ] **Step 1: 更新 API summary 描述**

在 `src/app/workline/models/workline.py` 中更新描述文字。目标语义：`PositionCarrierCapability` 描述货架停靠位可承载能力；`Position` 描述插件声明的货架停靠位；`code`、`role`、`station_code` 的字段说明都要指向货架停靠位/插件内 station 语义。

- [ ] **Step 2: 更新插件开发指南**

在 `docs/plugin_development_guide.md` 的 manifest 说明处写入约定：`positions` 只声明 WES 管理的货架停靠位，也就是库存事实、active snapshot、resource boundary 和 runtime overlay 的锚点；扫码台、传感器、输送线内部点、机器人临时中转位属于设备/硬件闭环，不进入 `positions`。

- [ ] **Step 3: 更新插件模板**

在 `docs/templates/workline_plugin/plugin.py.tmpl` 中确保示例 `Position` 使用货架停靠位语义，且模板中的 `FlowEdgeType.MATERIAL_FLOW` 只连接 `NodeRefKind.POSITION`。

- [ ] **Step 4: 更新模板资产测试**

在 `tests/workline_plugins/test_plugin_template_assets.py` 增加检查：模板保留 `FlowEdgeType.MATERIAL_FLOW` 和 `NodeRefKind.POSITION`，但不得出现 `SCAN_POSITION` 或 `PIPELINE_POSITION` 这类内部物理点位示例。

- [ ] **Step 5: 运行文档/模板相关测试**

Run:

```bash
uv run pytest tests/workline_plugins/test_plugin_template_assets.py -q
```

Expected:

```text
all selected tests passed
```

---

### Task 6: 回归验证与提交

**Files:**
- No new production files.
- Verify all files modified above.

- [ ] **Step 1: 运行核心回归**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_plugins/test_plugin_template_assets.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 2: 运行格式和 lint**

Run:

```bash
uv run ruff format src/workline_runtime/plugin_manifest.py src/app/workline/models/workline.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_plugins/test_plugin_template_assets.py
uv run ruff check src/workline_runtime/plugin_manifest.py src/app/workline/models/workline.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_plugins/test_plugin_template_assets.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: 运行 GitNexus detect changes**

Call:

```text
gitnexus_detect_changes()
```

Expected:

```text
changed symbols only cover manifest contract, real plugin manifest declarations, API summary models, docs/templates, and related tests
```

- [ ] **Step 4: 检查 git diff**

Run:

```bash
git diff -- src/workline_runtime/plugin_manifest.py src/app/workline/models/workline.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py docs/plugin_development_guide.md docs/templates/workline_plugin/plugin.py.tmpl tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_plugins/test_plugin_template_assets.py
```

Expected:

```text
diff only contains rack-position semantic tightening and related test/doc updates
```

- [ ] **Step 5: 提交**

Run:

```bash
git add src/workline_runtime/plugin_manifest.py src/app/workline/models/workline.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py docs/plugin_development_guide.md docs/templates/workline_plugin/plugin.py.tmpl tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_plugins/test_plugin_template_assets.py
git commit -m "refactor(workline): 收敛 manifest 货架位语义"
```

Expected:

```text
[feature/workline-plugin-manifest-refactor <sha>] refactor(workline): 收敛 manifest 货架位语义
```

## 自检

- 设计覆盖：已覆盖 `positions` 货架位语义、扫码台/输送线内部点排除、`ResourceBoundary` 货架位锚点、`MATERIAL_FLOW` position-to-position、真实插件和模板同步。
- 占位扫描：本文不包含待补内容标记；实现步骤均有目标文件、关键变更、命令和预期结果。
- 类型一致性：保持当前公开类型 `Position`、`PositionCarrierCapability`、`TopologySpec.flow_edges`、`NodeRefKind.POSITION`、`FlowEdgeType.MATERIAL_FLOW` 不变；本轮只收紧语义和校验。
