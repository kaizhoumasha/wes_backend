# Workline Plugin Manifest 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `WorklinePluginManifest` 重构为可序列化纯数据合同，并同步后端 runtime consumers、API/OpenAPI、前端 generated contract、runtime scene、插件模板和清理门禁。

**Architecture:** Manifest 只保留 `plugin_key + contract_version + devices + positions + topology + commands + events + resource_boundaries` 八类静态事实。所有 callable/type/runtime 行为迁移到 Plugin 类并通过 registry helper 统一进入，前端只消费 OpenAPI 生成的合同和 manifest detail，不从 plugin options 读取能力事实。

**Tech Stack:** Python 3.13, dataclasses, Pydantic/FastAPI, pytest, GitNexus impact analysis, Vue 3, TypeScript, Vitest, OpenAPI generated types/zod schemas.

---

## 项目约束

- 所有沟通、文档和 commit comment 使用中文；命令使用 `uv run ...`。
- 修改任何函数、类或方法前，必须运行 GitNexus impact analysis；HIGH/CRITICAL 风险先汇报用户。
- 每个 `git commit` 步骤都必须在 `git add` 后、`git commit` 前运行 GitNexus detect changes，确认 staged 变更范围符合当前任务。
- 本仓库 `AGENTS.md` 禁止在计划文档粘贴完整类、完整函数或大段测试代码。本计划给出精确文件、符号、测试名称、断言、字段合同和命令；实现代码在执行阶段通过 TDD diff 落地。
- 本次是未发布系统的破坏性清理，不保留旧 manifest 兼容层。
- 前端仓库路径：`/Users/kaizhou/SynologyDrive/works/wes_frontend`。后端仓库路径：`/Users/kaizhou/SynologyDrive/works/wes_backend`。

## Scope Check

该 spec 同时触及后端插件合同、运行时服务、API schema、前端合同和模板文档，但核心变更是一个 shared contract 的破坏性迁移。拆成多个独立实现会制造新旧合同并存风险，因此按一个计划执行；任务边界按可测试层拆分。

## File Structure

### 后端核心合同

- Modify: `src/workline_runtime/plugin_manifest.py`
  - 负责纯数据 dataclass/enum 合同、字段归一化、引用完整性辅助校验、旧导出清理。
- Modify: `src/workline_runtime/topology.py`
  - 负责 `WorklineTopologyView` 与新 manifest 约束校验，不再读取 `required_device_roles/event_source_roles/command_target_roles`。
- Modify: `src/workline_runtime/__init__.py`
  - 只导出新 manifest 合同类型，删除旧类型别名和旧 boundary 导出。

### 后端 registry/runtime helper

- Modify: `src/workline_plugin_registry.py`
  - 负责 registry 唯一插件实例、manifest shape 校验、business key/result/context/material/NG helper。
- Modify: `src/workline_runtime/orchestrator.py`
  - 删除 `_plugin_instance_cache`，改用 `WorklinePluginDefinition.plugin_instance`。
- Modify: `src/workline_runtime/session_resolver.py`
  - 保持业务键解析入口不变，但内部说明和 helper 调用指向 registry runtime 能力。
- Modify: `src/workline_runtime/plugin_sdk/classifiers/result_classifier.py`
- Modify: `src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py`
  - 清理旧 manifest callable 类型依赖。

### 插件实现

- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
  - manifest 只声明纯数据；Plugin 类公开 runtime 能力方法/属性。
- Read/keep aligned: `src/workline_plugins/rough_sorter/contract.py`
- Read/keep aligned: `src/workline_plugins/smt_sorting_inbound/constants.py`
  - 作为事件、命令、角色、动作参数来源。

### 后端 Workline service/runtime consumers

- Modify: `src/app/workline/models/workline.py`
  - 更新 `WorkLinePluginOption` selector-only 与 `WorkLinePluginManifestSummary` 新合同 schema。
- Modify: `src/app/workline/services/workline_service.py`
  - 更新 options、manifest summary、配置检查、设备/事件/命令/承载约束校验。
- Modify: `src/app/workline/services/inbox_batch_processor.py`
  - `ENTRY_DEVICE` 入口事件来源改读 `EventBinding.category`。
- Modify: `src/app/workline/services/operation_service.py`
  - sandbox event template 改读结构化 `EventBinding`。
- Modify: `src/app/workline/services/runtime_query_service.py`
  - `single_layer_boundaries` 迁移为通用 `resource_boundaries`。
- Modify: `src/app/workline/services/runtime_hold_release_service.py`
- Modify: `src/app/workline/services/ng_return_item_service.py`
- Modify: `src/app/workline/services/runtime_hold_query_service.py`
  - material identity / NG reason 改用 registry helper。

### 后端测试与模板

- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Modify: `tests/workline_runtime/test_reserved_runtime_events.py`
- Modify: `tests/workline_runtime/test_plugin_single_layer_rack_boundary.py`
- Modify: `tests/test_workline_service_plugin_validation.py`
- Modify: `tests/test_workline_routes.py`
- Modify: `tests/workline_runtime/test_inbox_batch_processor.py`
- Modify: `tests/workline_runtime/test_workline_operation_service.py`
- Modify: `tests/workline_runtime/test_runtime_query_service.py`
- Modify: `tests/workline_runtime/test_runtime_hold_release_service.py`
- Modify: `tests/workline_runtime/test_ng_return_item_service.py`
- Modify: `tests/workline_runtime/test_ng_reason_catalog.py`
- Modify: `tests/workline_runtime/test_material_identity.py`
- Modify: `tests/helpers/workline_test_plugin.py`
- Create: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`
- Modify: `docs/templates/workline_plugin/plugin.py.tmpl`
- Modify: `docs/templates/workline_plugin/tests.py.tmpl`
- Modify: `docs/templates/workline_plugin/README.md`
- Modify: `docs/plugin_development_guide.md`
- Modify: `tests/workline_plugins/test_plugin_template_assets.py`

### 前端合同与消费方

- Generated after backend OpenAPI update:
  - `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-types.ts`
  - `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/generated/zod-schemas.ts`
  - `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-metadata/WorkLinePluginManifestSummary.ts`
  - `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-metadata/WorkLinePluginOption.ts`
  - `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-metadata/index.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/runtime.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/utils/runtime-scene.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/views/admin/worklines/config/WorkLineConfigPage.vue`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/composables/useRuntimeSceneManifest.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/utils/runtime-scene.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/utils/runtime-scene.regression-1.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/composables/useRuntimeSceneManifest.test.ts`
- Create: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts`

## Contract Decisions To Preserve

新 manifest 顶层字段只允许：

```text
plugin_key, contract_version, devices, positions, topology, commands, events, resource_boundaries
```

关键结构约束：

```text
DeviceRequirement(role, min_count, max_count, hardware_capabilities)
Position(code, role, station_code, carrier_capability)
PositionCarrierCapability(allowed_rack_kinds, min_capacity, max_capacity, allowed_slot_kinds)
NodeRef(kind: DEVICE_ROLE | POSITION, ref)
FlowEdge(from_node, to_node, type: MATERIAL_FLOW | OPERATION)
EventBinding(event, source_device_roles, category, payload_schema_ref)
CommandBinding(command, target_device_role, position_args, payload_schema_ref, result_bindings)
PositionArg(name, role, required, position_ref, source)
PositionArgSource(kind: EVENT_PAYLOAD | SESSION_CONTEXT | COMMAND_PAYLOAD | RESOURCE_OVERLAY, path, fallback_position_ref)
CommandResultBinding(result, event, category: COMMAND_RESULT, classification, terminal, next_event)
ResourceBoundary(position_code, rack_kind, business_demand_type, wms_operation_type, snapshot_kind, lease_scope)
```

`ResourceBoundary` 不变量：

```text
position_code -> must exist in positions
rack_kind     -> must be allowed by that Position.carrier_capability.allowed_rack_kinds
never         -> station_code/station_role duplicated on ResourceBoundary
```

`PositionArg` 不变量：

```text
required=True  -> exactly one of position_ref/source
required=False -> zero or one of position_ref/source
never          -> both position_ref and source
STATIC source  -> invalid; static position uses position_ref
```

---

### Task 0: 执行前准备与影响面确认

**Files:**
- Read: `docs/superpowers/specs/2026-06-12-workline-plugin-manifest-refactor.md`
- Read: `AGENTS.md`
- No code changes

- [ ] **Step 1: 确认当前分支和脏工作区**

Run:

```bash
git branch --show-current
git status --short
```

Expected:

```text
develop
```

已有 `.gitignore`、`AGENTS.md`、`CLAUDE.md` 等非本计划改动不得回滚；只操作本任务列出的文件。

- [ ] **Step 2: 建议为实施创建隔离 worktree**

Run from backend repo root when isolation is needed:

```bash
mkdir -p ../worktrees/wes_backend
git worktree add ../worktrees/wes_backend/feature-workline-plugin-manifest-refactor -b feature/workline-plugin-manifest-refactor develop
cd ../worktrees/wes_backend/feature-workline-plugin-manifest-refactor
./scripts/init-env.sh dev
uv sync --dev
```

Expected: worktree 创建成功，后续所有后端命令在该 worktree 内执行。

- [ ] **Step 3: 记录必须先跑的 GitNexus impact targets**

Before editing symbols, run impact analysis for these targets in the agent environment:

```text
gitnexus_impact({target: "WorklinePluginManifest", direction: "upstream"})
gitnexus_impact({target: "validate_topology_manifest", direction: "upstream"})
gitnexus_impact({target: "WorklinePluginDefinition", direction: "upstream"})
gitnexus_impact({target: "resolve_workline_business_key", direction: "upstream"})
gitnexus_impact({target: "classify_workline_result", direction: "upstream"})
gitnexus_impact({target: "WorkLinePluginOption", direction: "upstream"})
gitnexus_impact({target: "WorkLinePluginManifestSummary", direction: "upstream"})
gitnexus_impact({target: "list_plugin_options", direction: "upstream"})
gitnexus_impact({target: "get_plugin_manifest_summary", direction: "upstream"})
gitnexus_impact({target: "_generate_event_templates_from_supported_events", direction: "upstream"})
```

Expected: no HIGH/CRITICAL unreported risk. If HIGH/CRITICAL appears, pause and report blast radius before editing.

---

### Task 1: Backend Manifest Contract Tests First

**Files:**
- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Modify: `tests/workline_runtime/test_reserved_runtime_events.py`
- Modify: `tests/workline_runtime/test_plugin_single_layer_rack_boundary.py`
- Create: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`

- [ ] **Step 1: Run impact analysis for manifest/topology symbols**

Run in agent environment:

```text
gitnexus_impact({target: "WorklinePluginManifest", direction: "upstream"})
gitnexus_impact({target: "validate_topology_manifest", direction: "upstream"})
```

Expected: impact is understood and no unreported HIGH/CRITICAL risk remains.

- [ ] **Step 2: Replace old manifest-shape tests with new contract tests**

In `tests/workline_runtime/test_plugin_manifest_and_topology.py`, keep `_device(...)` helper and rewrite manifest tests to cover these names and assertions:

| Test name | Required assertion |
|-----------|--------------------|
| `test_manifest_accepts_complete_pure_data_contract` | manifest exposes exactly the 8 top-level fields and has no callable/type fields |
| `test_manifest_rejects_missing_required_topology` | missing or `None` topology raises `ValueError` mentioning `topology` |
| `test_manifest_rejects_unknown_topology_node_ref` | unknown `DEVICE_ROLE` or `POSITION` ref raises `ValueError` |
| `test_manifest_rejects_illegal_flow_edge_type` | edge type outside `MATERIAL_FLOW` / `OPERATION` raises `ValueError` |
| `test_event_binding_rejects_unknown_source_role` | event source role must exist in `devices` |
| `test_event_binding_entry_device_is_only_entry_filter_source` | entry admission candidates derive only from `EventCategory.ENTRY_DEVICE` |
| `test_command_binding_rejects_unknown_target_role` | command target role must exist in `devices` |
| `test_command_result_binding_requires_command_result_category` | command result binding category must be `COMMAND_RESULT` |
| `test_position_arg_position_ref_and_source_are_mutually_exclusive` | both-set invalid, required both-missing invalid, optional both-missing valid |
| `test_position_arg_source_rejects_static_kind` | `PositionArgSource.kind="STATIC"` raises `ValueError` |
| `test_position_carrier_capability_validates_capacity_and_rack_kind` | invalid capacity range and invalid rack kind fail |
| `test_resource_boundary_references_position_and_omits_station_fields` | unknown `position_code` fails and resource boundary has no station duplication |
| `test_resource_boundary_rack_kind_must_match_position_carrier_capability` | boundary rack kind not listed by its position's `allowed_rack_kinds` fails |

Use short fixture builders in the test file; do not keep assertions against `required_device_roles`, `event_source_roles`, `command_target_roles`, `supported_events`, `supported_commands`, or `single_layer_boundaries`. Cleanup/negative tests that need removed token names must build them from fragments (for example `("supported", "_events")`) or be excluded from explicit `rg` scans, so the cleanup gate does not match itself.

- [ ] **Step 3: Rewrite reserved runtime event tests to use `events`**

In `tests/workline_runtime/test_reserved_runtime_events.py`, replace old `supported_events` / `event_source_roles` cases with:

| Test name | Required assertion |
|-----------|--------------------|
| `test_manifest_rejects_reserved_runtime_event_binding_event` | `EventBinding(event="ESTOP_PRESSED", ...)` fails |
| `test_manifest_rejects_reserved_runtime_command_result_event` | `CommandResultBinding(event="WORKLINE_START_REQUESTED", ...)` fails |

- [ ] **Step 4: Convert old single-layer boundary tests into generic resource boundary tests**

In `tests/workline_runtime/test_plugin_single_layer_rack_boundary.py`, replace `SingleLayerRackBoundary` tests with:

| Test name | Required assertion |
|-----------|--------------------|
| `test_resource_boundary_accepts_single_layer_and_five_layer_kinds` | one manifest can declare both rack kinds |
| `test_resource_boundary_derives_station_from_position` | station facts come from `positions`, not boundary fields |
| `test_registered_plugins_declare_resource_boundaries_for_rack_operations` | rough/smt plugins expose non-empty `resource_boundaries` where needed |
| `test_smt_manifest_declares_five_layer_resource_boundary` | SMT inbound includes `FIVE_LAYER` boundary coverage |

- [ ] **Step 5: Add cleanup gate test**

Create `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py` with one test that scans active paths only:

```text
src/
tests/
docs/templates/
docs/plugin_development_guide.md
```

The test must fail if old symbols remain outside explicit allowlist comments in this migration plan/spec:

```text
required_device_roles, DeviceRoleRequirement, event_source_roles, command_target_roles,
supported_events, supported_commands, capabilities (manifest级), 旧 DeviceRequirement.capabilities,
旧 Position.capabilities, 旧 PositionCarrierCapability.capacity, 扁平 CommandBinding.position_ref,
旧 CommandBinding.result_event, 旧 PositionArg.runtime_source, TopologySpec | None, resource_kinds,
requires_single_layer_boundary, single_layer_boundaries, SingleLayerRackBoundary,
business_key_resolver, result_classifier, context_model, material_identity_resolver,
ng_reason_catalog, BusinessKeyResolver, ResultClassifier, _looks_like_manifest,
_ALLOWED_SINGLE_LAYER_, _requires_single_layer_boundaries, 旧 __all__ 导出项
```

- [ ] **Step 6: Run red tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_reserved_runtime_events.py \
  tests/workline_runtime/test_plugin_single_layer_rack_boundary.py \
  tests/workline_runtime/test_plugin_manifest_cleanup_gate.py \
  -q
```

Expected: FAIL before implementation with missing new symbols, old field assertions, or cleanup-gate findings.

---

### Task 2: Implement Pure Manifest Data Contract

**Files:**
- Modify: `src/workline_runtime/plugin_manifest.py`
- Modify: `src/workline_runtime/topology.py`
- Modify: `src/workline_runtime/__init__.py`
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Test: `tests/workline_runtime/test_reserved_runtime_events.py`

- [ ] **Step 1: Run impact analysis**

Run:

```text
gitnexus_impact({target: "WorklinePluginManifest", direction: "upstream"})
gitnexus_impact({target: "validate_topology_manifest", direction: "upstream"})
```

Expected: proceed only after risks are understood.

- [ ] **Step 2: Replace old manifest dataclasses**

In `src/workline_runtime/plugin_manifest.py`, remove old callable/type/boundary definitions and define only these public contracts:

```text
NodeRefKind, FlowEdgeType, EventCategory, PositionArgRole, PositionArgSourceKind
DeviceRequirement
PositionCarrierCapability
Position
NodeRef
FlowEdge
TopologySpec
EventBinding
PositionArgSource
PositionArg
CommandResultBinding
CommandBinding
ResourceBoundary
WorklinePluginManifest
```

`__all__` must contain only the new symbols. Delete `BusinessKeyResolver`, `ResultClassifier`, `DeviceRoleRequirement`, `SingleLayerRackBoundary`, `MaterialIdentityResolver` re-export, and old helper constants.

- [ ] **Step 3: Implement validation invariants**

In `WorklinePluginManifest.__post_init__`, enforce:

```text
plugin_key / contract_version non-empty
devices / positions / topology present
device roles unique
position codes unique
events source roles exist in devices
commands target roles exist in devices
command result binding category == COMMAND_RESULT
PositionArg position_ref/source invariant
PositionArgSource.kind excludes STATIC
position_ref and resource boundary position_code exist in positions
resource boundary rack_kind exists in that position's carrier_capability.allowed_rack_kinds
NodeRef DEVICE_ROLE exists in devices
NodeRef POSITION exists in positions
FlowEdge.type in MATERIAL_FLOW / OPERATION
resource boundary has no station_code/station_role fields
reserved runtime events are rejected through EventBinding and CommandResultBinding
```

- [ ] **Step 4: Update topology validation to read new fields**

In `src/workline_runtime/topology.py`, change `validate_topology_manifest` to:

```text
iterate manifest.devices
validate device hardware capabilities from DeviceRequirement.hardware_capabilities
iterate manifest.events for source device support
iterate manifest.commands for target device support
reuse manifest's own static topology validation rather than mutating manifest fields
```

No line may assign `manifest.event_source_roles = {}` or `manifest.command_target_roles = {}`.

- [ ] **Step 5: Update runtime package exports**

In `src/workline_runtime/__init__.py`, export the new contract symbols and remove old ones. Keep existing unrelated exports untouched.

- [ ] **Step 6: Run focused manifest tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_reserved_runtime_events.py \
  -q
```

Expected: PASS for manifest/topology/reserved-event tests.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/workline_runtime/plugin_manifest.py src/workline_runtime/topology.py src/workline_runtime/__init__.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_reserved_runtime_events.py
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 重写插件 manifest 纯数据合同"
```

Expected: commit succeeds after focused tests pass.

---

### Task 3: Registry Singleton And Runtime Helpers

**Files:**
- Modify: `src/workline_plugin_registry.py`
- Modify: `src/workline_runtime/orchestrator.py`
- Modify: `src/workline_runtime/session_resolver.py`
- Modify: `src/workline_runtime/plugin_sdk/classifiers/result_classifier.py`
- Modify: `src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py`
- Test: `tests/workline_runtime/test_plugin_base.py`
- Test: `tests/workline_runtime/test_session_resolver.py`
- Test: `tests/workline_runtime/test_material_identity.py`
- Test: `tests/workline_runtime/test_ng_reason_catalog.py`

- [ ] **Step 1: Run impact analysis**

Run:

```text
gitnexus_impact({target: "WorklinePluginDefinition", direction: "upstream"})
gitnexus_impact({target: "resolve_workline_business_key", direction: "upstream"})
gitnexus_impact({target: "classify_workline_result", direction: "upstream"})
gitnexus_impact({target: "_load_plugin", direction: "upstream"})
```

Expected: proceed only after risk review.

- [ ] **Step 2: Add registry helper tests**

Add or update tests with these assertions:

| Test file | Test name | Required assertion |
|-----------|-----------|--------------------|
| `tests/workline_runtime/test_plugin_base.py` | `test_registry_definition_returns_single_plugin_instance` | two calls to `definition.plugin_instance` return same object |
| `tests/workline_runtime/test_session_resolver.py` | `test_resolve_business_key_uses_registry_plugin_runtime` | business key comes from Plugin runtime method, not manifest callable |
| `tests/workline_runtime/test_material_identity.py` | `test_registry_material_identity_helper_returns_missing_default` | missing plugin capability returns existing MISSING material identity semantics |
| `tests/workline_runtime/test_ng_reason_catalog.py` | `test_registry_ng_reason_helper_returns_empty_catalog_default` | plugin without catalog returns empty tuple |

- [ ] **Step 3: Implement `WorklinePluginDefinition.plugin_instance`**

In `src/workline_plugin_registry.py`:

```text
plugin_class remains lazy
manifest remains lazy but shape check expects new 8-field manifest
plugin_instance caches exactly one plugin object per definition
_looks_like_manifest is deleted
```

Use a private module-level cache keyed by `plugin_key` or a cached attribute on `WorklinePluginDefinition`; choose one stable registry-owned location, not orchestrator.

- [ ] **Step 4: Add registry runtime helpers**

In `src/workline_plugin_registry.py`, provide helpers with these names:

```text
resolve_workline_business_key(plugin_key, payload_json)
classify_workline_result(plugin_key, payload_json)
get_workline_context_model(plugin_key)
resolve_workline_material_identity(plugin_key, input_value)
list_workline_ng_reasons(plugin_key)
```

Semantics:

```text
unknown plugin -> None for business/result/context
unknown plugin or no material resolver -> MISSING MaterialIdentity
unknown plugin or no NG reason catalog -> empty tuple
helper calls Plugin instance capability, never manifest callable
```

- [ ] **Step 5: Remove orchestrator plugin cache**

In `src/workline_runtime/orchestrator.py`:

```text
delete _plugin_instance_cache
update _load_plugin to delegate registered classes through registry instance when possible
preserve null_plugin opt-in behavior
update comments that mention cached plugin_class map
```

- [ ] **Step 6: Update session resolver and SDK comments/imports**

In `src/workline_runtime/session_resolver.py`, update comments to say registry Plugin runtime resolves business keys. In SDK classifier/normalizer modules, remove imports/types that reference old manifest callable aliases.

- [ ] **Step 7: Run focused registry tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_base.py \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_material_identity.py \
  tests/workline_runtime/test_ng_reason_catalog.py \
  -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/workline_plugin_registry.py src/workline_runtime/orchestrator.py src/workline_runtime/session_resolver.py \
  src/workline_runtime/plugin_sdk/classifiers/result_classifier.py src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py \
  tests/workline_runtime/test_plugin_base.py tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_material_identity.py tests/workline_runtime/test_ng_reason_catalog.py
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 统一插件实例与运行时 helper"
```

Expected: commit succeeds after focused tests pass.

---

### Task 4: Migrate Real Plugin Manifests

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/workline_plugins/test_rough_sorter_plugin.py`

- [ ] **Step 1: Run impact analysis**

Run:

```text
gitnexus_impact({target: "RoughSorterPlugin", direction: "upstream"})
gitnexus_impact({target: "SmtSortingInboundPlugin", direction: "upstream"})
```

Expected: plugin blast radius is understood.

- [ ] **Step 2: Add golden sample tests for both plugins**

In manifest/topology plugin tests, assert:

```text
RoughSorterPlugin.manifest has 8 top-level fields
SmtSortingInboundPlugin.manifest has 8 top-level fields
old fields are absent on both manifest objects
topology is non-empty and uses typed NodeRef
events contain expected source roles and categories
commands contain target device role, position_args, and result_bindings
resource_boundaries cover rough single-layer work position
resource_boundaries cover SMT source, target, NG/work, and FIVE_LAYER needs
```

- [ ] **Step 3: Migrate rough sorter manifest**

In `src/workline_plugins/rough_sorter/plugin.py`:

```text
replace DeviceRoleRequirement -> DeviceRequirement
move business_key/result/context/material/NG capability to Plugin class methods/properties
replace supported_events/event_source_roles -> EventBinding list
replace supported_commands/command_target_roles -> CommandBinding list
replace single_layer_boundaries/resource_kinds/requires_single_layer_boundary -> positions + resource_boundaries
replace command action location facts with PositionArg role/source contracts
```

Keep existing business methods and action behavior; do not rewrite plugin workflow logic in this task.

- [ ] **Step 4: Migrate SMT sorting inbound manifest**

In `src/workline_plugins/smt_sorting_inbound/plugin.py`:

```text
replace DeviceRoleRequirement -> DeviceRequirement
move business_key/result/context/material/NG capability to Plugin class methods/properties
replace EVENT_SOURCE_ROLES -> EventBinding list
replace COMMAND_TARGET_ROLES -> CommandBinding list
declare source, target, workstation, NG station positions
declare resource_boundaries for SINGLE_LAYER and FIVE_LAYER rack kinds
ensure NG place command target remains TARGET_ARM when current behavior requires it
```

- [ ] **Step 5: Update plugin-local manifest references**

Replace `self.manifest.single_layer_boundaries` access in rough sorter allocation context with a helper that reads `manifest.resource_boundaries` by business role/snapshot kind and derives `position_code` / `rack_kind`.

- [ ] **Step 6: Run focused plugin tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_plugins/test_rough_sorter_plugin.py \
  -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_plugins/test_rough_sorter_plugin.py
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 迁移真实插件 manifest 到新合同"
```

Expected: commit succeeds after focused tests pass.

---

### Task 5: API Models And WorkLine Service Contract

**Files:**
- Modify: `src/app/workline/models/workline.py`
- Modify: `src/app/workline/services/workline_service.py`
- Modify: `tests/test_workline_service_plugin_validation.py`
- Modify: `tests/test_workline_routes.py`

- [ ] **Step 1: Run impact analysis**

Run:

```text
gitnexus_impact({target: "WorkLinePluginOption", direction: "upstream"})
gitnexus_impact({target: "WorkLinePluginManifestSummary", direction: "upstream"})
gitnexus_impact({target: "list_plugin_options", direction: "upstream"})
gitnexus_impact({target: "get_plugin_manifest_summary", direction: "upstream"})
```

Expected: API/generated contract impact understood.

- [ ] **Step 2: Update API schema tests first**

In `tests/test_workline_service_plugin_validation.py`, replace old summary assertions with:

```text
test_workline_service_lists_selector_only_plugin_options
test_workline_service_returns_new_plugin_manifest_summary
test_plugin_options_do_not_expose_manifest_capabilities
test_manifest_summary_exposes_devices_positions_topology_events_commands_resource_boundaries
test_configuration_checks_use_event_and_command_bindings
test_configuration_checks_validate_position_carrier_capability_against_workline_config
```

In `tests/test_workline_routes.py`, assert:

```text
GET /api/v1/workline/plugins/options returns only plugin_key,label,contract_versions,default_contract_version
GET /api/v1/workline/plugins/{plugin_key}/manifest returns new manifest summary fields
OpenAPI component WorkLinePluginOption has no old ability fields
OpenAPI component WorkLinePluginManifestSummary has new manifest fields
```

- [ ] **Step 3: Change `WorkLinePluginOption` to selector-only**

In `src/app/workline/models/workline.py`, keep only:

```text
plugin_key
label
contract_versions
default_contract_version
```

Delete fields:

```text
required_device_roles
supported_events
supported_commands
```

- [ ] **Step 4: Replace API summary schema**

In `src/app/workline/models/workline.py`, replace old summary fields with generated-friendly Pydantic schemas for:

```text
DeviceRequirement summary
Position summary
PositionCarrierCapability summary
NodeRef summary
FlowEdge summary
TopologySpec summary
EventBinding summary
CommandBinding summary
PositionArg / PositionArgSource summary
CommandResultBinding summary
ResourceBoundary summary
WorkLinePluginManifestSummary
```

Field names must match backend manifest field names.

- [ ] **Step 5: Update `list_plugin_options`**

In `src/app/workline/services/workline_service.py`, build `WorkLinePluginOption` without reading manifest capabilities:

```text
plugin_key = definition.plugin_key
label = definition.plugin_key
contract_versions = [definition.manifest.contract_version]
default_contract_version = definition.manifest.contract_version
```

- [ ] **Step 6: Update `get_plugin_manifest_summary`**

Replace old helper usage:

```text
_build_device_role_requirement_options
_normalize_manifest_role_map
_normalize_manifest_string_set
_build_single_layer_boundary_summaries
```

with new summary builders over:

```text
manifest.devices
manifest.positions
manifest.topology
manifest.events
manifest.commands
manifest.resource_boundaries
```

Keep helper functions small and local to `WorkLineService`; delete helpers that only support old fields after tests are migrated.

- [ ] **Step 7: Update configuration checks**

In `WorkLineService.build_configuration_status` and helpers:

```text
_role_requirement_checks reads manifest.devices
_event_source_checks reads manifest.events
_command_target_checks reads manifest.commands
_command_target_capability_config_checks reads command.target_device_role
_command_target_device_map reads command.target_device_role
carrier capability checks compare manifest.positions[].carrier_capability to WorklineRackPosition config
```

- [ ] **Step 8: Run focused API/service tests**

Run:

```bash
uv run pytest tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add src/app/workline/models/workline.py src/app/workline/services/workline_service.py \
  tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 更新插件 manifest API 合同"
```

Expected: commit succeeds after focused tests pass.

---

### Task 6: Runtime Consumers Migration

**Files:**
- Modify: `src/app/workline/services/inbox_batch_processor.py`
- Modify: `src/app/workline/services/operation_service.py`
- Modify: `src/app/workline/services/runtime_query_service.py`
- Modify: `src/app/workline/services/runtime_hold_release_service.py`
- Modify: `src/app/workline/services/ng_return_item_service.py`
- Modify: `src/app/workline/services/runtime_hold_query_service.py`
- Test: `tests/workline_runtime/test_inbox_batch_processor.py`
- Test: `tests/workline_runtime/test_workline_operation_service.py`
- Test: `tests/workline_runtime/test_runtime_query_service.py`
- Test: `tests/workline_runtime/test_runtime_hold_release_service.py`
- Test: `tests/workline_runtime/test_ng_return_item_service.py`
- Test: `tests/api/test_runtime_hold_api.py`

- [ ] **Step 1: Run impact analysis**

Run:

```text
gitnexus_impact({target: "_entry_event_types_for_workline", direction: "upstream"})
gitnexus_impact({target: "_generate_event_templates_from_supported_events", direction: "upstream"})
gitnexus_impact({target: "_single_layer_boundary_positions", direction: "upstream"})
gitnexus_impact({target: "_resolve_material_identity", direction: "upstream"})
gitnexus_impact({target: "_resolve_ng_reason", direction: "upstream"})
gitnexus_impact({target: "list_ng_reasons", direction: "upstream"})
```

Expected: consumer blast radius understood.

- [ ] **Step 2: Update entry event filtering tests**

In `tests/workline_runtime/test_inbox_batch_processor.py`, assert `_entry_event_types_for_workline` returns:

```text
only events where EventBinding.category == ENTRY_DEVICE
fallback _ENTRY_DEVICE_EVENT_TYPES for unknown plugin
no INTERNAL / COMMAND_RESULT / OPERATOR / SAFETY events
```

- [ ] **Step 3: Update inbox batch implementation**

In `src/app/workline/services/inbox_batch_processor.py`, replace `event_source_roles` lookup with:

```text
definition.manifest.events
filter category == EventCategory.ENTRY_DEVICE
return event names as frozenset
```

- [ ] **Step 4: Update sandbox event template tests**

In `tests/workline_runtime/test_workline_operation_service.py`, assert:

```text
templates are generated from manifest.events
device_role filter uses EventBinding.source_device_roles
ENTRY_DEVICE and other operator-visible event categories are handled by explicit category rules
old supported_events/event_source_roles absence does not break template generation
```

- [ ] **Step 5: Update operation service implementation**

In `src/app/workline/services/operation_service.py`, rename `_generate_event_templates_from_supported_events` only if all callers are updated; otherwise keep name temporarily but change internals to read `manifest.events`. Remove `_event_allows_device_role` only after no caller uses old role map shape.

- [ ] **Step 6: Update runtime query resource boundary tests**

In `tests/workline_runtime/test_runtime_query_service.py`, assert:

```text
resource boundary positions derive from manifest.resource_boundaries
SINGLE_LAYER filter still works where active snapshot requires it
FIVE_LAYER boundary is not ignored in generic resource evidence
station_code/station_role derive from Position
```

- [ ] **Step 7: Update runtime query implementation**

In `src/app/workline/services/runtime_query_service.py`, replace `_single_layer_boundary_positions` old field access with helper logic over:

```text
definition.manifest.resource_boundaries
boundary.rack_kind
boundary.position_code
manifest.positions lookup for station derivation
```

- [ ] **Step 8: Update hold release / NG return tests**

In runtime hold and NG tests, assert services call registry helpers rather than manifest methods:

```text
runtime_hold_release_service uses resolve_workline_material_identity
runtime_hold_release_service uses list_workline_ng_reasons
ng_return_item_service uses resolve_workline_material_identity
ng_return_item_service uses list_workline_ng_reasons
runtime_hold_query_service uses list_workline_ng_reasons
```

- [ ] **Step 9: Update hold release / NG return implementations**

Replace:

```text
definition.manifest.resolve_material_identity(...)
definition.manifest.list_ng_reasons()
```

with registry helper calls in:

```text
runtime_hold_release_service.py
ng_return_item_service.py
runtime_hold_query_service.py
```

- [ ] **Step 10: Run focused runtime tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_query_service.py \
  tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_ng_return_item_service.py \
  tests/api/test_runtime_hold_api.py \
  -q
```

Expected: PASS.

- [ ] **Step 11: Commit**

Run:

```bash
git add src/app/workline/services/inbox_batch_processor.py src/app/workline/services/operation_service.py \
  src/app/workline/services/runtime_query_service.py src/app/workline/services/runtime_hold_release_service.py \
  src/app/workline/services/ng_return_item_service.py src/app/workline/services/runtime_hold_query_service.py \
  tests/workline_runtime/test_inbox_batch_processor.py tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_query_service.py tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_ng_return_item_service.py tests/api/test_runtime_hold_api.py
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 迁移运行时消费方到新 manifest"
```

Expected: commit succeeds after focused tests pass.

---

### Task 7: Plugin Templates And Developer Docs

**Files:**
- Modify: `docs/templates/workline_plugin/plugin.py.tmpl`
- Modify: `docs/templates/workline_plugin/tests.py.tmpl`
- Modify: `docs/templates/workline_plugin/README.md`
- Modify: `docs/templates/workline_plugin/sandbox_happy_path.md`
- Modify: `docs/plugin_development_guide.md`
- Modify: `tests/workline_plugins/test_plugin_template_assets.py`

- [ ] **Step 1: Update template asset tests first**

In `tests/workline_plugins/test_plugin_template_assets.py`, assert template files:

```text
import DeviceRequirement, EventBinding, CommandBinding, ResourceBoundary
do not import DeviceRoleRequirement or SingleLayerRackBoundary
do not pass business_key_resolver/result_classifier/context_model/material_identity_resolver/ng_reason_catalog into manifest
declare runtime methods/properties on plugin class
declare events/commands/resource_boundaries in manifest
document PositionArg XOR/source behavior
```

- [ ] **Step 2: Update plugin template**

In `docs/templates/workline_plugin/plugin.py.tmpl`, rewrite manifest example to use new pure-data fields and move runtime capabilities to Plugin class members. Keep snippets short and omit unrelated business implementation.

- [ ] **Step 3: Update template tests**

In `docs/templates/workline_plugin/tests.py.tmpl`, update assertions to check:

```text
manifest top-level fields
events/commands are structured
plugin runtime helper methods exist
old fields absent
```

- [ ] **Step 4: Update docs**

In `docs/templates/workline_plugin/README.md`, `docs/templates/workline_plugin/sandbox_happy_path.md`, and `docs/plugin_development_guide.md`, replace old manifest-field guidance with:

```text
manifest = pure data
registry helper = runtime behavior
events/commands/resource_boundaries are structured
PositionArg static source uses position_ref
PositionArgSource does not support STATIC
```

- [ ] **Step 5: Run template tests**

Run:

```bash
uv run pytest tests/workline_plugins/test_plugin_template_assets.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add docs/templates/workline_plugin/plugin.py.tmpl docs/templates/workline_plugin/tests.py.tmpl \
  docs/templates/workline_plugin/README.md docs/templates/workline_plugin/sandbox_happy_path.md \
  docs/plugin_development_guide.md tests/workline_plugins/test_plugin_template_assets.py
npx gitnexus detect-changes --scope staged
git commit -m "docs(workline): 同步插件模板到新 manifest 合同"
```

Expected: commit succeeds after template tests pass.

---

### Task 8: Backend Cleanup Gate And Full Backend Verification

**Files:**
- Modify: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`
- Modify: any backend active source/test/template/doc file reported by the explicit scan in Step 2

- [ ] **Step 1: Run cleanup gate test**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_cleanup_gate.py -q
```

Expected: PASS.

- [ ] **Step 2: Run explicit backend old-field scan**

Run:

```bash
rg -n -P "(?<![A-Za-z0-9_])(required_device_roles|DeviceRoleRequirement|event_source_roles|command_target_roles|supported_events|supported_commands|resource_kinds|requires_single_layer_boundary|single_layer_boundaries|SingleLayerRackBoundary|BusinessKeyResolver|ResultClassifier|_looks_like_manifest|_ALLOWED_SINGLE_LAYER_|_requires_single_layer_boundaries)(?![A-Za-z0-9_])|DeviceRequirement\\.capabilities|Position\\.capabilities|PositionCarrierCapability\\.capacity|CommandBinding\\.(position_ref|result_event)|PositionArg\\.runtime_source|TopologySpec\\s*\\|\\s*None|manifest\\.(business_key_resolver|result_classifier|context_model|material_identity_resolver|ng_reason_catalog)" \
  src tests docs/templates docs/plugin_development_guide.md \
  --glob '!tests/workline_runtime/test_plugin_manifest_cleanup_gate.py' \
  --glob '!tests/mock/**'
```

Expected: no output. Any output in active source/test/template docs must be removed or converted to the new contract. Bare Plugin runtime helper names such as `get_context_model` or `context_model` are allowed outside manifest; this scan only rejects old manifest fields, old exported types, and `manifest.<runtime_field>` access.

- [ ] **Step 3: Run backend focused suite**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_reserved_runtime_events.py \
  tests/workline_runtime/test_plugin_single_layer_rack_boundary.py \
  tests/workline_runtime/test_plugin_manifest_cleanup_gate.py \
  tests/test_workline_service_plugin_validation.py \
  tests/test_workline_routes.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_query_service.py \
  tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_ng_return_item_service.py \
  tests/workline_plugins/test_plugin_template_assets.py \
  -q
```

Expected: PASS.

- [ ] **Step 4: Run backend formatting/lint**

Run:

```bash
uv run ruff format src tests docs/templates
uv run ruff check src tests
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit cleanup**

Run:

```bash
git add src tests docs/templates docs/plugin_development_guide.md
npx gitnexus detect-changes --scope staged
git commit -m "test(workline): 清理旧 manifest 合同残留"
```

Expected: commit succeeds after cleanup gate and focused suite pass.

---

### Task 9: Frontend Generated Contract

**Files:**
- Generated: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-types.ts`
- Generated: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/generated/zod-schemas.ts`
- Generated: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-metadata/WorkLinePluginManifestSummary.ts`
- Generated: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-metadata/WorkLinePluginOption.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/runtime.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/api/runtime.test.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/composables/useRuntimeSceneManifest.test.ts`

- [ ] **Step 1: Start backend OpenAPI source**

From backend repo:

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8001
```

Expected: server starts and `/openapi.json` reflects new Workline plugin schemas.

- [ ] **Step 2: Regenerate frontend contract**

From frontend repo:

```bash
cd /Users/kaizhou/SynologyDrive/works/wes_frontend
pnpm generate:types
pnpm generate:zod
pnpm contract:verify
```

Expected: all commands exit 0.

- [ ] **Step 3: Update runtime aliases**

In `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/runtime.ts`, remove local runtime manifest compatibility fields:

```text
required_device_roles
event_source_roles
command_target_roles
supported_events
supported_commands
single_layer_boundaries
```

Keep aliases tied to generated `components['schemas']` where available.

- [ ] **Step 4: Add contract tests**

In frontend tests, assert:

```text
WorkLinePluginOption generated schema has only selector fields
WorkLinePluginManifestSummary generated schema has devices, positions, topology, events, commands, resource_boundaries
useRuntimeSceneManifest cache key still uses plugin_key + contract_version
manifest route still accepts plugin_key path parameter
```

- [ ] **Step 5: Run frontend contract tests**

From frontend repo:

```bash
pnpm test -- tests/unit/api/runtime.test.ts tests/unit/composables/useRuntimeSceneManifest.test.ts
pnpm type:check
```

Expected: PASS and type check exits 0.

- [ ] **Step 6: Commit frontend generated contract**

From frontend repo:

```bash
git add src/api/generated src/types/generated src/types/runtime.ts tests/unit/api/runtime.test.ts tests/unit/composables/useRuntimeSceneManifest.test.ts .contract-sync-record.json
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 更新插件 manifest 前端生成合同"
```

Expected: commit succeeds after contract tests pass.

---

### Task 10: Frontend Config Page And Runtime Scene Migration

**Files:**
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/views/admin/worklines/config/WorkLineConfigPage.vue`
- Create: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/utils/runtime-scene.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/utils/runtime-scene.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/utils/runtime-scene.regression-1.test.ts`

- [ ] **Step 1: Add config page tests first**

Create `tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts` with Vue Test Utils/Vitest style matching existing tests. Required tests:

```text
test options selector renders plugin_key/label without required_device_roles
test selected plugin manifest detail is loaded by plugin_key
test role coverage reads manifest.devices rather than WorkLinePluginOption
test events display reads manifest.events
test commands display reads manifest.commands
test page still works when options contain only selector fields
```

- [ ] **Step 2: Update config page data flow**

In `WorkLineConfigPage.vue`:

```text
pluginOptions remains WorkLinePluginOption[]
add selectedManifest ref/computed fed by worklineApiMethods.manifest({ plugin_key })
rename selectedPluginManifest if needed so it no longer points to pluginOptions
roleCoverageList reads selected manifest devices
event tags read selected manifest events[].event
command tags read selected manifest commands[].command
watch workline.plugin_key and contract_version to reload manifest detail
```

Do not duplicate devices/events/commands in `WorkLinePluginOption`.

- [ ] **Step 3: Add runtime scene tests first**

In `tests/unit/utils/runtime-scene.test.ts` and regression file, replace old `single_layer_boundaries` fixtures with new manifest fixtures:

```text
positions include station_code and carrier_capability
resource_boundaries include position_code/rack_kind/WMS/snapshot/lease
topology includes typed flow edges
runtime evidence overlays by position_code
manifestLoadFailed still falls back to generic evidence
contract_version mismatch still fails closed through caller
```

- [ ] **Step 4: Update runtime scene implementation**

In `src/utils/runtime-scene.ts`:

```text
hasManifestBoundaries reads manifest.resource_boundaries
resolveBoundaries maps ResourceBoundary + Position into RuntimeSceneBoundary
fallback boundary summaries remain for no-manifest state
semantic fallback messages mention manifest/resource boundaries, not single_layer_boundaries
no code references WorkLineSingleLayerRackBoundarySummary
```

- [ ] **Step 5: Run frontend focused tests**

From frontend repo:

```bash
pnpm test -- \
  tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts \
  tests/unit/utils/runtime-scene.test.ts \
  tests/unit/utils/runtime-scene.regression-1.test.ts
pnpm type:check
```

Expected: PASS and type check exits 0.

- [ ] **Step 6: Commit frontend consumers**

From frontend repo:

```bash
git add src/views/admin/worklines/config/WorkLineConfigPage.vue src/utils/runtime-scene.ts \
  tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts \
  tests/unit/utils/runtime-scene.test.ts tests/unit/utils/runtime-scene.regression-1.test.ts
npx gitnexus detect-changes --scope staged
git commit -m "feat(workline): 适配配置页和运行态视图新 manifest"
```

Expected: commit succeeds after focused tests pass.

---

### Task 11: Cross-Repo Cleanup And Final Verification

**Files:**
- Backend and frontend active files changed by previous tasks

- [ ] **Step 1: Run backend old-field scan**

From backend repo:

```bash
rg -n -P "(?<![A-Za-z0-9_])(required_device_roles|DeviceRoleRequirement|event_source_roles|command_target_roles|supported_events|supported_commands|resource_kinds|requires_single_layer_boundary|single_layer_boundaries|SingleLayerRackBoundary|BusinessKeyResolver|ResultClassifier|_looks_like_manifest|_ALLOWED_SINGLE_LAYER_|_requires_single_layer_boundaries)(?![A-Za-z0-9_])|DeviceRequirement\\.capabilities|Position\\.capabilities|PositionCarrierCapability\\.capacity|CommandBinding\\.(position_ref|result_event)|PositionArg\\.runtime_source|TopologySpec\\s*\\|\\s*None|manifest\\.(business_key_resolver|result_classifier|context_model|material_identity_resolver|ng_reason_catalog)" \
  src tests docs/templates docs/plugin_development_guide.md \
  --glob '!tests/workline_runtime/test_plugin_manifest_cleanup_gate.py' \
  --glob '!tests/mock/**'
```

Expected: no output.

- [ ] **Step 2: Run frontend old-field scan**

From frontend repo:

```bash
cd /Users/kaizhou/SynologyDrive/works/wes_frontend
rg -n -P "(?<![A-Za-z0-9_])(required_device_roles|event_source_roles|command_target_roles|supported_events|supported_commands|single_layer_boundaries)(?![A-Za-z0-9_])|WorkLineSingleLayerRackBoundarySummary" \
  src tests \
  --glob '!tests/unit/api/runtime.test.ts'
```

Expected: no output.

- [ ] **Step 3: Run backend verification**

From backend repo:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_reserved_runtime_events.py \
  tests/workline_runtime/test_plugin_single_layer_rack_boundary.py \
  tests/workline_runtime/test_plugin_manifest_cleanup_gate.py \
  tests/test_workline_service_plugin_validation.py \
  tests/test_workline_routes.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_query_service.py \
  tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_ng_return_item_service.py \
  tests/workline_plugins/test_plugin_template_assets.py \
  -q
uv run ruff check src tests
```

Expected: all commands exit 0.

- [ ] **Step 4: Run frontend verification**

From frontend repo:

```bash
cd /Users/kaizhou/SynologyDrive/works/wes_frontend
pnpm contract:verify
pnpm test -- \
  tests/unit/api/runtime.test.ts \
  tests/unit/composables/useRuntimeSceneManifest.test.ts \
  tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts \
  tests/unit/utils/runtime-scene.test.ts \
  tests/unit/utils/runtime-scene.regression-1.test.ts
pnpm type:check
```

Expected: all commands exit 0.

- [ ] **Step 5: Run GitNexus detect changes before final commit or PR**

Run in agent environment:

```text
gitnexus_detect_changes()
```

Expected: detected symbol changes match this plan: manifest/topology, registry helpers, plugin manifests, workline service/runtime consumers, template docs/tests, frontend generated contract and consumers.

- [ ] **Step 6: Final commit if there are remaining unstaged cleanup changes**

From each repo with remaining intended changes:

```bash
git status --short
git add <intended files only>
npx gitnexus detect-changes --scope staged
git commit -m "chore(workline): 完成 manifest 重构清理门禁"
```

Expected: only intended files are committed; unrelated pre-existing changes remain untouched.

## Self-Review

- Spec coverage: Tasks 1-2 cover manifest pure data, typed topology, event/command/result/position/resource contracts; Task 3 covers registry helper and singleton lifecycle; Task 4 covers real plugins; Tasks 5-6 cover API and runtime consumers; Tasks 7-8 cover docs/templates and cleanup gate; Tasks 9-10 cover frontend generated contract/config/runtime scene; Task 11 covers final verification.
- Red-flag scan: no banned placeholder expressions; every test step names concrete files, test cases, assertions, and commands.
- Type consistency: all task names use the spec's final names: `DeviceRequirement`, `PositionCarrierCapability`, `NodeRef`, `FlowEdge`, `EventBinding`, `CommandBinding`, `PositionArgSource`, `CommandResultBinding`, `ResourceBoundary`, `WorkLinePluginOption`, `WorkLinePluginManifestSummary`.
