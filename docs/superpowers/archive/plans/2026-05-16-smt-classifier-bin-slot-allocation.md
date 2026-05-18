# SMT Classifier Bin Slot Allocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 `smt_classifier` 在粗分机出料口的逐料料箱格分配闭环：物料到达出料口后按 6 合 1 的 DC/LC 判断当前到位货架是否可存，可存则下发具体料箱格命令，不可存则请求 WMS/RCS 移出当前货架并补空料箱货架，补架回调后继续粗分机出料。

**Architecture:** 本计划复用当前 staged diff 已新增的 `RuntimeIntent.external_request(...)`、`EXTERNAL_HTTP` Outbox、`WAITING_EXTERNAL` 和外部回调幂等能力。`smt_classifier` 只通过 `WorklineRuntimeServices.bin_allocator` 访问领域调度服务，不直接访问 Repository 或数据库；调度服务 v1 使用 `session.context_json` 中的当前货架快照做执行判断，后续可替换为资源模型 Repository 投影。

**Tech Stack:** Python 3.13, FastAPI runtime, Pydantic, dataclass, pytest, Ruff, GitNexus.

---

## 项目约束

本仓库 `AGENTS.md` 明确要求规划文档优先表达目标、架构决策、任务边界、验收标准、风险和验证方式，并禁止在规划文档中粘贴完整类实现、完整函数实现或大段测试代码。因此本计划不提供可复制的大段实现代码；执行者应按每个任务的文件、接口、测试场景和验收命令进行 TDD 实现。

所有沟通、文档和 commit comment 使用中文。执行代码修改前必须按 GitNexus 规则对将要修改的函数、类或方法做 upstream impact analysis；若风险为 HIGH/CRITICAL，先向用户报告风险再继续。

## 当前基线

当前分支已有 17 个 staged 变更，已经完成或部分完成：

- `RuntimeIntentKind.EXTERNAL_REQUEST`
- `PluginNext.external_request(...)`
- `RuntimeIntentEffectApplier._apply_external_request(...)`
- `EXTERNAL_HTTP` outbox 立即进入 `WAITING_EXTERNAL`
- external callback 幂等优先使用 `source_event_id`
- `SmtRackBinSchedulingService` 占位服务
- `smt_classifier` 在 `MOVE_FORWARD SUCCESS` 后可接受调度服务返回外部请求

本计划基于这些 staged 变更继续推进，不重复实施整架满箱交换插件计划 `docs/superpowers/plans/2026-05-13-smt-full-box-exchange.md`。那份计划处理的是单层货架整体移出后的满箱交换，本计划处理的是粗分机出料口逐个物料落格。

## Scope

包含：

- 保存完整 6 合 1 字段到 `SmtClassifierContext`。
- 将 `DateCode/LotCode` 作为格位合并判断依据。
- 用当前到位货架快照选择可合并格或空格。
- 当前货架不可存时发起一个复合外部请求，payload 同时表达“移出当前货架”和“补空料箱货架”。
- `smt_classifier.on_external_http(...)` 处理 WMS/RCS 进度、失败和空架到位回调。
- 空架到位后重新执行分配并下发 `OUTPUT_ARM PICK_AND_PUT`。
- 出料命令 payload 携带 `bin_cell_location`。
- 用单元测试和插件集成测试覆盖业务闭环。

不包含：

- 新建数据库资源模型、Rack/Bin 主数据表或 Alembic migration。
- WES 自主维护库存主账、空箱授权、五层货架空箱锁定或库存扣减。
- 新建 `smt_full_box_exchange` 插件。
- 前端页面。
- WMS/RCS 全量接口实现；本计划只定义 `smt_classifier` 所需最小外部请求和回调合同。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `src/app/workline/domain/services/smt_rack_bin_scheduling_service.py` | SMT 粗分机出料口调度决策。定义当前货架快照、目标物料、决策类型、复合外部请求合同；实现 DC/LC 合并和空格分配。 |
| `src/app/workline/domain/services/__init__.py` | 导出新增调度合同类型和服务单例。 |
| `src/app/workline/domain/__init__.py` | 必要时导出领域服务类型。 |
| `src/workline_runtime/services.py` | 扩展 `BinAllocator` protocol，使运行时服务可声明 `plan_allocation(...)`。 |
| `src/workline_plugins/smt_classifier/context.py` | 保存插件业务上下文，包括完整 6 合 1、当前货架快照、待恢复的外部调度请求状态。 |
| `src/workline_plugins/smt_classifier/contract.py` | 出料命令 payload 增加 `bin_cell_location`；新增最小 WMS/RCS 外部回调常量和解析约定。 |
| `src/workline_plugins/smt_classifier/plugin.py` | 在扫码、测量、出料、外部回调中使用新上下文和调度决策；补 `on_external_http(...)`。 |
| `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py` | 调度领域服务规则测试。 |
| `tests/integration/workline_plugins/test_smt_classifier_plugin_events.py` | 扫码 context 保存完整 6 合 1 的插件测试。 |
| `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py` | 出料分配、不可存外部请求和回调恢复的插件测试。 |
| `tests/workline_runtime/test_runtime_intent_effects.py` | 仅在发现外部回调恢复需要 runtime 组合规则调整时修改。默认不改 runtime。 |
| `docs/business/workline_smt_classifier_runtime_flow.md` | 实现完成后更新粗分机出料口分格和外部补架闭环说明。 |

## 合同与状态约定

### 当前货架快照

`session.context_json["active_bin_rack"]` 是 v1 调度输入，来源可以是工作线初始化、模拟设备、WMS/RCS `WMS_RACK_ARRIVED` 回调或人工对账。结构约定：

| 字段 | 说明 |
| --- | --- |
| `rack_id` | 当前到位货架编码。 |
| `rack_type` | 例如 `SINGLE_LAYER`。 |
| `station_location_id` | 粗分机接料位。 |
| `cells` | 料箱格数组。每个元素描述一个可落料格。 |

`cells` 元素约定：

| 字段 | 说明 |
| --- | --- |
| `bin_id` | 料箱编码。 |
| `bin_type` | 料箱类型。 |
| `bin_cell_location` | 格位编码，必须透传给出料命令。 |
| `status` | `EMPTY` / `OCCUPIED` / `LOCKED` / `DISABLED`。 |
| `DateCode` | 已占用格的 DC；空格可为空。 |
| `LotCode` | 已占用格的 LC；空格可为空。 |
| `PkgID` | 已占用格的物料流水号；仅用于追溯，不参与合并判定。 |

### 调度规则

1. 新物料必须有完整 6 合 1，至少 `DateCode`、`LotCode`、`PkgID` 不为空；缺失时 `smt_classifier` 返回 `PAYLOAD_INVALID` 或 `BLOCK`，不能生成虚拟格位。
2. 当前货架中存在 `status=OCCUPIED` 且 `DateCode/LotCode` 与新物料完全相同的格位时，优先返回该格位。
3. 无可合并格时，选择第一个 `status=EMPTY` 的格位。
4. 无可合并格且无空格时，返回 `RACK_EXCHANGE_REQUIRED` 决策。
5. `LOCKED`、`DISABLED` 或缺少关键字段的格位不参与分配。
6. 若没有 `active_bin_rack`，按 `RACK_EXCHANGE_REQUIRED` 处理，原因码为 `NO_ACTIVE_RACK`。

### 外部请求

Runtime 当前只支持一个 command-producing intent，因此不可在一个回调中产生两个 `EXTERNAL_REQUEST`。本计划使用一个复合外部请求：

| 字段 | 说明 |
| --- | --- |
| `dispatch_key` | `external:smt_classifier:<trace-or-session>:RACK_EXCHANGE_AND_SUPPLY`。 |
| `target_code` | 从 workline config 或 context 读取，例如 `wms_rcs_rack_exchange_url`。 |
| `payload.request_type` | `SMT_RACK_EXCHANGE_AND_SUPPLY`。 |
| `payload.actions` | 包含 `MOVE_OUT_CURRENT_RACK` 和 `SUPPLY_EMPTY_RACK` 两个动作。 |
| `payload.resume_callback_type` | `WMS_RACK_ARRIVED`。 |
| `payload.current_rack_snapshot` | 当前货架快照。 |
| `payload.material` | 当前物料 6 合 1。 |

WMS/RCS 可以内部拆成移架和补架任务；WES Runtime 只等待同一个 `dispatch_key` 对应的外部回调。

### 外部回调

`smt_classifier.on_external_http(...)` 处理这些 `callback_type`：

| callback_type | 处理 |
| --- | --- |
| `WMS_RACK_EXCHANGE_PROGRESS` | 更新 `rack_exchange.status`，保持 `WAITING_EXTERNAL`。 |
| `WMS_RACK_ARRIVED` | 从 payload 写入新的 `active_bin_rack`，重新调用调度服务；若分配成功，生成 `UPDATE_CONTEXT + COMMAND`。 |
| `WMS_RACK_EXCHANGE_FAILED` | 返回 `BLOCK`，记录原因。 |

回调必须校验 `trace_id`、`dispatch_key` 或 `exchange_request_code` 与 context 中的待恢复请求一致。缺失或不匹配时返回 `BLOCK` 或让现有 runtime diagnostic 记录失败，不能静默继续。

## Task 0: 执行前安全检查

**Files:**
- Review only: current staged diff

- [ ] **Step 1: 确认当前 staged 基线**

Run:

```bash
rtk git status -sb
rtk git diff --cached --stat
```

Expected:

- 仍在 `feat/smt-execution-resource-model` 分支。
- staged diff 包含 Runtime `EXTERNAL_REQUEST` 和当前占位调度服务。

- [ ] **Step 2: 跑当前相关测试，确认基线未坏**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/workline_runtime/test_runtime_intent_contract.py tests/workline_runtime/test_plugin_next.py tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_inbox_service.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

Expected:

- 全部通过。若失败，先修复当前 staged 基线，再执行本计划。

- [ ] **Step 3: GitNexus 影响分析**

Before editing symbols, run upstream impact analysis for:

- `SmtRackBinSchedulingService`
- `SmtRackBinSchedulingDecision`
- `SmtClassifierContext`
- `SmtClassifierPlugin.handle_scan_completed`
- `SmtClassifierPlugin.handle_conveyor_success`
- `SmtClassifierPlugin._allocate_bin`
- `build_output_to_bin_params`

Expected:

- 记录 direct callers、affected processes、risk level。
- 若任一结果为 HIGH 或 CRITICAL，先向用户报告再继续。

## Task 1: 保存完整 6 合 1 到 SMT 插件上下文

**Files:**
- Modify: `src/workline_plugins/smt_classifier/context.py`
- Modify: `src/workline_plugins/smt_classifier/plugin.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_events.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`

- [ ] **Step 1: 写失败测试**

Add tests covering:

- `SCAN_COMPLETED` OK 后 context patch 包含 `six_in_one`，字段为 `HHPN/MfrPN/Qty/DateCode/LotCode/PkgID`。
- `MEASUREMENT_REEL SUCCESS` 不覆盖已保存的 `six_in_one`，只补 `reel_diameter` 和 `reel_thickness`。
- `MOVE_FORWARD SUCCESS` 调用 allocator 时，传入 context 包含 `six_in_one`。

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_events.py::TestSmtClassifierPluginEvents::test_scan_completed_persists_six_in_one_context -q
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_passes_six_in_one_to_allocator -q
```

Expected:

- 失败原因是 context 缺少 `six_in_one` 或 allocator context 断言失败。

- [ ] **Step 2: 扩展 Context 合同**

Modify `SmtClassifierContext`:

- 新增 `six_in_one: dict[str, Any] | None`。
- 新增 `active_bin_rack: dict[str, Any] | None`。
- 新增 `rack_exchange: dict[str, Any] | None`。

Do not add database access or repository dependency.

- [ ] **Step 3: 扫码 OK 写入 6 合 1**

Modify `handle_scan_completed`:

- 在 OK 分支用 `barcode_decision.six_in_one.model_dump(...)` 写入 `six_in_one`。
- 继续保留现有 `barcodes/location/barcode/device_code`。
- NG 或 invalid 分支不需要进入出料调度，但可保留已有 `barcodes` 和原因字段。

- [ ] **Step 4: allocator context 使用完整 session context**

Ensure `_allocate_bin` keeps passing `dict(ctx.session.context_json or {})` to `plan_allocation(...)` and tests assert `six_in_one` is present.

- [ ] **Step 5: 验证**

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_events.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py -q
```

Expected:

- 新增测试通过。
- 既有 `smt_classifier` plugin tests 继续通过。

- [ ] **Step 6: Commit**

Commit message:

```bash
git commit -m "feat(smt): preserve six-in-one context for bin scheduling"
```

## Task 2: 实现 DC/LC 格位调度规则

**Files:**
- Modify: `src/app/workline/domain/services/smt_rack_bin_scheduling_service.py`
- Modify: `src/app/workline/domain/services/__init__.py`
- Modify: `src/app/workline/domain/__init__.py`
- Modify: `src/workline_runtime/services.py`
- Test: `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`

- [ ] **Step 1: 写失败测试**

Add scheduling tests:

- same DC/LC chooses occupied compatible cell.
- different DC/LC chooses first empty cell.
- full rack without compatible cell returns `RACK_EXCHANGE_REQUIRED`.
- missing active rack returns `RACK_EXCHANGE_REQUIRED` with reason `NO_ACTIVE_RACK`.
- locked/disabled cells are ignored.

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py -q
```

Expected:

- 新规则相关测试失败，因为当前服务仍返回 md5 虚拟格位。

- [ ] **Step 2: 调整调度合同**

Modify `smt_rack_bin_scheduling_service.py`:

- Add decision kind enum or literal field, with values `ALLOCATED`, `RACK_EXCHANGE_REQUIRED`, `BLOCKED`.
- Replace the two-field exclusive decision with explicit fields:
  - `kind`
  - `bin_location`
  - `external_request`
  - `reason_code`
  - `message`
- Keep backward compatibility where practical: old mapping allocator results still normalize as `ALLOCATED`.

Important:

- Do not keep md5 allocation as production default when `plan_allocation(...)` is called.
- It is acceptable to keep `allocate(...)` only as a deprecated compatibility fallback used by old tests, but new `plan_allocation(...)` must use context rack snapshot.

- [ ] **Step 3: 实现规则**

Implement `plan_allocation(barcode, context=...)`:

- Read `context["six_in_one"]`.
- Read `context["active_bin_rack"]`.
- Validate `DateCode`, `LotCode`, `PkgID`.
- Scan compatible occupied cells first.
- Then scan empty cells.
- Otherwise build a composite rack exchange/supply external request.

Do not call WMS/RCS directly in this service; return a decision only.

- [ ] **Step 4: 外部请求 payload 合同**

External request payload must include:

- `request_type="SMT_RACK_EXCHANGE_AND_SUPPLY"`
- `material`
- `current_rack_snapshot`
- `actions=[MOVE_OUT_CURRENT_RACK, SUPPLY_EMPTY_RACK]`
- `resume_callback_type="WMS_RACK_ARRIVED"`

Use deterministic `dispatch_key` from trace/session context when available, otherwise from `PkgID` plus current rack id. The key must be stable for retry of the same wait.

- [ ] **Step 5: Runtime service protocol**

Update `BinAllocator` in `src/workline_runtime/services.py` so static typing documents `plan_allocation(barcode, context=...)`. Existing tests using only `allocate(...)` should still be valid during migration.

- [ ] **Step 6: 验证**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py -q
rtk uv run ruff check src/app/workline/domain/services/smt_rack_bin_scheduling_service.py src/workline_runtime/services.py
```

Expected:

- 调度规则测试通过。
- Ruff check 通过。

- [ ] **Step 7: Commit**

Commit message:

```bash
git commit -m "feat(smt): allocate bin slot by DC LC rack snapshot"
```

## Task 3: 出料命令携带具体料箱格

**Files:**
- Modify: `src/workline_plugins/smt_classifier/contract.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`

- [ ] **Step 1: 写失败测试**

Add assertions:

- `build_output_to_bin_params(...)` output includes `bin_cell_location`.
- `MOVE_FORWARD SUCCESS` allocated branch sends `PICK_AND_PUT` payload with `bin_cell_location`.
- Missing `bin_cell_location` in allocation should fail before command creation, preferably `PAYLOAD_INVALID` / `BLOCK`.

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_updates_bin_context_and_commands_output_arm -q
```

Expected:

- Fails because payload currently lacks `bin_cell_location`.

- [ ] **Step 2: Update command payload builder**

Modify `build_output_to_bin_params(...)`:

- Add `bin_cell_location`.
- Keep existing `barcode`, `reel_diameter`, `target_type`, `target_loc`, `bin_type`.
- Do not rename `target_loc` in this task; hardware/mock compatibility should be changed separately only if tests require it.

- [ ] **Step 3: Validate allocation before command**

In `handle_conveyor_success`, before command creation:

- Ensure `bin_id`, `bin_type`, `bin_cell_location` are non-empty strings.
- If missing, return `build_payload_invalid_block("料箱调度结果缺少 ...")`.

- [ ] **Step 4: 验证**

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py -q
rtk uv run ruff check src/workline_plugins/smt_classifier/contract.py src/workline_plugins/smt_classifier/plugin.py
```

Expected:

- SMT command result plugin tests pass.
- Ruff check passes.

- [ ] **Step 5: Commit**

Commit message:

```bash
git commit -m "feat(smt): send bin cell location to output arm"
```

## Task 4: 当前货架不可存时发起复合外部请求

**Files:**
- Modify: `src/workline_plugins/smt_classifier/plugin.py`
- Modify: `src/app/workline/domain/services/smt_rack_bin_scheduling_service.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
- Test: `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`

- [ ] **Step 1: 写失败测试**

Add plugin test:

- Session context has full `active_bin_rack` with no compatible DC/LC and no empty cell.
- `MOVE_FORWARD SUCCESS` returns `[UPDATE_CONTEXT, EXTERNAL_REQUEST]`.
- Context patch contains `rack_exchange.status="REQUESTED"` and current `pkg_id`.
- External request payload has both actions `MOVE_OUT_CURRENT_RACK` and `SUPPLY_EMPTY_RACK`.
- No `OUTPUT_ARM PICK_AND_PUT` command is produced.

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store -q
```

Expected:

- Fails because current test only covers `full_box_exchange_request` and not composite rack exchange/supply contract.

- [ ] **Step 2: Normalize new decision shape**

Modify `_normalize_bin_scheduling_decision(...)` in `plugin.py`:

- Accept new decision dataclass directly.
- Accept mapping with `kind="ALLOCATED"` and `bin_location`.
- Accept mapping with `kind="RACK_EXCHANGE_REQUIRED"` and `external_request`.
- Continue accepting existing `full_box_exchange_request` only as temporary compatibility during this branch.

- [ ] **Step 3: Update external request branch**

Modify `handle_conveyor_success`:

- If decision kind is `RACK_EXCHANGE_REQUIRED`, write `rack_exchange` context with `status`, `dispatch_key`, `target_code`, `reason_code`, `requested_actions`.
- Return one `ctx.next.external_request(...)`.
- Do not create output arm command in this branch.

- [ ] **Step 4: 验证 Runtime 组合规则不变**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_external_request_intent_creates_external_outbox_and_immediate_wait -q
rtk uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_external_request_before_terminal_intent_is_rejected_before_side_effects -q
```

Expected:

- Runtime still allows exactly one command-producing intent.
- No runtime behavior change is needed.

- [ ] **Step 5: Commit**

Commit message:

```bash
git commit -m "feat(smt): request rack exchange and supply when no bin slot fits"
```

## Task 5: 处理 WMS/RCS 外部回调并恢复粗分机出料

**Files:**
- Modify: `src/workline_plugins/smt_classifier/plugin.py`
- Modify: `src/workline_plugins/smt_classifier/contract.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`

- [ ] **Step 1: 写失败测试 - 进度回调**

Add test:

- Create EXTERNAL_HTTP inbox payload with `callback_type="WMS_RACK_EXCHANGE_PROGRESS"`, matching `dispatch_key`, status `IN_PROGRESS`.
- Session context has `rack_exchange.dispatch_key`.
- `plugin.on_external_http(...)` returns one `UPDATE_CONTEXT` intent and no command.
- Context patch updates `rack_exchange.status`.

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_external_rack_exchange_progress_keeps_waiting -q
```

Expected:

- Fails because `SmtClassifierPlugin` currently inherits default empty `on_external_http`.

- [ ] **Step 2: 写失败测试 - 空架到位恢复出料**

Add test:

- EXTERNAL_HTTP inbox payload has `callback_type="WMS_RACK_ARRIVED"`, matching `dispatch_key`, and `active_bin_rack` with at least one empty cell.
- Session context contains original `six_in_one`, `pkg_id`, `reel_diameter`, and pending `rack_exchange`.
- `plugin.on_external_http(...)` returns `[UPDATE_CONTEXT, COMMAND]`.
- The command targets `OUTPUT_ARM`, action `PICK_AND_PUT`, payload contains `bin_id`, `bin_type`, `bin_cell_location`.

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_external_rack_arrived_reallocates_and_commands_output_arm -q
```

Expected:

- Fails because no handler exists.

- [ ] **Step 3: 写失败测试 - 外部失败阻断**

Add test:

- EXTERNAL_HTTP inbox payload has `callback_type="WMS_RACK_EXCHANGE_FAILED"` and matching `dispatch_key`.
- `plugin.on_external_http(...)` returns `BLOCK` with reason from payload.

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_external_rack_exchange_failed_blocks_material -q
```

Expected:

- Fails because no handler exists.

- [ ] **Step 4: Implement minimal external callback parser**

In `contract.py`, add small constants or helper names for:

- `WMS_RACK_EXCHANGE_PROGRESS`
- `WMS_RACK_ARRIVED`
- `WMS_RACK_EXCHANGE_FAILED`

Do not create a large new protocol framework in this task.

- [ ] **Step 5: Implement `SmtClassifierPlugin.on_external_http(...)`**

Handler responsibilities:

- Read `inbox.payload_json`.
- Validate callback type.
- Validate `dispatch_key` against `session.context_json["rack_exchange"]["dispatch_key"]`.
- For progress: return `ctx.next.update_context(...)`.
- For rack arrived: write `active_bin_rack`, call `_allocate_bin(ctx, pkg_id)` using updated context, then return `UPDATE_CONTEXT + OUTPUT_ARM command`.
- For failed: return `ctx.next.block(...)`.

Implementation note:

- Because `_allocate_bin(...)` reads `ctx.session.context_json`, the handler may need a small helper that builds an allocation context from the current session context plus the just-arrived rack snapshot. Avoid mutating session directly in plugin code; return `UPDATE_CONTEXT` for persistence.

- [ ] **Step 6: 验证**

Run:

```bash
rtk uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py -q
rtk uv run pytest tests/workline_runtime/test_session_resolver.py tests/workline_runtime/test_inbox_service.py -q
rtk uv run ruff check src/workline_plugins/smt_classifier
```

Expected:

- SMT plugin command result tests pass.
- External callback resolver/inbox tests still pass.
- Ruff check passes.

- [ ] **Step 7: Commit**

Commit message:

```bash
git commit -m "feat(smt): resume output after rack supply callback"
```

## Task 6: 更新文档与完整验证

**Files:**
- Modify: `docs/business/workline_smt_classifier_runtime_flow.md`
- Optional Modify: `docs/business/wms_rcs_interface_requirements.md` only if callback payload names differ from existing documented names.

- [ ] **Step 1: 更新业务文档**

In `docs/business/workline_smt_classifier_runtime_flow.md`, add a concise section:

- `MOVE_FORWARD SUCCESS` 后先调度料箱格。
- 同 DC/LC 合并，不同 DC/LC 使用空格。
- 不满足时发起 `SMT_RACK_EXCHANGE_AND_SUPPLY` 外部请求。
- `WMS_RACK_ARRIVED` 回调后重新分配并下发 `OUTPUT_ARM`。
- 出料命令必须携带 `bin_cell_location`。

- [ ] **Step 2: 跑 focused test suite**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/integration/workline_plugins/test_smt_classifier_plugin_events.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py tests/workline_runtime/test_runtime_intent_contract.py tests/workline_runtime/test_plugin_next.py tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_inbox_service.py -q
```

Expected:

- All pass.

- [ ] **Step 3: 跑 lint/format**

Run:

```bash
rtk uv run ruff format src/app/workline/domain/services/smt_rack_bin_scheduling_service.py src/workline_runtime/services.py src/workline_plugins/smt_classifier tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/integration/workline_plugins/test_smt_classifier_plugin_events.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
rtk uv run ruff check src/app/workline/domain/services/smt_rack_bin_scheduling_service.py src/workline_runtime/services.py src/workline_plugins/smt_classifier tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/integration/workline_plugins/test_smt_classifier_plugin_events.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

Expected:

- Formatter completes.
- Ruff check passes.

- [ ] **Step 4: GitNexus 变更检测**

Run GitNexus detect changes for all staged/unstaged changes:

- `gitnexus_detect_changes(scope="all", repo="wes_backend")`

Expected:

- Affected flows match SMT classifier, runtime external request, and inbox idempotency.
- No unexpected API route or unrelated module impact.

- [ ] **Step 5: Commit**

Commit message:

```bash
git commit -m "docs(smt): document bin slot allocation and rack supply flow"
```

## Acceptance Criteria

- `SCAN_COMPLETED` OK 后 session context 保留完整 6 合 1。
- `SmtRackBinSchedulingService.plan_allocation(...)` 不再默认生成 md5 虚拟格位。
- 同 `DateCode/LotCode` 的物料优先合并到已有格。
- 不同 `DateCode/LotCode` 的物料使用空格。
- 当前到位货架无可合并格且无空格时，只发起外部请求，不下发 `OUTPUT_ARM`。
- 外部请求 payload 同时表达移出当前货架和补空料箱货架。
- `WMS_RACK_ARRIVED` 回调后，插件重新分配格位并通知粗分机继续出料。
- 出料命令 payload 包含 `bin_cell_location`。
- `WMS_RACK_EXCHANGE_FAILED` 进入阻断，不静默完成。
- 所有相关测试和 Ruff check 通过。

## 风险与控制

| 风险 | 控制 |
| --- | --- |
| 当前 Runtime 只允许一个 command-producing intent | 使用一个复合 `EXTERNAL_REQUEST`，不改 runtime 组合规则。 |
| 没有数据库资源模型 | v1 使用 `session.context_json.active_bin_rack` 作为执行快照；后续资源模型落地后替换调度服务输入来源。 |
| WMS/RCS 回调字段不稳定 | 回调必须校验 `dispatch_key` 和 `source_event_id`；重复由 inbox 幂等处理。 |
| 计划与整架满箱交换插件混淆 | 本计划不创建 `smt_full_box_exchange`，只改 `smt_classifier` 出料口。 |
| 插件直接写资源事实 | 插件只返回 RuntimeIntent，不直连数据库；资源事实投影留给后续资源模型。 |

## 自检结果

- Spec coverage: 覆盖物料到达粗分机出料口、请求分配、6 合 1 DC/LC 合并判断、空格分配、不可存时移架补架外部请求、空架到位回调继续工作。
- Placeholder scan: 本计划未使用待填占位；实现细节按项目规划文档规则留给编码阶段 TDD。
- Type consistency: 统一使用 `six_in_one`、`active_bin_rack`、`rack_exchange`、`bin_cell_location`、`RACK_EXCHANGE_REQUIRED`、`SMT_RACK_EXCHANGE_AND_SUPPLY`。
