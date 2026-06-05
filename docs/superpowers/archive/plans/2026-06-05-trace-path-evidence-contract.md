# Trace Path / Evidence 契约瘦身 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Trace Path API 改为 Path 专用轻量查询和轻量响应，消除 `evidence` 嵌套、`session` / `sessions` 重复，以及 `active_bin_rack` 扁平重复数据。

**Architecture:** Trace Detail 继续作为完整审计证据 API；Trace Path 使用专用查询，只加载路径页需要的 session、device path、timeline groups、diagnosis verdict 和 resource view。后端 `diagnosis_verdict_builder` 是唯一诊断主真源；`active_bin_rack` 通过纯 builder 从历史 trace payload 投影为 `rack -> bins -> cells`，不读取当前资源状态。

**Tech Stack:** FastAPI / Pydantic v2 / SQLAlchemy / pytest / Ruff；Vue 3 / TypeScript / Vitest / OpenAPI 类型生成 / Zod。

---

## 评审结论

- 完整同批实施：后端契约、后端 Path 查询、后端资源视图、前端类型、前端组件和测试同一变更批次完成。
- 不做向后兼容：删除字段不保留 alias、normalization fallback 或旧字段兜底。
- Path API 不再返回 `evidence`，也不再为了 Path 响应构建完整 `TraceDetailResponse`。
- Trace Detail 删除单数 `session`，只保留 `sessions`。
- Path 查询侧也要瘦身，不能只删除响应字段但仍加载完整 Detail 证据。
- 后端 `diagnosis_verdict_builder` 是诊断结论主真源；前端删除自算诊断主逻辑。
- `active_bin_rack` 层级视图必须是历史 payload 的纯投影，不能调用 `active_rack_snapshot_service` 读取当前资源状态。

## 现有事实

- `TraceDetailResponse` 当前同时包含 `session` 和 `sessions`：`src/app/workline/models/runtime.py`。
- `build_trace_response()` 当前同时填充 `session` 和 `sessions`：`src/app/workline/services/trace_response_builder.py`。
- `_build_trace_path()` 当前调用 `build_trace_response(result)`，并把完整 detail 放进 `RuntimeTracePathResponse.evidence`：`src/app/workline/services/runtime_query_service.py`。
- `TraceFocusPanel.vue` 当前依赖 `pathData.evidence` 展示 `TraceHealthPipeline` 和 raw timeline fallback。
- 前端 `runtime-diagnosis-verdict.ts` 当前从完整 `TraceDetailResponse` 推导诊断结论，并依赖 `detail.session` 计数。
- `active_rack_snapshot_service.py` 是“当前 active rack 恢复”服务，会读取当前 placement / occupancy / reservation；它不是历史 trace 展示投影。

## 目标接口契约

### TraceDetailResponse

用途：完整审计、排障、原始证据查看。

字段决策：

- 删除：`session`
- 保留：`sessions`
- 保留：`trace`、`summary`、`diagnosis_verdict`
- 保留 raw 证据：`callback_logs`、`inboxes`、`commands`、`outboxes`、`dispatch_attempts`、`timelines`、`diagnostics`、`resource_evidence`

### RuntimeTracePathResponse

用途：运行态路径页首屏、路径焦点面板、资源快照摘要。

字段决策：

- 删除：`evidence`
- 新增：`diagnosis_verdict`
- 新增：`sessions`
- 新增：`resource_view`
- 保留：`workline_id`、`session_id`、`trace_id`、`devices`、`timeline_groups`、`current_blocking_device_id`、`blocking_reason`

### RuntimeTraceResourceView

新增只读视图：

- `active_bin_racks: list[RuntimeActiveBinRackView]`
- `RuntimeActiveBinRackView`: `rack_id`、`rack_code`、`rack_kind`、`rack_type`、`bins`
- `RuntimeActiveBinRackBinView`: `rack_slot_code`、`rack_slot_location_code`、`bin_id`、`bin_code`、`bin_type`、`bin_orientation_code`、`cells`
- `RuntimeActiveBinRackCellView`: `bin_cell_index`、`bin_cell_code`、`bin_cell_location`、`status`、`capacity_depth_mm`、`used_depth_mm`、`material_identity_key`、`pkg_code`、`is_reserved`

归一化规则：

- rack key 优先 `rack_code`，缺失时使用 `rack_id`。
- 缺 rack key 的 payload 不生成 rack view。
- bin key 使用 `(rack_slot_code, bin_code)`；缺字段时用已有非空字段兜底。
- cell key 使用 `(bin_code, bin_cell_index)`；缺 cell key 的 cell 不进入结果。
- 同一 cell key 重复时，后出现的非空字段覆盖前值。
- 输入可来自扁平 `active_bin_rack.cells` 或层级 `active_bin_rack.bins[].cells[]`。
- 输出只包含 Path 页面需要的层级视图，不返回原始 `active_bin_rack` 大 payload。

## 数据流

```text
Trace Path request
  -> RuntimeQueryService.get_{session|trace}_path()
  -> TraceQueryService.query_path(...)
       loads only Path facts
  -> RuntimeQueryService._build_trace_path(path_result)
       -> sessions projection
       -> diagnosis_verdict_builder.build(path_result)
       -> trace_resource_view_builder.build_trace_resource_view(path_result)
       -> timeline_groups via build_trace_timeline_item()
  -> RuntimeTracePathResponse without evidence
```

```text
Trace Detail request
  -> TraceQueryService.query(...)
       loads full audit facts
  -> build_trace_response(result)
       -> TraceDetailResponse without session
       -> full raw evidence remains available
```

## 变更范围

### 后端

- 修改 `src/app/workline/models/runtime.py`
  - 删除 `TraceDetailResponse.session`
  - 新增 Path 资源视图模型
  - 更新 `RuntimeTracePathResponse`
- 修改 `src/app/workline/services/trace_response_builder.py`
  - `build_trace_response()` 停止填充单数 `session`
  - 将 session item 投影函数改为公共可复用函数，供 Path 响应复用
- 修改 `src/app/workline/services/trace_query_service.py`
  - 新增 Path 专用查询入口
  - Path 查询不加载 `dispatch_attempts`、完整 `resource_evidence` 和不需要的大 raw evidence
  - 保留诊断所需的 session、commands、inboxes、outboxes、timelines、callback 摘要和 WorkLine admission 投影
- 修改 `src/app/workline/services/runtime_query_service.py`
  - `get_session_path()` / `get_trace_path()` 改用 Path 专用查询
  - `_build_trace_path()` 停止调用 `build_trace_response(result)`
  - `_build_trace_path()` 返回 `sessions`、`diagnosis_verdict`、`resource_view`
- 新增 `src/app/workline/services/trace_resource_view_builder.py`
  - 只做历史 payload 到 Path resource view 的纯投影
  - 不访问数据库
  - 不调用当前资源状态服务
- 修改测试：
  - `tests/workline_runtime/test_trace_query_service.py`
  - `tests/api/test_workline_runtime_api.py`

### 前端

- 修改 `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/runtime.ts`
  - 删除 `TraceDetailResponse.session`
  - 删除 `RuntimeTracePathResponse.evidence`
  - 新增 Path resource view 类型
- 更新生成文件：
  - `src/api/generated/openapi-types.ts`
  - `src/api/generated/openapi-metadata/*`
  - `src/types/generated/zod-schemas.ts`
- 修改运行态组件和工具：
  - `src/components/runtime/trace/TraceTopologySummary.vue`
  - `src/components/runtime/trace/TraceFocusPanel.vue`
  - `src/components/runtime/trace/TraceBlockingPointCard.vue`
  - `src/utils/runtime-trace-topology.ts`
  - `src/utils/runtime-diagnosis-verdict.ts`
  - `src/views/runtime/traces/TraceExplorerPage.vue`
- 修改前端测试：
  - `tests/unit/components/runtime/traceTopologySummary.test.ts`
  - `tests/unit/components/runtime/traceFocusPanelTimelineGroups.test.ts`
  - `tests/unit/views/runtime/traceExplorerLayout.test.ts`
  - `tests/unit/utils/runtime-trace-topology.test.ts`
  - `tests/unit/utils/runtime-diagnosis-verdict.test.ts`

## Task 0: 实施前清理与影响分析

**Files:**
- Inspect: backend and frontend git status
- Inspect: GitNexus impact

- [ ] **Step 1: 确认后端工作区**

  Run:

  ```bash
  git status --short
  ```

  Expected: only plan/spec docs are present before implementation. Do not revert unrelated user files.

- [ ] **Step 2: 确认前端工作区**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_frontend
  git status --short
  ```

  Expected: record existing changes before implementation. Do not overwrite unrelated user work.

- [ ] **Step 3: 运行 GitNexus impact**

  Run from backend:

  ```bash
  npx gitnexus impact TraceDetailResponse --direction upstream
  npx gitnexus impact RuntimeTracePathResponse --direction upstream
  npx gitnexus impact TraceQueryService --direction upstream
  npx gitnexus impact RuntimeQueryService --direction upstream
  npx gitnexus impact build_trace_response --direction upstream
  ```

  Expected: no HIGH/CRITICAL risk without user confirmation. If GitNexus cannot resolve a symbol, record `UNKNOWN` and continue with grep evidence.

## Task 1: Trace Detail 删除单数 session

**Files:**
- Modify: `src/app/workline/models/runtime.py`
- Modify: `src/app/workline/services/trace_response_builder.py`
- Test: `tests/workline_runtime/test_trace_query_service.py`
- Test: `tests/api/test_workline_runtime_api.py`

- [ ] **Step 1: 写失败测试**

  Add tests asserting:

  - `TraceDetailResponse.model_fields` has no `session`
  - `build_trace_response(result).model_dump(mode="json")` has no `session`
  - `sessions[0]` remains populated for the primary session

- [ ] **Step 2: 运行失败测试**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_trace_query_service.py::test_trace_detail_response_uses_sessions_without_duplicate_session_field -q
  ```

  Expected: FAIL because current response still contains `session`.

- [ ] **Step 3: 更新模型和 builder**

  Change:

  - remove `TraceDetailResponse.session`
  - remove `session=_build_session_item(session)` from `build_trace_response()`
  - expose or rename `_build_session_item()` so Runtime Path response can reuse the same projection without duplication

- [ ] **Step 4: 验证测试通过**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_trace_query_service.py::test_trace_detail_response_uses_sessions_without_duplicate_session_field -q
  ```

  Expected: PASS.

## Task 2: 后端 Path 专用查询

**Files:**
- Modify: `src/app/workline/services/trace_query_service.py`
- Test: `tests/workline_runtime/test_trace_query_service.py`

- [ ] **Step 1: 写失败测试**

  Add tests asserting Path query:

  - resolves by `trace_id`
  - resolves by `session_id`
  - returns session / sessions / commands / inboxes / outboxes / timelines needed by Path
  - does not load `dispatch_attempts`
  - does not load full `resource_evidence`

- [ ] **Step 2: 运行失败测试**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_trace_query_service.py -k "path_query" -q
  ```

  Expected: FAIL because no Path-specific query exists yet.

- [ ] **Step 3: 新增 Path 查询入口**

  Implement a Path-specific query in `TraceQueryService`.

  Required behavior:

  - keep full `query()` unchanged for Trace Detail
  - add separate methods for Path, such as `path_by_trace_id()` and `path_by_session_id()`
  - return the existing `TraceQueryResult` shape or a lightweight dataclass compatible with `diagnosis_verdict_builder`
  - load only Path-required facts
  - preserve WorkLine admission projection needed by diagnosis verdict

- [ ] **Step 4: 验证测试通过**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_trace_query_service.py -k "path_query" -q
  ```

  Expected: PASS.

## Task 3: Path 响应删除 evidence 并返回专用事实

**Files:**
- Modify: `src/app/workline/models/runtime.py`
- Modify: `src/app/workline/services/runtime_query_service.py`
- Test: `tests/api/test_workline_runtime_api.py`

- [ ] **Step 1: 写失败测试**

  Add tests asserting:

  - `RuntimeTracePathResponse.model_fields` has no `evidence`
  - `_build_trace_path()` result has `diagnosis_verdict`
  - `_build_trace_path()` result has `sessions`
  - `_build_trace_path()` result has `resource_view`
  - `_build_trace_path()` does not call `build_trace_response()`

- [ ] **Step 2: 运行失败测试**

  Run:

  ```bash
  uv run pytest tests/api/test_workline_runtime_api.py::TestRuntimeQueryService::test_build_trace_path_returns_slim_contract_without_evidence -q
  ```

  Expected: FAIL because current Path response still exposes `evidence`.

- [ ] **Step 3: 更新 RuntimeTracePathResponse**

  Change model fields:

  - remove `evidence`
  - add `diagnosis_verdict: DiagnosisVerdictResponse`
  - add `sessions: list[TraceSessionItem]`
  - add `resource_view: RuntimeTraceResourceView`

- [ ] **Step 4: 更新 RuntimeQueryService**

  Required behavior:

  - `get_session_path()` calls Path-specific session query
  - `get_trace_path()` calls Path-specific trace query
  - `_build_trace_path()` no longer imports or calls `build_trace_response`
  - `_build_trace_path()` calls backend diagnosis builder for `diagnosis_verdict`
  - `_build_trace_path()` uses the shared session projection function for `sessions`

- [ ] **Step 5: 验证测试通过**

  Run:

  ```bash
  uv run pytest tests/api/test_workline_runtime_api.py::TestRuntimeQueryService::test_build_trace_path_returns_slim_contract_without_evidence -q
  ```

  Expected: PASS.

## Task 4: active_bin_rack 纯资源视图 builder

**Files:**
- Create: `src/app/workline/services/trace_resource_view_builder.py`
- Modify: `src/app/workline/models/runtime.py`
- Modify: `src/app/workline/services/runtime_query_service.py`
- Test: `tests/api/test_workline_runtime_api.py`

- [ ] **Step 1: 写失败测试**

  Add tests asserting:

  - flat `active_bin_rack.cells` becomes one rack with bins and cells
  - nested `active_bin_rack.bins[].cells[]` outputs the same view shape
  - duplicate cell keys are merged once
  - later non-empty fields overwrite earlier empty fields
  - payload without rack key produces no rack view
  - builder does not call `active_rack_snapshot_service`

- [ ] **Step 2: 运行失败测试**

  Run:

  ```bash
  uv run pytest tests/api/test_workline_runtime_api.py -k "active_bin_rack or trace_path_resource_view" -q
  ```

  Expected: FAIL because the new resource view builder does not exist yet.

- [ ] **Step 3: 新增模型**

  Add:

  - `RuntimeTraceResourceView`
  - `RuntimeActiveBinRackView`
  - `RuntimeActiveBinRackBinView`
  - `RuntimeActiveBinRackCellView`

- [ ] **Step 4: 新增纯 builder**

  Builder requirements:

  - no database access
  - no repository access
  - no current-state service calls
  - accepts TraceQueryResult-like input
  - collects historical snapshots from sessions, inboxes, outboxes and timelines
  - outputs only normalized view fields

- [ ] **Step 5: 接入 Path 响应**

  `_build_trace_path()` calls the pure builder and assigns `resource_view`.

- [ ] **Step 6: 验证测试通过**

  Run:

  ```bash
  uv run pytest tests/api/test_workline_runtime_api.py -k "active_bin_rack or trace_path_resource_view" -q
  ```

  Expected: PASS.

## Task 5: 后端诊断主真源收口

**Files:**
- Modify: `src/app/workline/services/runtime_query_service.py`
- Verify: `src/app/workline/services/diagnosis_verdict_builder.py`
- Test: `tests/api/test_workline_runtime_api.py`
- Test: `tests/workline_runtime/test_trace_query_service.py`

- [ ] **Step 1: 写失败测试**

  Add tests asserting:

  - Path response `diagnosis_verdict` equals backend builder output for the same Path query result
  - completed, waiting, failed and blocked examples still produce expected verdict states
  - evidence health session count uses backend result session / sessions, not frontend fallback

- [ ] **Step 2: 运行失败测试**

  Run:

  ```bash
  uv run pytest tests/api/test_workline_runtime_api.py tests/workline_runtime/test_trace_query_service.py -k "diagnosis_verdict" -q
  ```

  Expected: FAIL until Path response exposes backend verdict.

- [ ] **Step 3: 接入统一 verdict**

  Required behavior:

  - Trace Detail continues to use `diagnosis_verdict_builder.build(result)`
  - Trace Path uses the same builder
  - no frontend-derived diagnosis is required for normal runtime pages

- [ ] **Step 4: 验证测试通过**

  Run:

  ```bash
  uv run pytest tests/api/test_workline_runtime_api.py tests/workline_runtime/test_trace_query_service.py -k "diagnosis_verdict" -q
  ```

  Expected: PASS.

## Task 6: 后端契约和回归验证

**Files:**
- Test: backend runtime trace tests
- Output: OpenAPI schema for frontend generation

- [ ] **Step 1: 运行后端目标测试**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_trace_query_service.py tests/api/test_workline_runtime_api.py -q
  ```

  Expected: PASS.

- [ ] **Step 2: 运行 Ruff 检查**

  Run:

  ```bash
  uv run ruff check src/app/workline/models/runtime.py src/app/workline/services/trace_query_service.py src/app/workline/services/runtime_query_service.py src/app/workline/services/trace_response_builder.py src/app/workline/services/trace_resource_view_builder.py tests/workline_runtime/test_trace_query_service.py tests/api/test_workline_runtime_api.py
  ```

  Expected: PASS.

- [ ] **Step 3: 检查 OpenAPI 契约**

  Confirm:

  - `TraceDetailResponse` has no `session`
  - `RuntimeTracePathResponse` has no `evidence`
  - `RuntimeTracePathResponse` has `diagnosis_verdict`, `sessions`, `resource_view`

## Task 7: 前端类型与生成文件同步

**Files:**
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/runtime.ts`
- Generate: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-types.ts`
- Generate: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/generated/openapi-metadata/*`
- Generate: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/generated/zod-schemas.ts`

- [ ] **Step 1: 运行类型生成**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_frontend
  pnpm generate:types
  pnpm generate:zod
  ```

  Expected: generated schemas reflect backend OpenAPI.

- [ ] **Step 2: 同步手写 runtime 类型**

  Change:

  - remove `TraceDetailResponse.session`
  - remove `RuntimeTracePathResponse.evidence`
  - add `diagnosis_verdict`, `sessions`, `resource_view`
  - add rack/bin/cell view types

- [ ] **Step 3: 运行类型检查**

  Run:

  ```bash
  pnpm type:check
  ```

  Expected: FAIL before frontend old references are removed; failures should point to `session`, `pathData.evidence`, or frontend diagnosis derivation.

## Task 8: 前端删除旧字段和诊断推导

**Files:**
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/components/runtime/trace/TraceTopologySummary.vue`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/components/runtime/trace/TraceFocusPanel.vue`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/components/runtime/trace/TraceBlockingPointCard.vue`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/utils/runtime-trace-topology.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/utils/runtime-diagnosis-verdict.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/src/views/runtime/traces/TraceExplorerPage.vue`

- [ ] **Step 1: 删除 `detail.session` 依赖**

  Required behavior:

  - primary session comes from `detail.sessions[0]`
  - route/session normalization uses `sessions[0]`
  - fixtures no longer include `session`

- [ ] **Step 2: 删除 `pathData.evidence` 依赖**

  Required behavior:

  - `TraceFocusPanel` no longer renders `TraceHealthPipeline` from Path evidence
  - raw timeline fallback is removed
  - empty `timeline_groups` shows an empty state
  - resource snapshot reads only `pathData.resource_view.active_bin_racks`

- [ ] **Step 3: 删除前端诊断主推导**

  Required behavior:

  - runtime pages display backend `diagnosis_verdict`
  - no normal page recomputes verdict from raw Trace Detail evidence
  - no compatibility fallback for missing backend verdict

- [ ] **Step 4: 整理 TraceExplorerPage**

  Required behavior:

  - path mock/normalization no longer handles `evidence`
  - detail normalization no longer reconstructs `sessions` from `session`
  - Path view does not automatically load Trace Detail only to recover raw payload

- [ ] **Step 5: 类型检查**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_frontend
  pnpm type:check
  ```

  Expected: PASS.

## Task 9: 前端测试更新

**Files:**
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/components/runtime/traceTopologySummary.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/components/runtime/traceFocusPanelTimelineGroups.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/views/runtime/traceExplorerLayout.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/utils/runtime-trace-topology.test.ts`
- Modify: `/Users/kaizhou/SynologyDrive/works/wes_frontend/tests/unit/utils/runtime-diagnosis-verdict.test.ts`

- [ ] **Step 1: 更新 fixtures**

  Required fixture changes:

  - Trace Detail fixtures remove `session`
  - Trace Detail fixtures populate `sessions[0]`
  - Path fixtures remove `evidence`
  - Path fixtures add `sessions`, `diagnosis_verdict`, `resource_view`

- [ ] **Step 2: 添加路径页测试**

  Required assertions:

  - `TraceFocusPanel` renders timeline groups without `evidence`
  - `TraceFocusPanel` shows empty state when `timeline_groups=[]`
  - `TraceFocusPanel` does not access raw `pathData.evidence.timelines`
  - `TraceFocusPanel` renders resource view from `resource_view.active_bin_racks`

- [ ] **Step 3: 添加 Detail / topology 测试**

  Required assertions:

  - `TraceTopologySummary` displays session code / barcode / business key from `sessions[0]`
  - `runtime-trace-topology` uses `sessions[0]` and no `detail.session`
  - diagnosis UI uses backend verdict object

- [ ] **Step 4: 运行目标测试**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_frontend
  pnpm test tests/unit/components/runtime/traceTopologySummary.test.ts tests/unit/components/runtime/traceFocusPanelTimelineGroups.test.ts tests/unit/views/runtime/traceExplorerLayout.test.ts tests/unit/utils/runtime-trace-topology.test.ts tests/unit/utils/runtime-diagnosis-verdict.test.ts
  ```

  Expected: PASS.

## Task 10: 跨仓库验证

**Files:**
- Backend and frontend changed files from prior tasks

- [ ] **Step 1: 后端验证**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_backend
  uv run pytest tests/workline_runtime/test_trace_query_service.py tests/api/test_workline_runtime_api.py -q
  uv run ruff check src/app/workline/models/runtime.py src/app/workline/services/trace_query_service.py src/app/workline/services/runtime_query_service.py src/app/workline/services/trace_response_builder.py src/app/workline/services/trace_resource_view_builder.py tests/workline_runtime/test_trace_query_service.py tests/api/test_workline_runtime_api.py
  ```

  Expected: PASS.

- [ ] **Step 2: 前端验证**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_frontend
  pnpm type:check
  pnpm test tests/unit/components/runtime/traceTopologySummary.test.ts tests/unit/components/runtime/traceFocusPanelTimelineGroups.test.ts tests/unit/views/runtime/traceExplorerLayout.test.ts tests/unit/utils/runtime-trace-topology.test.ts tests/unit/utils/runtime-diagnosis-verdict.test.ts
  pnpm contract:verify
  ```

  Expected: PASS.

- [ ] **Step 3: 本地 API spot check**

  With backend service running, confirm:

  - Path response has no `evidence`
  - Path response has `diagnosis_verdict`
  - Path response has `sessions`
  - Path response has `resource_view.active_bin_racks`
  - Detail response has no `session`

## Task 11: 提交前变更检测

**Files:**
- All changed backend files
- All changed frontend files

- [ ] **Step 1: 后端 GitNexus detect changes**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_backend
  npx gitnexus detect-changes
  ```

  Expected: changes only affect Trace Detail / Trace Path contract, Path query, Path resource view and related tests.

- [ ] **Step 2: 前端 diff 检查**

  Run:

  ```bash
  cd /Users/kaizhou/SynologyDrive/works/wes_frontend
  git status --short
  git diff --stat
  ```

  Expected: changes only affect runtime trace types, generated contract files, trace UI components and related tests.

## 验收标准

- Path API 响应体不再出现 `evidence`。
- Path API 查询侧不再加载完整 Trace Detail-only evidence。
- Trace Detail 响应体不再出现单数 `session`。
- `sessions` 是唯一会话列表字段。
- Path API 返回后端统一 `diagnosis_verdict`。
- 前端不再用 raw evidence 自行推导运行态诊断主结论。
- Path API 的 `active_bin_rack` 展示数据是 `rack -> bins -> cells` 层级结构。
- `resource_view` 只来自历史 trace payload，不读取当前 active rack 状态。
- 前端路径页不再依赖 `pathData.evidence`。
- 后端目标测试、前端目标测试、类型检查和 contract 校验通过。

## 不在本批范围

- 不压缩 Trace Detail API 的 raw evidence。
- 不做 timeline `payload_json` redaction；如果 Path payload 仍过大，后续单独处理。
- 不改数据库 schema。
- 不改 `active_rack_snapshot_service` 的当前状态恢复逻辑。
- 不保留旧字段兼容层。
- 不做 UI 视觉重设计，只做契约同步和必要展示调整。

## 风险与注意事项

- `TraceQueryResult.session` 可继续作为服务内部 pivot；本计划只删除 API 响应字段。
- Path 专用查询必须满足 `diagnosis_verdict_builder` 所需输入，否则 verdict 会退化为 unknown。
- `active_bin_rack` 的历史 payload 可能来自多个位置；builder 必须对缺字段、重复 cell、空 payload 做稳定处理。
- 前端生成类型和手写 `src/types/runtime.ts` 必须一起更新。
- 本计划跨两个仓库，提交可拆为后端提交和前端提交，但评审必须按同一变更批次处理。
