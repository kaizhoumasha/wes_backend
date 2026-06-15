# WorkLine Manifest RackPosition Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 WorkLine plugin manifest 中泛化的 `Position` 合同重命名为明确的 `RackPosition` 合同，防止插件作者把扫码点、输送线内部点或机器人中转位误建模为 WES 资源拓扑节点。

**Architecture:** 这是一次未发布合同的破坏性清理，不保留 `Position = RackPosition` 兼容别名。重命名只发生在 manifest 静态合同和前端生成消费层；WMS/RCS payload、资源投影、运行时 outbox/session 中已有的 `position_code` 继续保持不变。backend 先收紧 dataclass/Pydantic/OpenAPI 合同和清理门禁，再由 frontend 重新生成类型并更新 runtime scene/config page 消费。

**Tech Stack:** Python 3.13, dataclasses, Pydantic, FastAPI OpenAPI, pytest, ruff, GitNexus, uv, Vue 3, TypeScript, pnpm, Vitest。

---

## Investigation Summary

Root cause hypothesis: manifest 把“WES 管理的货架停靠位/库存事实锚点”命名为泛化 `Position`，导致公开合同、OpenAPI schema 和前端生成类型仍暗示它可以表示任意物理点位。

Evidence:

- `src/workline_runtime/plugin_manifest.py` 当前 `Position` 注释已经说明“不代表泛化物理位置”，但类名、字段名和 enum 值仍是 `Position` / `positions` / `NodeRefKind.POSITION`。
- `src/app/workline/models/workline.py` 会把该命名暴露到 OpenAPI，frontend generated schemas 当前生成 `PositionSchema`、`position_ref` 和 `fallback_position_ref`。
- 旧计划 `docs/superpowers/plans/2026-06-12-rack-position-contract-optimization.md` 明确选择“不做 API 字段级破坏性重命名”。本计划反转该选择，因为只靠注释不能防止后续误用。
- 既有学习记录说明当前 WES 未发布，可接受 WorkLine 破坏性合同清理，不需要保留旧 facade 或 alias。

Non-goals:

- 不重命名 `WorklineRackPosition` 数据库模型。它已经表示现场工作线货架位实际配置。
- 不重命名 WMS/RCS payload 和运行时资源投影中的 `position_code`、`source_position_code`、`target_position_code`、`work_position_code`。
- 不改变资源编排、station lease、outbox dispatch 或 rack operation 的运行逻辑。
- 不修改 `lease_scope="POSITION"`，这是资源预占范围枚举，不是 manifest 节点类型。

## Naming Contract

| Current manifest name | New manifest name | Scope |
| --- | --- | --- |
| `Position` | `RackPosition` | `src/workline_runtime/plugin_manifest.py`, API schema, generated frontend schema |
| `PositionCarrierCapability` | `RackPositionCarrierCapability` | manifest rack position 承载能力 |
| `WorklinePluginManifest.positions` | `WorklinePluginManifest.rack_positions` | manifest 顶层字段 |
| `WorkLinePluginManifestSummary.positions` | `WorkLinePluginManifestSummary.rack_positions` | API response field |
| `NodeRefKind.POSITION` / `"POSITION"` | `NodeRefKind.RACK_POSITION` / `"RACK_POSITION"` | topology typed node ref |
| `PositionArg` | `RackPositionArg` | command binding 中的货架位参数声明 |
| `PositionArgRole` | `RackPositionArgRole` | command rack position 参数业务角色 |
| `PositionArgSource` | `RackPositionArgSource` | command rack position 参数动态来源 |
| `PositionArgSourceKind` | `RackPositionArgSourceKind` | command rack position 参数来源类型 |
| `CommandBinding.position_args` | `CommandBinding.rack_position_args` | command binding 字段 |
| `position_ref` | `rack_position_ref` | 静态固定 rack position 引用 |
| `fallback_position_ref` | `fallback_rack_position_ref` | 动态来源失败时的兜底 rack position 引用 |
| `ResourceBoundary.position_code` | `ResourceBoundary.rack_position_code` | manifest resource boundary 引用 rack position |
| `POSITION_CARRIER_CAPABILITY` | `RACK_POSITION_CARRIER_CAPABILITY` | WorkLine 配置检查 code |

## File Structure

Backend files:

- Modify: `src/workline_runtime/plugin_manifest.py`
  - Manifest dataclass 合同主真源，执行 `RackPosition*` 命名、引用完整性和 error message 更新。
- Modify: `src/workline_runtime/__init__.py`
  - 导出新 manifest 类型，删除旧 `Position*` 导出。
- Modify: `src/app/workline/models/workline.py`
  - API Pydantic summary 模型和 OpenAPI schema 名称/字段描述。
- Modify: `src/app/workline/services/workline_service.py`
  - manifest summary builder、配置检查、context 字段和 check code。
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
  - 粗分机真实 manifest 使用 `rack_positions`、`RackPosition*`、`RACK_POSITION` 和 `rack_position_args`。
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
  - SMT 分拣入库真实 manifest 使用新命名。
- Modify: `docs/plugin_development_guide.md`
  - 插件开发指南新合同名和示例约定。
- Modify: `docs/templates/workline_plugin/README.md`
  - 插件模板说明新合同名。
- Modify: `docs/templates/workline_plugin/plugin.py.tmpl`
  - 插件模板 manifest 示例新合同名。
- Modify: `docs/templates/workline_plugin/tests.py.tmpl`
  - 插件模板测试示例新合同名。
- Modify: `docs/templates/workline_plugin/sandbox_happy_path.md`
  - sandbox checklist 新合同名。
- Modify: `docs/superpowers/plans/2026-06-12-rack-position-contract-optimization.md`
  - 在文件头追加 superseded note，说明本计划反转旧的“不重命名”选择。
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
  - manifest 合同、真实插件 golden sample、命令 rack position 参数校验。
- Test: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`
  - 旧 manifest 命名清零门禁。
- Test: `tests/test_workline_service_plugin_validation.py`
  - API summary 和配置检查字段名/check code 更新。
- Test: `tests/test_workline_routes.py`
  - manifest route response shape 更新。
- Test: `tests/workline_plugins/test_plugin_template_assets.py`
  - 插件模板资产新命名和旧命名清零。

Frontend files under `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor`:

- Generate: `src/api/generated/openapi-types.ts`
  - 由 backend OpenAPI 生成，schema 改为 `RackPosition*` 和 `rack_positions`。
- Generate: `src/types/generated/zod-schemas.ts`
  - 由 backend OpenAPI 生成，zod schema 改为 `RackPosition*`。
- Generate: `src/api/generated/openapi-metadata/*.ts`
  - metadata 生成文件中移除 `PositionArg*` 旧 schema，新增 `RackPositionArg*`。
- Modify: `src/utils/runtime-scene.ts`
  - `manifest.rack_positions`、`ResourceBoundary.rack_position_code` 和 scene boundary 映射。
- Modify: `src/views/admin/worklines/config/WorkLineConfigPage.vue`
  - 配置页 manifest positions 展示、变量名和 `RACK_POSITION_CARRIER_CAPABILITY` 检查 code。
- Test: `tests/unit/api/runtime.test.ts`
  - generated metadata/zod shape 断言。
- Test: `tests/unit/utils/runtime-scene.test.ts`
  - runtime scene manifest fixture 和 orphan boundary 用例。
- Test: `tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts`
  - 配置页 manifest rack position 展示。
- Test: `tests/unit/views/admin/worklines/config/WorkLineConfigPage.position-capability-regression.test.ts`
  - 配置检查 code 和承载能力提示。
- Test: `tests/unit/views/runtime/runtimeRouteSync.test.ts`
  - route sync manifest fixture 顶层字段。
- Test: `tests/unit/scripts/contract-endpoint-noise.test.ts`
  - manifest endpoint allowed fields。

## Task 1: 安全门禁和影响面复核

**Files:**
- Read: `src/workline_runtime/plugin_manifest.py`
- Read: `src/app/workline/models/workline.py`
- Read: `src/app/workline/services/workline_service.py`
- Read: `src/workline_plugins/rough_sorter/plugin.py`
- Read: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Read: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/utils/runtime-scene.ts`
- Read: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/views/admin/worklines/config/WorkLineConfigPage.vue`

- [ ] **Step 1: 确认 worktree 干净**

Run:

```bash
git status --short
```

Expected: no output, or only unrelated user-owned files that are explicitly excluded from this task.

- [ ] **Step 2: 刷新 GitNexus 索引**

Run:

```bash
npx gitnexus analyze
```

Expected: analyzer completes, or reports only generated/large-file skips. If it exits non-zero without a usable index, capture the output and continue with `mcp__gitnexus.cypher` plus local source search as fallback.

- [ ] **Step 3: 运行 backend symbol impact analysis**

Call GitNexus impact for these symbols before editing code symbols:

```text
WorklinePluginManifest
Position
PositionCarrierCapability
PositionArg
PositionArgSource
NodeRefKind
CommandBinding
ResourceBoundary
WorkLinePluginManifestSummary
WorkLineService._build_position_summary
WorkLineService._build_position_arg_summary
WorkLineService._position_carrier_capability_checks
RoughSorterPlugin
SmtSortingInboundPlugin
```

Expected: no HIGH or CRITICAL risk. If any target returns HIGH, CRITICAL, or UNKNOWN for a symbol that will be edited, stop and report the direct callers, affected processes and unknowns before changing files.

- [ ] **Step 4: Confirm rename tool availability**

Call `tool_search` for GitNexus rename support.

Expected: use GitNexus rename for class/enum symbol renames if available. If no rename tool is exposed, use targeted symbol-level edits only; do not run broad repository string replacement commands.

- [ ] **Step 5: Record blast radius**

Run:

```bash
rg -l "PositionCarrierCapability|\\bPositionArg\\b|PositionArgSource|NodeRefKind\\.POSITION|position_ref|fallback_position_ref|\\bpositions\\b" \
  src/workline_runtime src/workline_plugins src/app/workline/models/workline.py src/app/workline/services/workline_service.py \
  tests/workline_runtime tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py docs/plugin_development_guide.md docs/templates/workline_plugin
```

Expected: output is limited to manifest contract, plugin declarations, API summary, tests and docs/templates.

## Task 2: Backend 先写失败测试和 cleanup gate

**Files:**
- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Modify: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`
- Modify: `tests/test_workline_service_plugin_validation.py`
- Modify: `tests/test_workline_routes.py`
- Modify: `tests/workline_plugins/test_plugin_template_assets.py`

- [ ] **Step 1: 修改 manifest 合同 shape 测试**

Update `test_manifest_contract_exports_public_data_types` or the closest current public surface test to assert:

- `RackPosition`, `RackPositionCarrierCapability`, `RackPositionArg`, `RackPositionArgSource`, `RackPositionArgRole`, `RackPositionArgSourceKind` are exported.
- `Position`, `PositionCarrierCapability`, `PositionArg`, `PositionArgSource`, `PositionArgRole`, `PositionArgSourceKind` are not exported from `src.workline_runtime.plugin_manifest`.
- `WorklinePluginManifest` accepts `rack_positions` and does not accept `positions`.

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py -q
```

Expected before production changes: failures mention missing `RackPosition*` or unexpected old `Position*`.

- [ ] **Step 2: 修改 topology ref 测试**

Update topology tests to use `NodeRefKind.RACK_POSITION`. Keep the existing rule that `MATERIAL_FLOW` edges must connect rack-position nodes only.

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py::test_manifest_rejects_unknown_topology_refs -q
```

Expected before production changes: failure mentions missing `RACK_POSITION` or old `POSITION` enum value.

- [ ] **Step 3: 修改 command rack position arg 测试**

Rename command arg tests so they assert:

- `RackPositionArg.rack_position_ref` and `RackPositionArg.source` are mutually exclusive.
- required `RackPositionArg` must declare `rack_position_ref` or `source`.
- `RackPositionArgSource.fallback_rack_position_ref` must reference `manifest.rack_positions`.
- `RackPositionArgSourceKind` still does not include `STATIC`.

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py -k "rack_position_arg or source_kind" -q
```

Expected before production changes: failures mention old `PositionArg` names or missing `rack_position_ref`.

- [ ] **Step 4: 修改 cleanup gate**

Update `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py` so active sources fail on old manifest names in manifest contexts:

- old public classes and enums: `Position`, `PositionCarrierCapability`, `PositionArg`, `PositionArgSource`, `PositionArgRole`, `PositionArgSourceKind`
- old manifest field: `positions`
- old command field: `position_args`
- old refs: `position_ref`, `fallback_position_ref`
- old topology enum member: `NodeRefKind.POSITION`

Keep the gate scoped to manifest contract paths and templates so unrelated runtime payload keys such as `position_code` remain allowed.

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_cleanup_gate.py -q
```

Expected before production changes: cleanup gate reports old manifest contract names in active source/template files.

- [ ] **Step 5: 修改 API route/service tests**

Update expected manifest summary fields from `positions` to `rack_positions`, and expected configuration check code from `POSITION_CARRIER_CAPABILITY` to `RACK_POSITION_CARRIER_CAPABILITY`.

Run:

```bash
uv run pytest tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py -q
```

Expected before production changes: failures mention missing `rack_positions` or check code mismatch.

- [ ] **Step 6: 修改 plugin template asset tests**

Update template asset tests to expect `RackPosition*`, `rack_positions`, `rack_position_args`, `rack_position_ref` and `fallback_rack_position_ref`.

Run:

```bash
uv run pytest tests/workline_plugins/test_plugin_template_assets.py -q
```

Expected before template changes: failures mention old template snippets.

## Task 3: 重命名 backend manifest dataclass 合同

**Files:**
- Modify: `src/workline_runtime/plugin_manifest.py`
- Modify: `src/workline_runtime/__init__.py`
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Test: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`

- [ ] **Step 1: Rename manifest data types**

In `src/workline_runtime/plugin_manifest.py`, rename the manifest-only dataclasses/enums according to the Naming Contract table:

- `Position` family becomes `RackPosition` family.
- `WorklinePluginManifest.positions` becomes `rack_positions`.
- `CommandBinding.position_args` becomes `rack_position_args`.
- `ResourceBoundary.position_code` becomes `rack_position_code`.
- `NodeRefKind.POSITION` becomes `NodeRefKind.RACK_POSITION`.

Do not alter `PositionArgRole` enum values `SOURCE` and `TARGET`; only rename the enum type to `RackPositionArgRole`.

- [ ] **Step 2: Update validation messages and helper names**

Update validation helpers and error messages so they refer to:

- `manifest.rack_positions`
- `RackPosition.carrier_capability`
- `RackPositionArg.rack_position_ref`
- `RackPositionArgSource.fallback_rack_position_ref`
- `Topology NodeRef RACK_POSITION`
- `ResourceBoundary.rack_position_code`

Keep validation behavior identical to current behavior.

- [ ] **Step 3: Update runtime package exports**

In `src/workline_runtime/__init__.py`, export the new names and remove old `Position*` exports. Keep unrelated runtime exports unchanged.

- [ ] **Step 4: Run manifest tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_plugin_manifest_cleanup_gate.py -q
```

Expected: selected tests pass after plugin files are updated in Task 4; at this step, remaining failures should only come from plugin declarations and templates still using old names.

## Task 4: 更新真实插件 manifest 声明

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Test: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`

- [ ] **Step 1: Update rough sorter imports and builders**

In `src/workline_plugins/rough_sorter/plugin.py`:

- import `RackPosition`, `RackPositionCarrierCapability`, `RackPositionArg`, `RackPositionArgRole`, `RackPositionArgSource`, `RackPositionArgSourceKind`
- rename local helper return annotations from `Position*` to `RackPosition*`
- construct `rack_positions=(...)`
- construct `rack_position_args=(...)`
- use `NodeRefKind.RACK_POSITION`
- use `fallback_rack_position_ref`

Keep command payload names such as `"source_position_code"` and `"target_position_code"` unchanged.

- [ ] **Step 2: Update SMT inbound imports and builders**

Apply the same manifest-only rename in `src/workline_plugins/smt_sorting_inbound/plugin.py`.

Keep constants such as `POSITION_SOURCE_STATION_A` unchanged in this task; they are plugin-local stable codes and not public type names.

- [ ] **Step 3: Update real plugin golden assertions**

In `tests/workline_runtime/test_plugin_manifest_and_topology.py`, update real plugin assertions:

- `manifest.rack_positions` replaces `manifest.positions`
- command args read from `command.rack_position_args`
- `NodeRefKind.RACK_POSITION` replaces `NodeRefKind.POSITION`
- boundary lookup uses `boundary.rack_position_code`

- [ ] **Step 4: Run real plugin tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py -q
```

Expected: all selected tests pass or only API summary/template tests remain failing.

## Task 5: 更新 backend API summary 和配置检查

**Files:**
- Modify: `src/app/workline/models/workline.py`
- Modify: `src/app/workline/services/workline_service.py`
- Test: `tests/test_workline_service_plugin_validation.py`
- Test: `tests/test_workline_routes.py`

- [ ] **Step 1: Rename API schema classes and fields**

In `src/app/workline/models/workline.py`:

- rename `PositionCarrierCapability` to `RackPositionCarrierCapability`
- rename `Position` to `RackPosition`
- rename `PositionArg*` schema classes to `RackPositionArg*`
- rename `CommandBinding.position_args` to `rack_position_args`
- rename `ResourceBoundary.position_code` to `rack_position_code`
- rename `WorkLinePluginManifestSummary.positions` to `rack_positions`

Update descriptions to say “货架停靠位” and “rack position” consistently.

- [ ] **Step 2: Update service summary builders**

In `src/app/workline/services/workline_service.py`:

- `_build_position_carrier_capability_summary` becomes `_build_rack_position_carrier_capability_summary`
- `_build_position_summary` becomes `_build_rack_position_summary`
- `_build_position_arg_source_summary` becomes `_build_rack_position_arg_source_summary`
- `_build_position_arg_summary` becomes `_build_rack_position_arg_summary`
- summary construction reads `manifest.rack_positions`
- command summary construction reads `command.rack_position_args`
- resource boundary summary emits `rack_position_code`

Do not change service/repository layering.

- [ ] **Step 3: Update configuration check context**

Rename the check code to `RACK_POSITION_CARRIER_CAPABILITY`. In the context payload, prefer `rack_position_code` and `rack_position_role`; keep `position_code` out of this manifest-derived check to prevent mixed terminology.

- [ ] **Step 4: Run service and route tests**

Run:

```bash
uv run pytest tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py -q
```

Expected: all selected tests pass or only docs/template cleanup failures remain.

## Task 6: 更新 backend docs、templates 和旧计划状态

**Files:**
- Modify: `docs/plugin_development_guide.md`
- Modify: `docs/templates/workline_plugin/README.md`
- Modify: `docs/templates/workline_plugin/plugin.py.tmpl`
- Modify: `docs/templates/workline_plugin/tests.py.tmpl`
- Modify: `docs/templates/workline_plugin/sandbox_happy_path.md`
- Modify: `docs/superpowers/plans/2026-06-12-rack-position-contract-optimization.md`
- Test: `tests/workline_plugins/test_plugin_template_assets.py`
- Test: `tests/workline_runtime/test_plugin_manifest_cleanup_gate.py`

- [ ] **Step 1: Update plugin development guide**

Replace manifest contract references so the guide says:

- manifest top-level field is `rack_positions`
- rack positions are WES-managed rack docking positions / inventory-fact anchors
- `RackPositionArg.rack_position_ref` is the only static fixed rack position reference path
- hardware internal points remain in command payload or plugin business logic

- [ ] **Step 2: Update plugin template files**

Update template imports, manifest declaration and template tests from old `Position*` names to `RackPosition*` names.

Keep template examples focused on rack docking positions; do not introduce scanner, conveyor or robot internal point examples as rack positions.

- [ ] **Step 3: Mark old rack-position semantic plan as superseded**

At the top of `docs/superpowers/plans/2026-06-12-rack-position-contract-optimization.md`, add a short note that its “keep Position naming” decision is superseded by `docs/superpowers/plans/2026-06-14-workline-manifest-rack-position-rename.md`.

Do not delete the old plan; it is useful decision history.

- [ ] **Step 4: Run template and cleanup tests**

Run:

```bash
uv run pytest tests/workline_plugins/test_plugin_template_assets.py tests/workline_runtime/test_plugin_manifest_cleanup_gate.py -q
```

Expected: all selected tests pass.

## Task 7: Regenerate and update frontend contract consumers

**Files:**
- Generate: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/api/generated/openapi-types.ts`
- Generate: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/types/generated/zod-schemas.ts`
- Generate: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/api/generated/openapi-metadata/*.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/utils/runtime-scene.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/src/views/admin/worklines/config/WorkLineConfigPage.vue`
- Test: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/tests/unit/api/runtime.test.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/tests/unit/utils/runtime-scene.test.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/tests/unit/views/admin/worklines/config/WorkLineConfigPage.position-capability-regression.test.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/tests/unit/views/runtime/runtimeRouteSync.test.ts`
- Test: `/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor/tests/unit/scripts/contract-endpoint-noise.test.ts`

- [ ] **Step 1: Start backend OpenAPI server**

From backend repo:

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8001
```

Expected: server exposes `http://localhost:8001/api/openapi.json`.

- [ ] **Step 2: Regenerate frontend OpenAPI types**

From frontend worktree:

```bash
cd /Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor
pnpm generate:types
pnpm generate:zod
```

Expected: generated files contain `RackPosition`, `RackPositionArg`, `rack_positions`, `rack_position_args`, `rack_position_ref`, `fallback_rack_position_ref`, and no manifest `PositionArg` schema.

- [ ] **Step 3: Update runtime scene consumer**

In `src/utils/runtime-scene.ts`:

- replace `WorkLinePluginManifestSummary['positions']` with `WorkLinePluginManifestSummary['rack_positions']`
- build `rackPositionsByCode` from `manifest.rack_positions`
- read `boundary.rack_position_code` when mapping manifest resource boundaries
- keep runtime evidence fields such as `item.position_code` unchanged

- [ ] **Step 4: Update config page consumer**

In `src/views/admin/worklines/config/WorkLineConfigPage.vue`:

- rename `selectedPluginPositions` to `selectedPluginRackPositions`
- read `selectedPluginManifest.value?.rack_positions`
- update labels and check-code handling to `RACK_POSITION_CARRIER_CAPABILITY`
- keep workline actual configuration model names such as `WorklineRackPosition` unchanged

- [ ] **Step 5: Update frontend tests and fixtures**

Update frontend tests so manifest fixtures use:

- `rack_positions`
- `rack_position_args`
- `rack_position_ref`
- `fallback_rack_position_ref`
- `rack_position_code`
- node ref kind value `RACK_POSITION`

Keep runtime projection/resource evidence fixtures using `position_code`.

- [ ] **Step 6: Run frontend targeted tests**

Run:

```bash
cd /Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor
pnpm test tests/unit/api/runtime.test.ts \
  tests/unit/utils/runtime-scene.test.ts \
  tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts \
  tests/unit/views/admin/worklines/config/WorkLineConfigPage.position-capability-regression.test.ts \
  tests/unit/views/runtime/runtimeRouteSync.test.ts \
  tests/unit/scripts/contract-endpoint-noise.test.ts
```

Expected: all selected Vitest suites pass.

## Task 8: Full verification and cleanup scan

**Files:**
- Verify backend and frontend files changed in Tasks 2-7.

- [ ] **Step 1: Run backend focused regression**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_plugin_manifest_cleanup_gate.py \
  tests/test_workline_service_plugin_validation.py \
  tests/test_workline_routes.py \
  tests/workline_plugins/test_plugin_template_assets.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run backend formatting and lint**

Run:

```bash
uv run ruff format src/workline_runtime/plugin_manifest.py src/workline_runtime/__init__.py src/app/workline/models/workline.py src/app/workline/services/workline_service.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_plugin_manifest_cleanup_gate.py tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py tests/workline_plugins/test_plugin_template_assets.py
uv run ruff check src/workline_runtime/plugin_manifest.py src/workline_runtime/__init__.py src/app/workline/models/workline.py src/app/workline/services/workline_service.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_plugin_manifest_cleanup_gate.py tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py tests/workline_plugins/test_plugin_template_assets.py
```

Expected: ruff reports no errors after formatting.

- [ ] **Step 3: Run backend manifest cleanup search**

Run:

```bash
rg -n -P "\\b(?<!Rack)PositionCarrierCapability\\b|\\b(?<!Rack)PositionArg\\b|\\b(?<!Rack)PositionArgSource\\b|\\b(?<!Rack)PositionArgRole\\b|\\b(?<!Rack)PositionArgSourceKind\\b|NodeRefKind\\.POSITION|position_ref|fallback_position_ref|\\bpositions\\b" \
  src/workline_runtime src/workline_plugins src/app/workline/models/workline.py src/app/workline/services/workline_service.py docs/plugin_development_guide.md docs/templates/workline_plugin tests/workline_runtime tests/workline_plugins tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py
```

Expected: no output except archived/superseded plan references if the command is intentionally extended to include `docs/superpowers`.

- [ ] **Step 4: Run frontend type and contract checks**

Run:

```bash
cd /Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor
pnpm type:check
pnpm contract:test
```

Expected: type check and contract test pass.

- [ ] **Step 5: Run frontend cleanup search**

Run:

```bash
cd /Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor
rg -n -P "\\bPositionSchema\\b|\\bPositionArg\\b|\\bPositionArgSource\\b|position_ref|fallback_position_ref|\\bpositions\\b" \
  src/api/generated src/types/generated src/utils/runtime-scene.ts src/views/admin/worklines/config tests/unit/api tests/unit/utils tests/unit/views/admin/worklines/config tests/unit/views/runtime tests/unit/scripts
```

Expected: no manifest-contract old names. If unrelated UI layout text contains “position”, keep it only outside manifest contract files.

- [ ] **Step 6: Run GitNexus detect changes before commit**

Call:

```text
gitnexus_detect_changes(scope: "all")
```

Expected: changed symbols and affected flows are limited to manifest contract, workline plugin summary, real plugin declarations, docs/templates and related tests.

## Task 9: Commit backend and frontend changes

**Files:**
- Backend: all modified backend repo files from Tasks 2-6 plus this plan file.
- Frontend: all modified frontend worktree files from Task 7.

- [ ] **Step 1: Review backend diff**

Run:

```bash
git diff -- src/workline_runtime/plugin_manifest.py src/workline_runtime/__init__.py src/app/workline/models/workline.py src/app/workline/services/workline_service.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py docs/plugin_development_guide.md docs/templates/workline_plugin docs/superpowers/plans tests/workline_runtime tests/workline_plugins tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py
```

Expected: diff contains only manifest RackPosition rename, docs/templates alignment, and tests.

- [ ] **Step 2: Commit backend**

Run:

```bash
git add src/workline_runtime/plugin_manifest.py src/workline_runtime/__init__.py src/app/workline/models/workline.py src/app/workline/services/workline_service.py src/workline_plugins/rough_sorter/plugin.py src/workline_plugins/smt_sorting_inbound/plugin.py docs/plugin_development_guide.md docs/templates/workline_plugin docs/superpowers/plans/2026-06-12-rack-position-contract-optimization.md docs/superpowers/plans/2026-06-14-workline-manifest-rack-position-rename.md tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_plugin_manifest_cleanup_gate.py tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py tests/workline_plugins/test_plugin_template_assets.py
git commit -m "refactor(workline): 将 manifest 货架位合同重命名为 RackPosition"
```

Expected: backend commit succeeds.

- [ ] **Step 3: Review frontend diff**

Run:

```bash
cd /Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor
git diff -- src/api/generated src/types/generated src/utils/runtime-scene.ts src/views/admin/worklines/config tests/unit/api tests/unit/utils tests/unit/views/admin/worklines/config tests/unit/views/runtime tests/unit/scripts
```

Expected: diff contains generated schema rename, runtime scene/config page consumption updates, and tests.

- [ ] **Step 4: Commit frontend**

Run:

```bash
cd /Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend/feature-workline-plugin-manifest-refactor
git add src/api/generated src/types/generated src/utils/runtime-scene.ts src/views/admin/worklines/config/WorkLineConfigPage.vue tests/unit/api/runtime.test.ts tests/unit/utils/runtime-scene.test.ts tests/unit/views/admin/worklines/config/WorkLineConfigPage.test.ts tests/unit/views/admin/worklines/config/WorkLineConfigPage.position-capability-regression.test.ts tests/unit/views/runtime/runtimeRouteSync.test.ts tests/unit/scripts/contract-endpoint-noise.test.ts
git commit -m "refactor(workline): 对齐 manifest RackPosition 合同"
```

Expected: frontend commit succeeds.

## Self-Review

Spec coverage:

- Covers backend manifest data contract rename.
- Covers API summary/OpenAPI generated contract rename.
- Covers real plugins and templates.
- Covers frontend generated types and runtime/config consumers.
- Covers cleanup gates so old manifest names do not leak back into active code.
- Keeps runtime/WMS/RCS `position_code` payloads out of scope.

Placeholder scan:

- No deferred implementation markers are used.
- Each task names exact files, commands and expected results.
- Code-level changes are described as contract mappings instead of full implementations, following this repository's planning-document readability rule.

Type consistency:

- `RackPosition*` names are used consistently for manifest-only static contract types.
- `rack_position_code` is used only for manifest `ResourceBoundary` references.
- Runtime projection/evidence fields retain `position_code`.

Acceptance criteria:

- Backend OpenAPI exposes `rack_positions` and `RackPosition*` schemas for plugin manifest summary.
- `src.workline_runtime.plugin_manifest.__all__` exports only new manifest rack-position names.
- Real plugin manifests use `rack_positions`, `rack_position_args`, `rack_position_ref`, `fallback_rack_position_ref`, `NodeRefKind.RACK_POSITION`.
- Frontend type generation and runtime scene consume `rack_positions` and `rack_position_code`.
- Cleanup gates fail on old active manifest contract names.
- Focused backend pytest, frontend Vitest, frontend type check and contract test pass.
