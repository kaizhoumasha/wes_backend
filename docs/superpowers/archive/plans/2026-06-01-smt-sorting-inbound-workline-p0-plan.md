# SMT Sorting Inbound WorkLine P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 SMT 分拣入库 P0 本地状态闭环：源格取盘出账、扫码分格、目标端放盘、本地 NG、Session 完成前闭环检查。

**Architecture:** P0 分成 Foundation PR 和 Plugin PR。Foundation 只落共享分格策略、Decimal/Numeric 深度、资源事实投影和结构化 NG 冲突；Plugin 在 foundation 稳定后接入 `SortingInboundContext` 和分拣入库业务 handler。WMS/CTU/NG 生产级外部对账不属于 P0，只保留证据和扩展点。

**Tech Stack:** Python 3.13, FastAPI service/repository layering, SQLModel/SQLAlchemy AsyncSession, Alembic, pytest, Ruff, WorkLine plugin manifest/topology, resource projection services.

---

## 实施验证状态

日期：2026-06-02。

结论：SMT 分拣入库 P0 后端本地状态闭环已在 `wes_backend` 当前 `develop` 验证通过。完整 CTU/WMS/NG 对账仍按 SPEC 非目标和 TODO 延后；平台级前端 STOPPED/START 合同已在前端 worktree 落地但尚未合并，跨计划端到端沙箱验收仍不能关闭。

已合并实现提交：`f87e48f v0.4.4.0 feat: SMT 分拣入库基础流`。

已验证通过：

- P0 相关聚焦 suite 已包含在后端 383 passed 命令中，覆盖 shared allocation policy、粗分机 delegation、Decimal/Numeric 深度、`MATERIAL_UNMOUNTED`、`NG_MATERIAL_CONFLICT`、`SortingInboundContext`、插件 manifest/roles、源端取盘、扫码分格、目标放盘、本地 NG、completion guard 和 P0 integration smoke。
- 相关回归：`uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_runtime_intent_effects.py -q`，54 passed。
- 质量门禁：相关 runtime/callback/resource/plugin/test 路径 `uv run ruff check ...` 通过，`uv run ruff format --check ...` 显示 264 files already formatted。
- 迁移 smoke：`./scripts/migrate.sh upgrade` 通过。

流程偏差 / 风险：

- 原计划要求 Foundation PR 和 Plugin PR 可独立 review、独立回滚；实际落地为单个提交 `f87e48f`，功能可验收，但回滚边界不如计划理想。
- 未复核历史 RED/TDD 失败态输出和实施前 `gitnexus_impact` 记录；本次只验证当前 HEAD 行为和质量门禁。
- GitNexus 从计划前提交到当前 HEAD 的 compare 为 critical 风险，属于跨 runtime/resource/plugin 大改后的预期风险；仍建议后续做独立 code review 和联调 QA。
- 前端 STOPPED/START 合同依赖分支 `../worktrees/wes_frontend/feature-workline-stopped-start-contract`，当前 HEAD `d9bb38a`，需合并后再执行跨计划沙箱 smoke。

## Scope Check

本计划只覆盖 [SMT 分拣入库复合 WorkLine 设计](../specs/2026-06-01-smt-sorting-inbound-workline-design.md) 的 P0 本地状态闭环。完整 CTU/WMS/NG 对账、WMS typed port、目标箱回写生产闭环、多扫码平台、多目标机械臂、跨 WorkLine reservation 和 resource 域全量 float 清理均不在本计划内。

本计划依赖平台级 WorkLine START 准入计划提供运行时入口语义：非 `READY` 不接收生产事件，设备命令下发前做 realtime status guard。依赖关系见 [2026-06-01-workline-fast-fail-and-smt-sorting-plan-dependencies.md](2026-06-01-workline-fast-fail-and-smt-sorting-plan-dependencies.md)。

## Implementation Guardrails

- 修改任何函数、类或方法前，先按项目要求运行 GitNexus impact；如果 HIGH/CRITICAL，先向用户确认。
- API 层不得直接访问 DB 或 repository；本计划主要改 service、plugin、resource projection 和 tests。
- Alembic migration 必须使用 `uv run alembic revision -m "<message>"` 生成 revision ID 后再编辑。
- 分拣业务阶段不得扩展通用 `SessionStatus`；细粒度状态保存在插件 context、资源投影或 evidence。
- handler 不得直接深层修改 `session.context_json["sorting"]`；必须通过 typed `SortingInboundContext` 读写，并确保 ORM 感知更新。
- 源格和目标格事实必须走 resource projection 链路，不允许插件直接改表绕过审计。
- 规划文档只写职责、接口名、状态流、测试场景和验收标准；实现细节在代码 diff 和测试中体现。

## File Structure

### Foundation PR

- Modify `src/app/resource/models/resource.py`: 将 `BinCellOccupancy.used_depth_mm`、`capacity_depth_mm`、`remaining_depth_mm` 切到 Decimal/Numeric 合同。
- Create Alembic migration under `migrations/versions/`: 修改 `resource_bin_cell_occupancies` 三个核心深度字段类型。
- Create `src/app/resource/services/smt_bin_cell_allocation_policy.py`: 共享纯策略，输入 active snapshot、物料身份、Decimal 厚度，输出目标格选择或拒绝原因。
- Modify `src/app/resource/services/smt_rack_bin_scheduling_service.py`: 粗分机保留调度包装，把兼容格/空格/容量判断委托给共享策略。
- Modify `src/app/resource/services/projection_service.py`: 支持 `MATERIAL_UNMOUNTED` 资源事实，按 `cell_stack_position` 最大的 active 料盘出账。
- Modify `src/app/resource/repositories/resource_repository.py`: 如现有 repository 不足，补充按源格读取 active 顶部料盘和 active snapshot 的有界查询。
- Modify `src/app/workline/services/ng_return_item_service.py`: 将不同来源 active `material_identity_key` 冲突升级为结构化 `NG_MATERIAL_CONFLICT` 专用异常或结果。
- Modify `src/app/workline/services/__init__.py` and `src/app/resource/services/__init__.py`: 导出新增 service / policy。

### Plugin PR

- Create `src/workline_plugins/smt_sorting_inbound/`: 新分拣入库插件目录，包含 plugin manifest、handler 入口和 P0 flow 编排。
- Create `src/workline_plugins/smt_sorting_inbound/context.py`: `SortingInboundContext` typed contract，负责 `sorting.context_schema_version=1`、`current_material`、`pending_target_placement`、active target bin 和 station 字段。
- Create `src/workline_plugins/smt_sorting_inbound/constants.py`: 设备角色、业务阶段、事件/命令名、NG reason code。
- Create `src/workline_plugins/smt_sorting_inbound/flow_service.py`: 源端取盘、扫码分格、目标放盘、本地 NG、Session 完成检查的插件业务服务。
- Modify plugin registry files matching current repo pattern: 注册 `SMT_SORTING_INBOUND`，但不复活已清理的旧 SMT 插件。
- Modify existing WorkLine operation/sandbox code if needed: 让插件 manifest 的 `event_source_roles` / `command_target_roles` 进入沙箱模板和拓扑校验。

### Tests

- Create `tests/resource/test_smt_bin_cell_allocation_policy.py`
- Modify `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`
- Modify `tests/resource/test_resource_projection_service.py`
- Modify `tests/resource/test_resource_runtime_base.py`
- Create `tests/workline_runtime/test_smt_sorting_inbound_context.py`
- Create `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Create `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`
- Modify `tests/workline_runtime/test_ng_return_item_service.py`
- Modify `tests/test_workline_service_plugin_validation.py`

## Task 0: Baseline, Impact, And Dependency Check

**Files:**
- Read only: `docs/superpowers/specs/2026-06-01-smt-sorting-inbound-workline-design.md`
- Read only: `docs/superpowers/plans/2026-06-01-workline-fast-fail-start-admission-plan.md`
- Read only: files listed in File Structure

- [ ] **Step 0.1: Confirm dependency state**

Run:

```bash
git status --short
rg -n "STOPPED|WORKLINE_START_REQUESTED|start_admission_status|device_status_timeout_seconds" src/app src/workline_runtime tests
```

Expected: understand whether platform START plan is already merged. If it is not merged, implement only Foundation PR or coordinate branches so Plugin PR waits for the platform runtime contract.

- [ ] **Step 0.2: Run GitNexus impact before code edits**

Run impact checks before modifying these symbols:

```text
SmtRackBinSchedulingService
ResourceProjectionService
BinCellOccupancy
NgReturnItemService
WorkLineService
PluginManifest
```

Expected: record direct callers and risk level in implementation notes. HIGH/CRITICAL requires user confirmation before edits.

- [ ] **Step 0.3: Run baseline focused tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/resource/test_resource_projection_service.py tests/workline_runtime/test_ng_return_item_service.py tests/test_workline_service_plugin_validation.py -q
```

Expected: capture baseline. If unrelated failures exist, record them before starting TDD.

## Task 1: Shared Decimal Allocation Policy

**Files:**
- Create: `src/app/resource/services/smt_bin_cell_allocation_policy.py`
- Modify: `src/app/resource/services/__init__.py`
- Test: `tests/resource/test_smt_bin_cell_allocation_policy.py`

- [ ] **Step 1.1: Write failing pure policy tests**

Cover:

- Compatible cell with same material/DC/LC and enough Decimal depth wins before empty cell.
- Empty cell with enough Decimal depth is selected when no compatible cell exists.
- No empty cell and no compatible capacity returns structured no-capacity reason.
- Missing, invalid, zero/negative reel thickness is rejected.
- Missing, invalid, negative capacity/used depth is rejected.
- `used_depth > total_depth` returns projection-inconsistent reason.
- Output includes target bin, target cell, source snapshot version if present, and capacity evidence.
- Policy does not accept repository/session/db dependencies.

- [ ] **Step 1.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/resource/test_smt_bin_cell_allocation_policy.py -q
```

Expected: FAIL because the policy file does not exist.

- [ ] **Step 1.3: Implement minimal pure policy**

Implementation boundaries:

- Use `Decimal` internally for all depth comparisons.
- Preserve Decimal source strings in evidence.
- Return a typed result object or small dataclass-like structure that is easy to assert in tests.
- Do not import SQLAlchemy, repositories, WorkLine session models, or command dispatch services.

- [ ] **Step 1.4: Run tests**

Run:

```bash
uv run pytest tests/resource/test_smt_bin_cell_allocation_policy.py -q
```

Expected: PASS.

- [ ] **Step 1.5: Commit Foundation task**

Run:

```bash
git add src/app/resource/services/smt_bin_cell_allocation_policy.py src/app/resource/services/__init__.py tests/resource/test_smt_bin_cell_allocation_policy.py
git commit -m "feat(resource): add smt bin cell allocation policy"
```

Expected: commit contains only shared policy and tests.

## Task 2: Decimal/Numeric Depth Contract

**Files:**
- Modify: `src/app/resource/models/resource.py`
- Modify: `src/app/resource/services/projection_service.py`
- Modify: `src/app/resource/services/active_rack_snapshot_service.py`
- Modify: `src/app/resource/services/smt_rack_bin_scheduling_service.py`
- Create: `migrations/versions/<generated>_numeric_bin_cell_depth.py`
- Test: `tests/resource/test_resource_runtime_base.py`
- Test: `tests/resource/test_resource_projection_service.py`
- Test: `tests/resource/test_smt_active_rack_snapshot_service.py`

- [ ] **Step 2.1: Write failing model and projection tests**

Cover:

- `BinCellOccupancy` three depth columns are Numeric/Decimal-compatible.
- Projection stores Decimal-safe values and does not introduce float rounding in evidence.
- Active snapshot exposes depth values without losing original precision.
- Existing mount projection behavior remains compatible with current tests.

- [ ] **Step 2.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/resource/test_resource_runtime_base.py tests/resource/test_resource_projection_service.py tests/resource/test_smt_active_rack_snapshot_service.py -q
```

Expected: failures show current float contract.

- [ ] **Step 2.3: Generate migration**

Run:

```bash
uv run alembic revision -m "numeric bin cell depth"
```

Expected: Alembic creates one new migration file with generated revision ID.

- [ ] **Step 2.4: Implement Decimal/Numeric depth handling**

Implementation boundaries:

- Limit schema migration to `BinCellOccupancy.used_depth_mm`, `capacity_depth_mm`, `remaining_depth_mm`.
- Do not migrate unrelated resource float fields in P0.
- Convert calculations at resource boundary carefully; avoid `.timestamp()` or timezone changes because this task has no time semantics.
- Keep API response compatibility if existing response schemas expect numeric JSON.

- [ ] **Step 2.5: Run targeted tests**

Run:

```bash
uv run pytest tests/resource/test_resource_runtime_base.py tests/resource/test_resource_projection_service.py tests/resource/test_smt_active_rack_snapshot_service.py -q
```

Expected: PASS.

- [ ] **Step 2.6: Commit Foundation task**

Run:

```bash
git add src/app/resource/models/resource.py src/app/resource/services/projection_service.py src/app/resource/services/active_rack_snapshot_service.py src/app/resource/services/smt_rack_bin_scheduling_service.py migrations/versions/*.py tests/resource/test_resource_runtime_base.py tests/resource/test_resource_projection_service.py tests/resource/test_smt_active_rack_snapshot_service.py
git commit -m "feat(resource): store bin cell depth as numeric"
```

Expected: commit contains Decimal/Numeric migration and related tests.

## Task 3: Rough Sorter Uses Shared Policy

**Files:**
- Modify: `src/app/resource/services/smt_rack_bin_scheduling_service.py`
- Test: `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`
- Test: `tests/resource/test_smt_bin_cell_allocation_policy.py`

- [ ] **Step 3.1: Write failing delegation tests**

Cover:

- Rough sorter calls the shared allocation policy for compatible/empty/no-capacity decisions.
- Rough sorter still owns rack operation, move-out target role, and current rack snapshot wrapping.
- Existing rough sorter rejection messages remain stable where API/tests depend on them.

- [ ] **Step 3.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/resource/test_smt_bin_cell_allocation_policy.py -q
```

Expected: failure proves current service still contains parallel capacity logic.

- [ ] **Step 3.3: Refactor rough sorter boundary**

Implementation boundaries:

- Do not move rack operation orchestration into shared policy.
- Do not give shared policy DB/repository access.
- Keep rough sorter-specific command payload and move-out behavior in `SmtRackBinSchedulingService`.

- [ ] **Step 3.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/resource/test_smt_bin_cell_allocation_policy.py -q
```

Expected: PASS.

- [ ] **Step 3.5: Commit Foundation task**

Run:

```bash
git add src/app/resource/services/smt_rack_bin_scheduling_service.py tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/resource/test_smt_bin_cell_allocation_policy.py
git commit -m "refactor(resource): share smt bin allocation policy"
```

Expected: commit contains rough sorter delegation only.

## Task 4: MATERIAL_UNMOUNTED Resource Projection

**Files:**
- Modify: `src/app/resource/models/resource.py`
- Modify: `src/app/resource/repositories/resource_repository.py`
- Modify: `src/app/resource/services/projection_service.py`
- Test: `tests/resource/test_resource_projection_service.py`

- [ ] **Step 4.1: Write failing projection tests**

Cover:

- `MATERIAL_UNMOUNTED` closes the active mount with max `cell_stack_position`.
- Source occupancy `reel_count`, used depth, remaining depth, and occupancy status update once.
- Idempotent replay of same source Session/source command/source cell/source mount returns same result and does not double decrement.
- Missing active top reel, identity mismatch, source version mismatch, or inconsistent occupancy returns reconciliation/hold result instead of guessing.
- Event/evidence includes source session, source command, source bin/cell, mount ID or identity, source version and trace.

- [ ] **Step 4.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/resource/test_resource_projection_service.py -q
```

Expected: failure shows `MATERIAL_UNMOUNTED` is not implemented or incomplete.

- [ ] **Step 4.3: Implement unmount projection**

Implementation boundaries:

- Follow existing `MATERIAL_MOUNTED` projection style.
- Keep source out账 in resource projection service, not plugin code.
- Do not reopen source active mount later during NG or target placement branches.

- [ ] **Step 4.4: Run targeted tests**

Run:

```bash
uv run pytest tests/resource/test_resource_projection_service.py -q
```

Expected: PASS.

- [ ] **Step 4.5: Commit Foundation task**

Run:

```bash
git add src/app/resource/models/resource.py src/app/resource/repositories/resource_repository.py src/app/resource/services/projection_service.py tests/resource/test_resource_projection_service.py
git commit -m "feat(resource): project material unmounted facts"
```

Expected: commit contains resource projection changes only.

## Task 5: Structured NG_MATERIAL_CONFLICT

**Files:**
- Modify: `src/app/workline/services/ng_return_item_service.py`
- Modify if needed: `src/app/workline/models/runtime_hold.py`
- Test: `tests/workline_runtime/test_ng_return_item_service.py`
- Test: `tests/api/test_runtime_hold_api.py`

- [ ] **Step 5.1: Write failing conflict tests**

Cover:

- Same Session/same command duplicate NG returns existing `NgReturnItem`.
- Different source with same active `material_identity_key` raises or returns structured `NG_MATERIAL_CONFLICT`.
- Conflict evidence includes existing item, new material identity key, source Session, source command, scan event, expected identity, actual identity if available.
- Conflict can be converted into RuntimeHold / `RECONCILING` / `MANUAL_HOLD` by caller without parsing `ValueError` text.

- [ ] **Step 5.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_ng_return_item_service.py tests/api/test_runtime_hold_api.py -q
```

Expected: failures show current behavior relies on generic `ValueError`.

- [ ] **Step 5.3: Implement structured conflict**

Implementation boundaries:

- Preserve existing active uniqueness semantics.
- Do not silently aggregate different-source NG items.
- Keep normal same-source idempotency behavior.

- [ ] **Step 5.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_ng_return_item_service.py tests/api/test_runtime_hold_api.py -q
```

Expected: PASS.

- [ ] **Step 5.5: Commit Foundation task**

Run:

```bash
git add src/app/workline/services/ng_return_item_service.py src/app/workline/models/runtime_hold.py tests/workline_runtime/test_ng_return_item_service.py tests/api/test_runtime_hold_api.py
git commit -m "feat(workline): structure ng material conflict"
```

Expected: commit contains NG conflict structure only.

## Foundation PR Acceptance

- [x] Shared policy is pure, Decimal-based, and covered by unit tests.
- [x] Rough sorter uses shared policy without losing existing rack operation behavior.
- [x] `BinCellOccupancy` core depth fields are Numeric/Decimal.
- [x] `MATERIAL_UNMOUNTED` handles LIFO source out账 and idempotency.
- [x] `NG_MATERIAL_CONFLICT` is structured and test-covered.
- [x] Focused tests pass:

```bash
uv run pytest tests/resource/test_smt_bin_cell_allocation_policy.py tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/resource/test_resource_projection_service.py tests/workline_runtime/test_ng_return_item_service.py -q
uv run ruff format .
uv run ruff check .
```

## Task 6: SortingInboundContext Contract

**Files:**
- Create: `src/workline_plugins/smt_sorting_inbound/context.py`
- Create: `src/workline_plugins/smt_sorting_inbound/constants.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_context.py`

- [ ] **Step 6.1: Write failing context tests**

Cover:

- Missing or incompatible `sorting.context_schema_version` refuses automatic sorting.
- `current_material` can be opened, updated, and closed through typed methods.
- `pending_target_placement` can be written before target command and cleared after success.
- Decimal depth original values are preserved as strings in context/evidence.
- Nested JSON updates are persisted by replacing the session context object or using the runtime update entrypoint.

- [ ] **Step 6.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_context.py -q
```

Expected: FAIL because context contract does not exist.

- [ ] **Step 6.3: Implement context contract**

Implementation boundaries:

- Keep this file focused on parsing, validation, and context writeback.
- Do not put device command dispatch, WMS calls, or allocation policy logic in context.
- Use explicit methods for current material and pending placement transitions.

- [ ] **Step 6.4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_context.py -q
```

Expected: PASS.

- [ ] **Step 6.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound/context.py src/workline_plugins/smt_sorting_inbound/constants.py tests/workline_runtime/test_smt_sorting_inbound_context.py
git commit -m "feat(workline): add sorting inbound context contract"
```

Expected: commit contains typed context only.

## Task 7: Plugin Manifest, Roles, And Registration

**Files:**
- Create: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Create/modify plugin registry files matching existing pattern.
- Modify: `tests/test_workline_service_plugin_validation.py`
- Create: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`

- [ ] **Step 7.1: Write failing manifest tests**

Cover:

- Plugin key `SMT_SORTING_INBOUND` is registered.
- Required roles include `SORTING_SOURCE_ARM`, `SORTING_TARGET_ARM`, `SORTING_NG_ARM`, `SORTING_SCAN_PLATFORM`, `SORTING_NG_STATION`, `SORTING_WORKSTATION`.
- `command_target_roles` maps source pick, target place, NG place to the correct roles.
- `event_source_roles` maps source result, target result, NG result, and `WORKING_BIN_SCAN` to the correct roles.
- No plugin logic writes hard-coded `ARM01/ARM02` device codes.

- [ ] **Step 7.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py -q
```

Expected: FAIL because plugin is not registered.

- [ ] **Step 7.3: Implement manifest and registration**

Implementation boundaries:

- Follow rough sorter plugin manifest style.
- Keep platform control events like `WORKLINE_START_REQUESTED` out of plugin `supported_events`.
- Keep business event names explicit; if hardware names are not final, use stable P0 contract names documented in tests.

- [ ] **Step 7.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py -q
```

Expected: PASS.

- [ ] **Step 7.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound src/workline_plugin_registry.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py
git commit -m "feat(workline): register smt sorting inbound plugin"
```

Expected: commit contains plugin registration and manifest tests.

## Task 8: Source Pick And MATERIAL_UNMOUNTED Flow

**Files:**
- Create: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`

- [ ] **Step 8.1: Write failing source pick tests**

Cover:

- Source pick success requires scan platform empty.
- Source pick success emits/processes `MATERIAL_UNMOUNTED`.
- `MATERIAL_UNMOUNTED`, `sorting.current_material`, and scan platform occupancy are committed atomically or equivalently.
- Source pick uses max `cell_stack_position` as top reel.
- After source pick, later NG/target branches never decrement source cell again.

- [ ] **Step 8.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: FAIL because source pick flow is missing.

- [ ] **Step 8.3: Implement source pick flow**

Implementation boundaries:

- Use WorkLine topology role resolution; do not hard-code device codes.
- Use resource projection service for source out账.
- Keep source selection snapshot reads bounded; do not query each source cell in a loop.

- [ ] **Step 8.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: PASS for source pick cases.

- [ ] **Step 8.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py
git commit -m "feat(workline): unmount source reel for sorting inbound"
```

Expected: commit contains source pick flow only.

## Task 9: Scan Allocation And Pending Target Placement

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`

- [ ] **Step 9.1: Write failing scan allocation tests**

Cover:

- Scan OK uses shared `SmtBinCellAllocationPolicy`.
- Compatible target cell writes `pending_target_placement`.
- Empty target cell writes `pending_target_placement`.
- No capacity moves scan platform to `WAITING_TARGET_BIN_SWITCH` and does not start a new source pick.
- Thickness mismatch with same identity uses actual thickness and records evidence.
- Missing/invalid/negative thickness refuses automatic placement.
- Target cell projection inconsistency freezes target cell/bin or enters reconciliation path.

- [ ] **Step 9.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: FAIL because scan allocation is missing.

- [ ] **Step 9.3: Implement scan allocation flow**

Implementation boundaries:

- Allocation happens after scan, not before source pick.
- `pending_target_placement` is not a cross-WorkLine reservation.
- Do not dispatch target arm before pending placement is persisted.

- [ ] **Step 9.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: PASS for scan allocation cases.

- [ ] **Step 9.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py
git commit -m "feat(workline): allocate target cell for sorting inbound"
```

Expected: commit contains scan allocation flow only.

## Task 10: Target Placement And MATERIAL_MOUNTED Flow

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`

- [ ] **Step 10.1: Write failing target placement tests**

Cover:

- Target arm command requires existing `pending_target_placement`.
- Target command payload includes target bin/cell from pending placement.
- Target success writes/reuses `MATERIAL_MOUNTED`.
- Target success clears `pending_target_placement`, closes `current_material`, and releases scan platform.
- Target failure with known location enters manual suspend and preserves failure evidence.
- Target failure with unknown location enters reconciliation.

- [ ] **Step 10.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: FAIL because target placement is missing.

- [ ] **Step 10.3: Implement target placement flow**

Implementation boundaries:

- Use resource projection service for `MATERIAL_MOUNTED`.
- Do not modify target occupancy directly from plugin code.
- Preserve pending placement evidence when physical location is uncertain.

- [ ] **Step 10.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: PASS for target placement cases.

- [ ] **Step 10.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py
git commit -m "feat(workline): mount target reel for sorting inbound"
```

Expected: commit contains target placement flow only.

## Task 11: Local NG Flow

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Modify if needed: `src/app/workline/services/ng_return_item_service.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/workline_runtime/test_ng_return_item_service.py`
- Test: `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`

- [ ] **Step 11.1: Write failing NG tests**

Cover:

- Source snapshot mismatch sends reel to local NG, not target bin.
- NG success updates current material actual identity, NG status, NG location, and evidence.
- NG success creates or idempotently returns `NgReturnItem`.
- NG transaction is atomic: if material state, `NgReturnItem`, or evidence write fails, scan platform is not released and Session does not advance.
- Different-source active `material_identity_key` conflict enters `NG_MATERIAL_CONFLICT` hold/reconciliation/manual hold.
- NG command failure with known location enters manual suspend; unknown location enters reconciliation.

- [ ] **Step 11.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_ng_return_item_service.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: FAIL because local NG flow is missing/incomplete.

- [ ] **Step 11.3: Implement local NG flow**

Implementation boundaries:

- Source cell was already decremented at source pick; NG must not decrement it again.
- NG does not update target bin projection.
- NG does not put Session into external HTTP wait for WMS real-time confirmation.
- Conflict blocks Session completion until resolved.

- [ ] **Step 11.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_ng_return_item_service.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: PASS for local NG cases.

- [ ] **Step 11.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound src/app/workline/services/ng_return_item_service.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_ng_return_item_service.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py
git commit -m "feat(workline): close sorting inbound ng locally"
```

Expected: commit contains NG flow only.

## Task 12: Session Completion And N+1 Guard

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`

- [ ] **Step 12.1: Write failing completion tests**

Cover:

- Session cannot complete while `current_material` is open.
- Session cannot complete while `pending_target_placement` exists.
- Session can complete when all in-process material is closed to target, local NG, runtime hold, or reconciliation.
- NG follow-up reconciliation not yet sent to WMS does not force Session into `EXTERNAL_HTTP`.
- Source/target candidate selection uses one active snapshot and in-memory indexes; test with query counter or fake repository call counts.

- [ ] **Step 12.2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: FAIL because completion guard and query bound are missing.

- [ ] **Step 12.3: Implement completion and query guards**

Implementation boundaries:

- Keep business stages in plugin context; do not add generic `SessionStatus` values.
- Use existing generic states only as mapped runtime categories.
- Ensure source/target snapshot reads are explicit and bounded.

- [ ] **Step 12.4: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: PASS for completion and query-bound cases.

- [ ] **Step 12.5: Commit Plugin task**

Run:

```bash
git add src/workline_plugins/smt_sorting_inbound tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py
git commit -m "feat(workline): guard sorting inbound session completion"
```

Expected: commit contains completion guard only.

## Final Verification

- [ ] **Step 13.1: Run P0 focused suite**

Run:

```bash
uv run pytest tests/resource/test_smt_bin_cell_allocation_policy.py tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/resource/test_resource_projection_service.py tests/workline_runtime/test_ng_return_item_service.py tests/workline_runtime/test_smt_sorting_inbound_context.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q
```

Expected: PASS.

- [ ] **Step 13.2: Run related WorkLine regression**

Run:

```bash
uv run pytest tests/test_workline_service_plugin_validation.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_runtime/test_runtime_intent_effects.py -q
```

Expected: PASS.

- [ ] **Step 13.3: Run quality checks**

Run:

```bash
uv run ruff format .
uv run ruff check .
```

Expected: PASS.

- [ ] **Step 13.4: Run migration smoke**

Run:

```bash
./scripts/migrate.sh upgrade
```

Expected: migration applies cleanly in the active dev/test database environment. If DB is unavailable, record exact blocker.

- [ ] **Step 13.5: Run GitNexus changed-scope check before final commit**

Run:

```text
gitnexus_detect_changes(scope="all", repo="wes_backend")
```

Expected: affected flows match resource projection, SMT rack/bin allocation, NG return, WorkLine plugin registration, and SMT Sorting P0 plugin only.

## Final Acceptance Checklist

- [ ] Foundation PR and Plugin PR can be reviewed and rolled back independently.
  - 2026-06-02 verification: implementation landed as single commit `f87e48f`; 功能已验收，但计划的 PR/rollback 切分未满足。
- [x] Shared allocation policy is pure and used by rough sorter and sorting inbound.
- [x] Core bin cell depth calculations use Decimal/Numeric.
- [x] Source pick writes `MATERIAL_UNMOUNTED` and opens `current_material`.
- [x] Scan allocation writes `pending_target_placement` before target command.
- [x] Target success writes `MATERIAL_MOUNTED` and closes current material.
- [x] Local NG success writes `NgReturnItem` and closes current material without WMS realtime wait.
- [x] `NG_MATERIAL_CONFLICT` blocks completion through structured hold/reconciliation/manual hold.
- [x] Session completion rejects dangling `current_material` and `pending_target_placement`.
- [x] Candidate selection avoids source/target N+1 query patterns.
- [x] Plugin declares role mappings and does not hard-code physical device codes.

## Self-Review

Spec coverage:

- P0 Foundation scope is covered by Tasks 1-5.
- P0 Plugin scope is covered by Tasks 6-12.
- Full CTU/WMS/NG external reconciliation is intentionally deferred and documented as out of scope.
- Risks around duplicate algorithms, float precision, context mutation, source out账, pending placement, and NG conflict are mapped to explicit tasks and tests.

Placeholder scan:

- No section uses unresolved placeholders or vague “handle later” wording.
- Deferred items are named as out-of-scope external reconciliation work, not hidden implementation tasks.

Type consistency:

- Runtime business context uses `sorting.context_schema_version=1`, `sorting.current_material`, and `sorting.pending_target_placement` consistently.
- Resource facts use `MATERIAL_UNMOUNTED` and `MATERIAL_MOUNTED`.
- NG conflict uses `NG_MATERIAL_CONFLICT`.
- Plugin role names match the SPEC: `SORTING_SOURCE_ARM`, `SORTING_TARGET_ARM`, `SORTING_NG_ARM`, `SORTING_SCAN_PLATFORM`, `SORTING_NG_STATION`, `SORTING_WORKSTATION`.
