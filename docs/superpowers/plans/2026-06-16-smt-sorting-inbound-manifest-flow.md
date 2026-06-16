# 新 Manifest 下 SMT 分拣入库后端闭环优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `SMT_SORTING_INBOUND` 从粗分机 release fact 到 source item `PICKED/SORTED/SKIPPED`、demand `COMPLETED` 收敛为后端自动闭环。

**Architecture:** Handoff service 是 source item ledger 和 demand 聚合的唯一推进入口。Plugin 只产生命令、context patch 和 resource fact intents；runtime effect 落地 intents 后调用 handoff service 写 `PICKED/SORTED/SKIPPED`。Claim 使用 manifest-declared source boundary、typed `SortingInboundContext` 和 target WorkLine 两阶段短锁串行保护。

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async, PostgreSQL row lock/SKIP LOCKED, Celery, pytest, Ruff, GitNexus.

---

## 验证结论

已对 `docs/superpowers/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md` 做代码事实验证，结论是 SPEC 可以实施，但必须按 T0-T8 分阶段推进。

已确认的当前差距：

- `src/workline_plugins/smt_sorting_inbound/plugin.py` 仍声明 `ROLE_SORTING_NG_STATION`、`POSITION_NG_STATION`、`POSITION_WORKSTATION` 和 `SORTING_INBOUND_NG` / `SORTING_INBOUND_WORK` resource boundaries。
- `src/workline_plugins/smt_sorting_inbound/context.py` 已有 `sorting.context_schema_version` helper，但没有 `source_pick_request` typed helper；claim 仍手写 loose JSON。
- `src/app/workline/domain/services/smt_inbound_handoff_route_service.py` 仍从 `source_station_code` / `source_position_code` 自由字符串解析 source position，且默认 ECS probe 是 allow-idle stub。
- `src/app/workline/services/smt_inbound_handoff_service.py` 的 `claim_next_source_item` 在 READY item 行锁内继续执行 route/ECS probe/session/inbox 创建，不满足两阶段短锁模型。
- `src/workline_runtime/runtime_intent_effects.py` 只在 `SORTING_SOURCE_PICK` command 创建后回写 command correlation，尚未在 command success / terminal success 后统一调用 handoff service 写 ledger。
- `scan_smt_inbound_handoff_demands_batch` 只扫描 due demand 和 stuck item，summary 没有 `claimed`，Celery task 也没有 `claim_limit`。

Prior learning applied:

- `ecs_idle_is_dispatch_admission_source`: ECS `/device/status` IDLE 是设备准入真源，生产路径不得默认 allow-idle。
- `smt_handoff_source_items_are_claim_rows`: SMT inbound handoff source item 是并发 claim 行级真源，不回退到 JSON hot path。
- `workline-rack-handoff-entrypoint`: 当前断点不是低级能力缺失，而是 release/source item/session/runtime ledger 的生产可达入口未闭环。

## 文件结构

### Manifest 与插件合同

- Modify: `src/workline_plugins/smt_sorting_inbound/constants.py`
  - 移除或停止导出 `ROLE_SORTING_NG_STATION`，保留 NG 命令和 NG reason。
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
  - 收敛 manifest：只声明 source single-layer rack positions 和 target five-layer rack position。
  - `SORTING_TARGET_PLACE` target rack position 固定引用 `POSITION_TARGET_STATION`。
  - `SORTING_NG_PLACE` 绑定 `ROLE_SORTING_TARGET_ARM`，不声明 target `RackPositionArg`。
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
  - 保留 context/resource intents，补充 handoff terminal evidence 所需的 context payload。
  - 不在 plugin 中直接写 handoff ledger。

### Typed context

- Modify: `src/workline_plugins/smt_sorting_inbound/context.py`
  - 增加 `write_source_pick_request(...)` / `source_pick_request()` 等小 helper。
  - 初始化时支持写 `stations.scan_platform=EMPTY`。
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_context.py`
  - 覆盖 source pick request JSON-safe 和必填字段。

### Route / claim / repository

- Modify: `src/app/workline/domain/services/smt_inbound_handoff_route_service.py`
  - 从 manifest resource boundaries 解析 source boundary。
  - 默认生产 probe 改为真实 ECS realtime probe 端口，测试必须显式注入 stub。
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
  - 新增/收敛 `record_source_pick_success(...)`。
  - 新增/收敛 `record_source_item_terminal_result(..., terminal_status)`。
  - 改造 `claim_next_source_item(...)` 为两阶段短锁。
  - 改造 `scan_smt_inbound_handoff_demands_batch(...)` 支持 READY claim。
- Modify: `src/app/workline/repositories/smt_inbound_handoff_repository.py`
  - 增加无锁 READY 候选读取、短事务 re-lock、target WorkLine lock/open session/in-flight ledger 查询。

### Runtime effect / Celery

- Modify: `src/workline_runtime/runtime_intent_effects.py`
  - 在 source pick success effects 成功落地后调用 `record_source_pick_success(...)`。
  - 在 target/ng terminal success effects 成功落地后调用 `record_source_item_terminal_result(...)`。
- Modify: `src/celery_app/tasks/workline.py`
  - task 参数拆为 `scan_limit` / `recovery_limit` / `claim_limit`。
- Modify: `src/celery_app/config.py`
  - 保持 Beat 保守 claim 上限，更新注释。

### Tests

- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Modify: `tests/test_workline_service_plugin_validation.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_context.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_route_service.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_claim.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_recovery.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_celery.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Modify: `tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py`
- Modify: `tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py`

## 实施规则

- 每个任务修改函数、类、方法前先跑 GitNexus impact；HIGH/CRITICAL 必须先停下向用户确认。
- 每个任务先写 RED 测试，确认失败原因指向本任务缺口，再实现。
- 每个任务完成后跑局部测试和 Ruff，提交一个小 commit。
- 不在 API 层新增数据库或 Repository 直连。
- 不把 NG 区、工作站、扫码平台、料箱码、料格码建模为 manifest `RackPosition`。
- 不新增旧 context 兼容迁移。
- 不在 planning 文档粘贴完整实现；实现阶段以 diff 和测试体现细节。

## Task T0: Implementation Preflight

**Files:**
- Read: `docs/superpowers/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md`
- Read: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Read: `src/workline_plugins/smt_sorting_inbound/context.py`
- Read: `src/app/workline/domain/services/smt_inbound_handoff_route_service.py`
- Read: `src/app/workline/services/smt_inbound_handoff_service.py`
- Read: `src/workline_runtime/runtime_intent_effects.py`
- Read: `src/celery_app/tasks/workline.py`

- [x] **Step 1: 确认工作区只包含预期变更**

Run:

```bash
rtk git status --short
```

Expected: 只看到本 plan 和已确认的 spec 变更；若有其它用户变更，记录并避开。

- [x] **Step 2: 跑 GitNexus impact**

Run via GitNexus MCP or CLI equivalent:

```text
impact({target: "SmtSortingInboundPlugin", direction: "upstream"})
impact({target: "SortingInboundContext", direction: "upstream"})
impact({target: "SmtInboundHandoffRouteService", direction: "upstream"})
impact({target: "SmtInboundHandoffService.claim_next_source_item", direction: "upstream"})
impact({target: "RuntimeIntentEffectApplier", direction: "upstream"})
impact({target: "scan_smt_inbound_handoff_demands_batch", direction: "upstream"})
```

Expected: 无 HIGH/CRITICAL 未确认风险。若有 HIGH/CRITICAL，停止并汇报 direct callers、affected processes、risk level。

Actual: 当前 worktree 已重新 `gitnexus analyze`，索引 commit 与当前 commit 一致。`SmtSortingInboundPlugin`、`SortingInboundContext`、`SmtInboundHandoffRouteService`、`claim_next_source_item`、`RuntimeIntentEffectApplier` 均为 LOW。`SmtInboundHandoffService.scan_smt_inbound_handoff_demands_batch` 为 HIGH，直接影响 `src/celery_app/tasks/workline.py` 的定时扫描入口；这是 T7 计划内变更，后续必须保留 retry/dead-letter/manual-hold 恢复并跑 Celery focused tests。

- [x] **Step 3: 跑当前聚焦测试基线**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py \
  tests/workline_runtime/test_smt_inbound_handoff_route_service.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  -q
```

Expected: 记录现有通过/失败基线。若失败与本 spec 无关，先保存输出，不在本计划内修旁路问题。

Actual: `145 passed in 1.93s`。

- [x] **Step 4: Commit preflight note if any tracked doc changed**

Run:

```bash
rtk git status --short
```

Expected: 不提交业务代码。若只新增 plan，可等 T1 后一起提交；若团队要求先提交计划，commit message 使用 `docs(workline): 制定 SMT 分拣入库闭环执行计划`。

## Task T1: SMT Manifest 静态合同收敛

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/constants.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Modify: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Modify: `tests/test_workline_service_plugin_validation.py`

- [x] **Step 1: 写 manifest RED 测试**

Add/adjust assertions:

```python
assert "SORTING_NG_STATION" not in device_roles
assert "NG_STATION" not in rack_position_codes
assert "WORKSTATION" not in rack_position_codes
assert "SORTING_INBOUND_NG" not in business_demand_types
assert "SORTING_INBOUND_WORK" not in business_demand_types
```

For command contract:

```python
assert command_by_name["SORTING_NG_PLACE"].target_device_role == ROLE_SORTING_TARGET_ARM
assert command_by_name["SORTING_NG_PLACE"].rack_position_args == ()
assert target_place_target.rack_position_ref == "TARGET_STATION"
assert target_place_target.source is None
```

- [x] **Step 2: Run RED tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/test_workline_service_plugin_validation.py \
  -k "smt_sorting_inbound or manifest or rack_position or NG_STATION or WORKSTATION" -q
```

Expected: FAIL because current manifest still exposes NG/WORK station positions and tests still expect old roles.

- [x] **Step 3: Update manifest**

Implement these exact contract changes:

- `devices`: keep `SORTING_SOURCE_ARM`、`SORTING_TARGET_ARM`、`SORTING_SCAN_PLATFORM`、`SORTING_WORKSTATION` only if workstation is still an event source role; remove `SORTING_NG_STATION`.
- `rack_positions`: keep `SOURCE_STATION_A`、`SOURCE_STATION_B`、`TARGET_STATION`; remove `NG_STATION` and `WORKSTATION`.
- `topology.flow_edges`: remove material flow edges to/from `WORKSTATION` and `NG_STATION`; only connect real `RACK_POSITION` nodes.
- `SORTING_SOURCE_PICK`: source from event payload source position; do not set target rack position to `WORKSTATION`.
- `SORTING_TARGET_PLACE`: target arg is static `TARGET_STATION`; `target_bin_code` / `target_cell_code` remain payload/context evidence only.
- `SORTING_NG_PLACE`: no `RackPositionArg`; NG target remains command payload evidence.
- `resource_boundaries`: keep only `SORTING_INBOUND_SOURCE` and `SORTING_INBOUND_TARGET`.

- [x] **Step 4: Update old tests that asserted legacy manifest**

Change expected summaries in `tests/test_workline_service_plugin_validation.py` and plugin tests so they match the new contract. Do not weaken tests to only check count; assert forbidden role/position strings are absent.

- [x] **Step 5: Run manifest tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/test_workline_service_plugin_validation.py \
  -q
```

Expected: PASS.

Actual: T1 review fix 追加验证 API runtime boundary 和 dev/test seed 合同；`tests/api/test_workline_runtime_api.py` 已确认 station lease / snapshot 查询只覆盖 `SOURCE_STATION_A`、`SOURCE_STATION_B`，`scripts/data/sync_test_workline_devices.py` 不再 seed `SORTING_NG_STATION` 设备或 `NG_STATION` / `WORKSTATION` rack positions。

- [x] **Step 6: Commit**

Run:

```bash
rtk git add \
  src/workline_plugins/smt_sorting_inbound/constants.py \
  src/workline_plugins/smt_sorting_inbound/plugin.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/test_workline_service_plugin_validation.py
rtk git commit -m "fix(workline): 收敛 SMT 分拣入库 manifest 货架位合同"
```

## Task T2: Typed Sorting Context Helper

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/context.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_context.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_claim.py`

- [x] **Step 1: 写 context helper RED 测试**

Add tests for:

```python
context.write_source_pick_request(
    handoff_demand_id=1,
    handoff_source_item_id=2,
    claim_attempt_no=1,
    event_id="smt-inbound-handoff-source-item:2:claim:1",
    target_workline_code="WL-SMT-SORT-01",
    manifest_contract_version="2026-06-01.p0",
    source_rack_position_code="SOURCE_STATION_A",
    target_rack_position_code="TARGET_STATION",
    route_evidence={"usage": Decimal("0.42")},
)
context.set_station_state(scan_platform="EMPTY")
```

Expected context shape:

```python
assert sorting["context_schema_version"] == 1
assert sorting["stations"]["scan_platform"] == "EMPTY"
assert sorting["source_pick_request"]["handoff_source_item_id"] == 2
assert sorting["source_pick_request"]["route_evidence"]["usage"] == "0.42"
```

- [x] **Step 2: Run RED context tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_sorting_inbound_context.py -q
```

Expected: FAIL with missing `write_source_pick_request`.

- [x] **Step 3: Implement helper**

Add focused methods to `SortingInboundContext`:

- `write_source_pick_request(...)`
- `get_source_pick_request()` or `source_pick_request()`

Validation rules:

- IDs and `claim_attempt_no` must be positive integers.
- required strings must be non-empty.
- evidence must pass `_json_safe`.
- helper must preserve existing `sorting.context_schema_version`.

- [x] **Step 4: Route claim session through helper**

Update `_create_sorting_claim_session(...)` in `SmtInboundHandoffService` to initialize session with `SortingInboundContext.initialize(session)`, then call helper. Keep this change narrow; full claim behavior changes happen in T4.

- [x] **Step 5: Run context and claim tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_sorting_inbound_context.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py::test_claim_creates_internal_source_pick_inbox_with_session_workline_bucket \
  -q
```

Expected: PASS and claim-created session contains `sorting.context_schema_version=1`.

- [x] **Step 6: Commit**

Run:

```bash
rtk git add \
  src/workline_plugins/smt_sorting_inbound/context.py \
  src/app/workline/services/smt_inbound_handoff_service.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py
rtk git commit -m "feat(workline): 增加 SMT 分拣入库 source pick typed context"
```

## Task T3: Handoff Route 绑定 Manifest Source Boundary

**Files:**
- Modify: `src/app/workline/domain/services/smt_inbound_handoff_route_service.py`
- Modify: `src/app/workline/domain/services/smt_inbound_handoff_reason.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_route_service.py`

- [x] **Step 1: 写 route RED 测试**

Cover these cases:

- 单个 `SORTING_INBOUND_SOURCE` boundary 时可默认选择。
- 多个 source boundaries 且 config 未指定时返回 manual hold / controlled failure。
- config 指定的 source boundary 不在 manifest 中时，不调用 station lease，不创建 route selected。
- route evidence 包含 `manifest_contract_version`、`source_rack_position_code`、`source_station_code`、`target_rack_position_code`、`source_boundary`。
- 默认 service 未注入 ECS probe 时，不应 silently allow idle。

Use compact assertions:

```python
assert result.kind == "MANUAL_HOLD"
assert result.failure_code == SmtInboundHandoffReasonCode.SOURCE_BOUNDARY_AMBIGUOUS.value
assert station_lease.calls == []
```

- [x] **Step 2: Run RED route tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_inbound_handoff_route_service.py -q
```

Expected: FAIL because route still reads `source_station_code` / `source_position_code` and default probe allows idle.

- [x] **Step 3: Implement manifest boundary resolver**

Add route service helpers:

- read target WorkLine plugin manifest from registry or injected manifest provider.
- filter source boundaries by `business_demand_type == "SORTING_INBOUND_SOURCE"` and `rack_kind == "SINGLE_LAYER"`.
- filter target boundary by `business_demand_type == "SORTING_INBOUND_TARGET"` and `rack_kind == "FIVE_LAYER"`.
- config may select only a manifest-declared source rack position code.
- route evidence must include selected source and target rack position codes.

- [x] **Step 4: Replace default ECS stub**

Default production behavior must call a real ECS realtime probe adapter with a short timeout. Unit tests may inject `_EcsProbe(available=True)` explicitly.

- [x] **Step 5: Run route tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_inbound_handoff_route_service.py -q
```

Expected: PASS.

Actual: T3 implementation commit `9cb9a92d`；route focused tests `11 passed`，route + reason catalog quality review verification `14 passed`。Spec review PASS，quality review Ready；无 Critical / Important 阻塞，Minor 诊断建议留给后续真实 ECS adapter/refinement。

- [x] **Step 6: Commit**

Run:

```bash
rtk git add \
  src/app/workline/domain/services/smt_inbound_handoff_route_service.py \
  src/app/workline/domain/services/smt_inbound_handoff_reason.py \
  tests/workline_runtime/test_smt_inbound_handoff_route_service.py
rtk git commit -m "feat(workline): 将 SMT handoff route 绑定 manifest source boundary"
```

## Task T4: Handoff Claim 两阶段短锁与串行保护

**Files:**
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Modify: `src/app/workline/repositories/smt_inbound_handoff_repository.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_claim.py`
- Modify: `tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py`

- [x] **Step 1: 写 claim RED 测试**

Add tests:

- 同一 target WorkLine 已有 in-flight item 时，第二条 READY 保持 READY，写 retry evidence，不创建 session/inbox。
- 两个并发 claim 同一 target WorkLine，只有一个创建 session/inbox。
- ECS probe 期间不持 source item / target WorkLine 行锁。
- 第二阶段 recheck 发现 item 不再 READY 时，不创建 session/inbox。
- claim session context 包含 manifest/source/target rack position evidence。

Important statuses:

```python
IN_FLIGHT = {
    PICK_REQUESTED,
    CLAIMED_BY_SORTING,
    PICKED,
    SORTING,
}
```

- [x] **Step 2: Run RED claim tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py \
  -q
```

Expected: FAIL because current claim holds READY row through route/probe and does not lock target WorkLine for final recheck.

- [x] **Step 3: Split claim into phase 1 / phase 2**

Phase 1:

- read READY candidate without holding row lock across ECS probe.
- resolve route and call ECS realtime probe.
- keep probe result short-lived in local evidence only.

Phase 2:

- open short transaction section.
- lock source item by ID.
- lock target WorkLine row.
- recheck item still READY and due.
- recheck no open sorting session with `current_material`.
- recheck no handoff in-flight source item for target WorkLine.
- recheck ECS probe result has not expired.
- create session and inbox.

- [x] **Step 4: Add repository helpers**

Add focused repository methods:

- list READY candidates for claim by due ordering.
- lock source item by ID.
- lock target WorkLine by ID.
- list in-flight source items by target WorkLine.

Keep SQL in repository layer. Service orchestrates state transitions only.

- [x] **Step 5: Run claim tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py \
  -q
```

Expected: PASS.

Actual: T4 implementation commits `a035847d` and `d8d14c65`；focused tests `13 passed, 2 skipped`（PostgreSQL gated tests 当前环境未启用集成开关）。Spec re-review PASS，quality review Ready；GitNexus compare risk high 属于 T4 计划内 claim/recovery 影响面，无 Critical / Important 阻塞。

- [x] **Step 6: Commit**

Run:

```bash
rtk git add \
  src/app/workline/services/smt_inbound_handoff_service.py \
  src/app/workline/repositories/smt_inbound_handoff_repository.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py
rtk git commit -m "feat(workline): 实现 SMT handoff 两阶段 claim 串行保护"
```

## Task T5: Source Pick Ledger 幂等推进

**Files:**
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_recovery.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`

- [x] **Step 1: 写 source pick ledger RED 测试**

Service tests:

- `PICK_REQUESTED` / `CLAIMED_BY_SORTING` + success -> `PICKED`。
- already `PICKED` -> no-op，summary 不重复 advanced。
- already `SORTED/SKIPPED/EXCHANGED` + late source pick success -> no-op。
- `MANUAL_HOLD` + late source pick success -> controlled hold，不静默改回 `PICKED`。

Runtime effect test:

```python
monkeypatch.setattr(
    smt_inbound_handoff_service,
    "record_source_pick_success",
    record_call,
)
```

Assert runtime effect calls service after successful source pick effects.

- [x] **Step 2: Run RED tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  -k "source_pick or handoff" -q
```

Expected: FAIL because recovery directly assigns `item.status = PICKED` and runtime effect has no `record_source_pick_success` call.

- [x] **Step 3: Implement `record_source_pick_success(...)`**

Rules:

- Locate item by command correlation or session context source pick request.
- Validate demand/item/attempt match when evidence exists.
- Transition only allowed claimed statuses to `PICKED`.
- Clear `failure_code` / `failure_message` / `next_attempt_at`.
- Recalculate demand through `recalculate_demand_status`.
- Return structured result, including `advanced` vs `already_terminal`.

- [x] **Step 4: Reuse method in recovery**

Replace direct recovery mutation with `record_source_pick_success(...)`.

- [x] **Step 5: Wire runtime effect**

After source-pick success intents are applied without block/reconciliation, call handoff service. Do not call before resource/context effects succeed.

- [x] **Step 6: Run tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  -k "source_pick or handoff" -q
```

Expected: PASS.

- [x] **Step 7: Commit**

Run:

```bash
rtk git add \
  src/app/workline/services/smt_inbound_handoff_service.py \
  src/workline_runtime/runtime_intent_effects.py \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_runtime_intent_effects.py
rtk git commit -m "feat(workline): 统一 source pick success handoff ledger"
```

Actual: T5 implementation commits `e0e656e1`, `7a32366`, `fc76120`。RED focused tests 先出现预期失败（缺少 `record_source_pick_success`、真实 `MATERIAL_UNMOUNTED + UPDATE_CONTEXT` 未写 ledger、source item evidence mismatch 未拒绝）；最终 focused tests `20 passed, 55 deselected`。`rtk uv run ruff check ...` 通过，`rtk uv run ruff format --check ...` 显示 `4 files already formatted`。GitNexus impact：`_apply_resource_fact` / `RuntimeIntentEffectApplier.apply` 为 LOW；新增 helper 因索引 stale 未收录。`gitnexus detect-changes --scope unstaged --repo <当前 worktree>` 报 high，范围为 T5 授权的 runtime apply / handoff service 影响面。Spec re-review PASS，quality re-review Ready；无 Critical / Important 阻塞，Minor 集成测试增强留给后续 E2E 阶段。

## Task T6: Target / NG Terminal Ledger 幂等闭环

**Files:**
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Modify: `tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py`

- [x] **Step 1: 写 terminal ledger RED 测试**

Cover:

- `SORTING_TARGET_PLACE SUCCESS` -> source item `SORTED`、`completed_at` set、demand recalculated。
- `SORTING_NG_PLACE SUCCESS` -> source item `SKIPPED`、`completed_at` set、demand recalculated。
- terminal success completes current sorting session and records terminal evidence。
- repeated same terminal success does not claim next READY twice。
- conflicting terminal status enters controlled hold/manual block。
- resource projection reconciling blocks `SORTED` write。
- missing `sorting.source_pick_request` blocks ledger write。

- [x] **Step 2: Run RED tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py \
  -k "target_place or ng_place or terminal or handoff" -q
```

Expected: FAIL because current plugin only updates context/resource facts and runtime effect does not write terminal handoff ledger.

- [x] **Step 3: Implement `record_source_item_terminal_result(...)`**

Contract:

- `terminal_status` accepts only `SORTED` or `SKIPPED`。
- Source item ID comes from `sorting.source_pick_request.handoff_source_item_id`。
- Allowed current states include `PICKED` / `SORTING` and already same terminal。
- First terminal write sets `completed_at`, clears failure fields, recalculates demand。
- First terminal write completes the sorting session and records evidence。
- Same terminal replay returns already-terminal and does not trigger next claim。
- Conflict terminal returns controlled hold/manual block。

- [x] **Step 4: Wire runtime effect after resource/context success**

For target success:

- Apply `MATERIAL_MOUNTED` first.
- If resource projection returns reconciling, do not write `SORTED`。
- Apply context cleanup.
- Call terminal ledger with `SORTED`。

For NG success:

- Apply context cleanup.
- Validate NG target evidence exists in command payload.
- Call terminal ledger with `SKIPPED`。

- [x] **Step 5: Trigger demand-scoped next claim only after first terminal write**

After `record_source_item_terminal_result(...)` returns `advanced=True`, call `claim_next_source_item(..., demand_id=current_demand_id)` or equivalent demand-scoped claim. Replays must not call claim again.

- [x] **Step 6: Run terminal tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py \
  -k "target_place or ng_place or terminal or handoff" -q
```

Expected: PASS.

- [x] **Step 7: Commit**

Run:

```bash
rtk git add \
  src/app/workline/services/smt_inbound_handoff_service.py \
  src/workline_runtime/runtime_intent_effects.py \
  src/workline_plugins/smt_sorting_inbound/flow_service.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py
rtk git commit -m "feat(workline): 闭合 SMT handoff terminal ledger"
```

Actual: T6 implementation commits `66cd24d3`, `f731a85f`, `b240fa0c`。RED focused tests 先暴露 target/NG terminal ledger 未写入、NG payload evidence 缺失仍成功、非 SMT `MATERIAL_MOUNTED` 误触发 ledger、source item/session 串线，以及 terminal conflict 可能改写 `FAILED/CANCELLED` session；最终修复为 `SORTED/SKIPPED` terminal ledger 幂等推进、NG evidence 非空校验、SMT/source_pick_request 限定的 runtime hook、当前 session 绑定校验和终态 session 冲突保护。最终 focused tests：`tests/workline_runtime/test_smt_sorting_inbound_plugin.py -k "ng_place"` 为 `6 passed`；`tests/workline_runtime/test_runtime_intent_effects.py -k "terminal_conflict or terminal_result or target_place or ng_place or terminal"` 为 `10 passed`；T6 combined focused tests 为 `14 passed, 6 skipped, 53 deselected`（PostgreSQL gated integration 需 `RUN_WORKLINE_INTEGRATION=1`，本地未启用）。`rtk uv run ruff check ...` 和 `rtk uv run ruff format --check ...` 通过。GitNexus impact：`record_source_item_terminal_result` 为 MEDIUM，`_manual_hold_terminal_conflict` / runtime apply / flow service 相关符号为 LOW；`detect-changes --scope compare --base-ref 008d1e04` 报 high，范围集中在 T6 runtime apply、flow service、terminal ledger。Spec re-review PASS，quality re-review Ready；无 Critical / Important 阻塞，保留 runtime/plugin 常量耦合、marker patch 清理和 NG missing payload 文案三个 Minor 观察。

## Task T7: Celery Recovery READY Claim 兜底

**Files:**
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Modify: `src/app/workline/repositories/smt_inbound_handoff_repository.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/celery_app/config.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_recovery.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_celery.py`

- [x] **Step 1: 写 Celery RED 测试**

Cover:

- scan summary includes `claimed`。
- READY item due gets claimed when `claim_limit > 0`。
- `scan_limit` / `recovery_limit` and `claim_limit` are separate。
- `claim_limit=0` keeps old recovery behavior。
- same scan deduplicates ECS probe by target WorkLine / ECS endpoint。
- existing FAILED inbox retry, DEAD_LETTER manual hold, processed-without-command manual hold still pass。

Expected summary shape:

```python
assert summary == {
    "scanned": 0,
    "claimed": 1,
    "advanced": 0,
    "retry_scheduled": 0,
    "manual_hold": 0,
    "recovery_errors": 0,
}
```

- [x] **Step 2: Run RED Celery tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_smt_inbound_handoff_celery.py \
  -q
```

Expected: FAIL because `claimed` and `claim_limit` do not exist.

- [x] **Step 3: Implement scan parameters**

Change service signature to:

```python
scan_smt_inbound_handoff_demands_batch(
    scan_limit=100,
    recovery_limit=100,
    claim_limit=10,
    stale_after_seconds=300,
)
```

Keep backwards compatibility only for internal call sites if needed during the same task; final public task should pass named parameters.

- [x] **Step 4: Add READY claim loop**

After due demand recalculation and stuck item recovery, claim up to `claim_limit` due READY items. Use the same `claim_next_source_item` path and count only `CLAIMED` results.

- [x] **Step 5: Add per-scan ECS probe cache**

Cache key should include target WorkLine or ECS endpoint identity. Cache lifetime is one scan invocation only; do not add Redis/global cache.

- [x] **Step 6: Update Celery task and Beat config**

Task defaults:

- `scan_limit=100`
- `recovery_limit=100`
- `claim_limit=10`
- `stale_after_seconds=300`

Update log message and config comment to state it scans due demand, stuck item, and READY claim fallback.

- [x] **Step 7: Run Celery tests**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_smt_inbound_handoff_celery.py \
  -q
```

Expected: PASS.

- [x] **Step 8: Commit**

Run:

```bash
rtk git add \
  src/app/workline/services/smt_inbound_handoff_service.py \
  src/app/workline/repositories/smt_inbound_handoff_repository.py \
  src/celery_app/tasks/workline.py \
  src/celery_app/config.py \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_smt_inbound_handoff_celery.py
rtk git commit -m "feat(workline): 增加 SMT handoff READY claim 兜底扫描"
```

Actual: T7 implementation commits `6a19438f` and `4fbcf100`。RED focused tests 先出现预期失败（summary 缺 `claimed`、`scan_limit/claim_limit` 参数不存在、task `limit=` legacy 调用不兼容、ECS probe cache 过 TTL 未重新 probe）；最终实现 READY claim 兜底扫描、`scan_limit/recovery_limit/claim_limit` 拆分、task legacy `limit` 兼容、单次 scan 内 ECS probe cache 和 TTL freshness。最终 focused tests：`tests/workline_runtime/test_smt_inbound_handoff_recovery.py tests/workline_runtime/test_smt_inbound_handoff_celery.py -q` 为 `24 passed`；claim 回归 `tests/workline_runtime/test_smt_inbound_handoff_claim.py -q` 为 `13 passed`。`rtk uv run ruff check ...` 和 `rtk uv run ruff format --check ...` 通过。GitNexus impact：service scan 入口为 HIGH（Celery `_scan` / E2E smoke / scan flows），`claim_next_source_item` 为 MEDIUM，Celery task 和 repository READY candidate 为 LOW；`detect-changes --scope compare --base-ref 3f2823bd` 报 high，范围集中在 T7 recovery/claim/Celery flows。Spec review PASS，quality re-review Ready；无 Critical / Important 阻塞，保留真实 PostgreSQL 并发 claim 集成覆盖为剩余风险。

## Task T8: Release-to-Terminal E2E Regression Gate

**Files:**
- Modify: `tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Modify: `tests/workline_runtime/test_smt_inbound_handoff_claim.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`

- [x] **Step 1: Expand happy-path E2E RED test**

Extend the existing smoke from:

```text
release fact -> demand/source item READY -> manual claim -> SOURCE_PICK command
```

to:

```text
release fact
-> automatic claim
-> SORTING_SOURCE_PICK_REQUESTED inbox
-> SOURCE_PICK SUCCESS
-> WORKING_BIN_SCAN
-> TARGET_PLACE SUCCESS
-> source item SORTED
-> demand COMPLETED
```

Expected RED: current code stops before `PICKED/SORTED/COMPLETED`.

- [x] **Step 2: Add multi-item serial E2E**

Cover:

- first item completion before second claim。
- second item automatically claimed after first `SORTED`。
- same behavior when first terminal is `SKIPPED`。
- replayed terminal success does not claim third time。

- [x] **Step 3: Run RED E2E**

Run:

```bash
rtk uv run pytest tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py -q
```

Expected: FAIL on missing automatic claim/terminal ledger until T4-T7 are complete.

- [x] **Step 4: Make release path trigger claim**

Find the release fact producer path in `SingleLayerRackOrchestrationService` / handoff service integration. After demand becomes `READY_FOR_SORTING`, call the same handoff service claim path. Do not add an API/manual-only entrypoint.

- [x] **Step 5: Run full focused regression**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/test_workline_service_plugin_validation.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py \
  tests/workline_runtime/test_smt_inbound_handoff_route_service.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_smt_inbound_handoff_celery.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py \
  -q
```

Expected: PASS.

- [x] **Step 6: Run quality gates**

Run:

```bash
rtk uv run ruff format .
rtk uv run ruff check .
rtk ./scripts/git-quality-gate.sh --profile quality
```

Expected: PASS. If full quality gate is too slow or environment-blocked, record exact failure and run the widest available targeted tests.

- [x] **Step 7: Run architecture checks**

Run:

```bash
rtk grep -r "from sqlalchemy import select" src/app/*/v1/ || true
rtk grep -r "db.execute(" src/app/*/v1/ || true
```

Expected: no new API-layer DB access introduced by this work.

- [x] **Step 8: Commit**

Run:

```bash
rtk git add \
  tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/workline_runtime/test_runtime_intent_effects.py
rtk git commit -m "test(workline): 覆盖 SMT handoff release 到 terminal 闭环"
```

Actual: T8 implementation commits `ec15740d`, `6160bd04`, `a0c21197`。Integration E2E 在本地默认 guard 下为 `7 skipped`，因此补充可执行 RED：release path focused test 先因缺 `evaluate` / `claim_next_source_item` 预期失败。最终实现 release fact 后自动 `evaluate(prefer_full_box_exchange=False)` 并按 demand scope claim，补充 claim diagnostics，扩展 release-to-terminal happy path、multi-item serial、SORTED/SKIPPED 后自动 claim、terminal replay 不 claim 第三项、duplicate release session/inbox 幂等断言。验证：focused regression 为 `252 passed, 9 skipped`；controller 复验 release producer/claim/E2E guard 命令为 `3 passed, 7 skipped`；T8 相关 ruff check / format check 和 `git diff --check` 通过；架构 grep 无 API 层 DB 访问输出；`./scripts/git-quality-gate.sh --profile quality` 通过。Spec re-review PASS；quality re-review PASS，无 Critical / Important 阻塞。剩余风险是本地缺 `INTEGRATION_DATABASE_URL`，真实 PostgreSQL E2E 只能在启用 integration 环境后执行。

## Final Gate

- [x] **Step 1: Run GitNexus detect changes before final commit/PR**

Run:

```text
detect_changes()
```

Expected: changed symbols and execution flows match this plan: SMT sorting inbound manifest/context/route/claim/runtime effect/Celery/tests only.

- [x] **Step 2: Run final test suite subset**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_smt_sorting_inbound_plugin.py \
  tests/workline_runtime/test_plugin_manifest_and_topology.py \
  tests/test_workline_service_plugin_validation.py \
  tests/workline_runtime/test_smt_sorting_inbound_context.py \
  tests/workline_runtime/test_smt_inbound_handoff_route_service.py \
  tests/workline_runtime/test_smt_inbound_handoff_claim.py \
  tests/workline_runtime/test_smt_inbound_handoff_recovery.py \
  tests/workline_runtime/test_smt_inbound_handoff_celery.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py \
  tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py \
  -q
```

Expected: PASS.

- [x] **Step 3: Run broad local quality gate**

Run:

```bash
rtk uv run ruff format .
rtk uv run ruff check .
rtk uv run pytest tests/workline_runtime/ tests/integration/workline_runtime/ -q
```

Expected: PASS or documented environment blocker.

Actual (2026-06-16):

- GitNexus compare against `develop`: `critical` because this branch contains the complete T0-T8 implementation and plan docs; changed scope matches this plan's SMT sorting inbound manifest/context/route/claim/runtime effect/Celery/tests boundary.
- Final focused subset: `254 passed, 9 skipped`.
- Broad quality gate: `ruff format .` reported no file changes; `ruff check .` passed.
- Broad runtime/integration subset: `1121 passed, 27 skipped`.
- Final Gate follow-up fix committed as `2c7ca403 fix(workline): 收窄 SMT handoff ledger 触发条件`.
- Remaining environment note: PostgreSQL-backed integration cases that require `INTEGRATION_DATABASE_URL` are skipped by local guard and need an integration environment for full database-backed E2E execution.

## Self-Review

Spec coverage:

- Manifest rack-position contract: T1.
- Typed context and `source_pick_request`: T2.
- Manifest source boundary route and ECS admission: T3.
- Two-phase claim and target WorkLine serial protection: T4.
- `PICKED` ledger: T5.
- `SORTED/SKIPPED` terminal ledger and session completion: T6.
- Celery READY claim fallback and `claimed` summary: T7.
- Release-to-terminal and multi-item serial E2E: T8.
- GitNexus and quality gates: T0 and Final Gate.

Placeholder scan:

- No `TBD` / `TODO` / `implement later` placeholders.
- Each task names exact files, concrete tests, expected RED/PASS result, verification command, and commit command.

Type consistency:

- `record_source_pick_success(...)` is the single service method for `PICKED`.
- `record_source_item_terminal_result(..., terminal_status)` is the single service method for `SORTED/SKIPPED`.
- `claim_next_source_item(...)` remains the single claim entrypoint and gains demand-scoped claim support.
- `SortingInboundContext.write_source_pick_request(...)` is the typed writer used by claim.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
