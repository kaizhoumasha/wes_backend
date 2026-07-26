# 测试套件瘦身 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低测试套件维护成本，把散落、超大、mock 绑定过深的测试整理成可定位、可分层、可渐进收敛的结构。

**Architecture:** 本计划不先删测试，先建立测试目录契约和 guardrail，再做文件搬迁、超大文件拆分、共享 fixture 收敛。每个任务都保持行为等价，使用 `pytest --collect-only` 和定向测试证明收集项稳定，再逐步收紧允许的历史债务清单。

**Tech Stack:** Python 3.13, pytest, pytest-asyncio, Ruff, GitNexus, RTK, FastAPI/SQLModel 测试基础设施。

---

## 当前基线

当前 `develop` 基线来自 2026-06-27 本地扫描：

- `tests/` 下 `258` 个 `test_*.py`
- 测试代码约 `94133` 行
- pytest 默认收集 `2703` 个测试
- AST 识别 `2589` 个 `test_*` 函数，其中 `1376` 个 async 测试
- 本地 fixture `75` 个，分布在 `24` 个测试文件
- `100` 个测试文件使用 `patch`，`62` 个使用 `monkeypatch`，`73` 个使用 mock 类
- 最大文件：
  - `tests/workline_runtime/test_runtime_intent_effects.py`: `4636` 行，`91` 个测试
  - `tests/api/test_workline_runtime_api.py`: `4363` 行，`82` 个测试
  - `tests/rack/test_rack_operation_service.py`: `2898` 行，`62` 个测试
  - `tests/api/test_callback_api.py`: `2785` 行，`45` 个测试
  - `tests/resource/test_resource_projection_service.py`: `2444` 行，`44` 个测试

关键判断：

- 测试数量不是主要问题，维护负担才是主要问题。
- 第一阶段不删除业务覆盖，只做分层、搬迁、拆文件和 fixture 收敛。
- 所有命令使用 `rtk ...` 和 `uv run ...`，不依赖已激活 shell 环境。
- 修改任何测试类、fixture 函数或 helper 函数前，按项目规则运行 GitNexus impact analysis；若 HIGH/CRITICAL，先停下汇报。
- Commit 前必须运行 `rtk gitnexus detect-changes`。

## 文件结构

### 新增文件

- `tests/support/test_suite_topology.py`
  - 测试套件结构扫描工具。
  - 维护当前允许存在的历史债务清单，例如超大测试文件、根目录领域测试文件。
  - 只服务测试 guardrail，不被业务代码 import。

- `tests/architecture/test_test_suite_topology_guardrail.py`
  - 固化测试目录治理规则。
  - 防止新测试继续散落到根目录。
  - 防止新增超大测试文件。
  - 校验 `pyproject.toml` 默认排除目录和 `tests/README.md` 说明一致。

- `tests/workline_runtime/README.md`
  - 说明 WorkLine runtime 测试如何放置。
  - 明确 API facade、service 单元、runtime intent effect、contract、integration 的边界。

- `tests/workline_runtime/support/runtime_intent_effects.py`
  - 从 `test_runtime_intent_effects.py` 抽出共享 builder 和 recording fake。
  - 只放测试支撑对象，不放断言。

- `tests/workline_runtime/test_runtime_intent_material_unit_effects.py`
  - 放 material unit create/update/status transition 相关 RuntimeIntentEffect 测试。

- `tests/workline_runtime/test_runtime_intent_completion_effects.py`
  - 放 complete、terminal ledger、source/target/ng place 完成态相关 RuntimeIntentEffect 测试。

- `tests/workline_runtime/test_runtime_intent_resource_effects.py`
  - 放 resource fact、resource reservation、resource wait 相关 RuntimeIntentEffect 测试。

- `tests/workline_runtime/test_runtime_intent_command_effects.py`
  - 放 command destination、device command outbox、command timeout 相关 RuntimeIntentEffect 测试。

- `tests/workline_runtime/test_runtime_intent_external_operation_effects.py`
  - 放 external request、rack operation、bin operation、device event inbox 相关 RuntimeIntentEffect 测试。

- `tests/workline_runtime/test_runtime_query_projection_service.py`
  - 从 API 文件中迁出 RuntimeQueryService projection/builder 类测试。

- `tests/device/`
  - 接收当前根目录中的 device service/model/runtime state 测试。

- `tests/admin/`
  - 接收当前根目录中的 menu/user/permission 相关 admin 测试。

### 修改文件

- `tests/README.md`
  - 增加目录归属矩阵、默认快速回归边界、重测试入口、测试文件大小约束。

- `pyproject.toml`
  - 若需要新增 pytest marker 或收紧默认排除目录，改动集中在 `[tool.pytest.ini_options]`。
  - 当前 `norecursedirs` 对 `tests/e2e`、`tests/resilience`、`tests/load`、`tests/mock` 已生效，默认不改。

- `tests/api/test_workline_runtime_api.py`
  - 保留 API route permission、API facade 和 response model contract。
  - 移出 `TestRuntimeQueryService` 类。

- `tests/workline_runtime/test_runtime_query_service.py`
  - 保留 TraceQueryService 聚合查询测试。
  - 不再承接 API facade 测试。

- `tests/workline_runtime/test_runtime_intent_effects.py`
  - 拆分后只保留 orchestrator write-back bridge 或删除空壳文件。
  - 若保留，文件目标不超过 500 行。

- `tests/mock/test_data_generator.py`
  - 将 `TestDataScenario` 改名为 `DataScenario`，消除显式运行 `tests/mock` 时的 pytest collection warning。

---

## Task 1: 建立测试套件拓扑 guardrail

**Files:**
- Create: `tests/support/test_suite_topology.py`
- Create: `tests/architecture/test_test_suite_topology_guardrail.py`
- Modify: `tests/README.md`
- Test: `tests/architecture/test_test_suite_topology_guardrail.py`

- [ ] **Step 1: 写入当前基线扫描工具**

在 `tests/support/test_suite_topology.py` 中定义测试拓扑扫描工具。必须包含这些稳定接口：

```python
MAX_TEST_FILE_LINES = 3000
DEFAULT_EXCLUDED_TEST_DIRS = {"tests/e2e", "tests/resilience", "tests/load", "tests/mock"}
def iter_test_files() -> list[Path]: ...
def line_count(path: Path) -> int: ...
def root_level_test_files() -> list[Path]: ...
def test_files_over_line_limit() -> list[Path]: ...
```

初始允许清单必须显式列出当前历史债务：

- `tests/workline_runtime/test_runtime_intent_effects.py`
- `tests/api/test_workline_runtime_api.py`

不要把 `tests/rack/test_rack_operation_service.py` 加入超大清单，因为它当前低于 `3000` 行，可以作为第二阶段治理对象。

- [ ] **Step 2: 写入 guardrail 测试**

在 `tests/architecture/test_test_suite_topology_guardrail.py` 中覆盖四条规则：

- 根目录测试文件只能出现在允许清单中。
- 超过 `3000` 行的测试文件只能出现在允许清单中。
- `pyproject.toml` 的 `norecursedirs` 包含 `tests/e2e`、`tests/resilience`、`tests/load`、`tests/mock`。
- `tests/README.md` 必须说明默认快速回归不包含这四个重测试目录。

- [ ] **Step 3: 更新测试指南**

修改 `tests/README.md`，在“默认快速回归集”后增加“目录归属矩阵”：

| 目录 | 放置内容 |
| --- | --- |
| `tests/api/` | FastAPI route、permission、response model、API facade 测试 |
| `tests/workline_runtime/` | runtime service、orchestrator、intent、diagnostic、session resolver 纯逻辑测试 |
| `tests/workline_plugins/` | plugin contract、plugin behavior、template asset 测试 |
| `tests/contracts/` | 跨系统/跨模块契约测试 |
| `tests/integration/` | 多组件但不依赖人工操作的集成测试 |
| `tests/e2e/` | 显式运行的端到端测试 |
| `tests/resilience/` | 降级、断连、恢复类测试 |
| `tests/mock/` | mock server 和模拟器测试，默认不收集 |

- [ ] **Step 4: 验证 guardrail 通过**

Run:

```bash
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: 验证默认 collect 不变**

Run:

```bash
rtk uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected:

```text
2703 tests collected
```

如果 collect 数量不同，先确认是否当前分支已有测试增删；只要没有 collection error，记录新数量到本计划执行笔记。

- [ ] **Step 6: Commit**

Run:

```bash
rtk gitnexus detect-changes
git add tests/support/test_suite_topology.py tests/architecture/test_test_suite_topology_guardrail.py tests/README.md
git commit -m "test: 增加测试套件拓扑治理门禁"
```

Expected:

- GitNexus 变更范围只包含测试支撑和测试文档。
- commit 成功。

---

## Task 2: 清理根目录领域测试

**Files:**
- Create: `tests/device/`
- Modify: `tests/support/test_suite_topology.py`
- Move:
  - `tests/test_workline_service_plugin_validation.py` → `tests/workline_runtime/test_workline_service_plugin_validation.py`
  - `tests/test_workline_routes.py` → `tests/api/test_workline_routes.py`
  - `tests/test_device_command_service_contract.py` → `tests/contracts/device/test_device_command_service_contract.py`
  - `tests/test_device_context_service.py` → `tests/device/test_device_context_service.py`
  - `tests/test_device_service_runtime_state.py` → `tests/device/test_device_service_runtime_state.py`
  - `tests/test_frontend_menu_parser.py` → `tests/admin/test_frontend_menu_parser.py`
  - `tests/test_menu_repository_superuser_filter.py` → `tests/admin/test_menu_repository_superuser_filter.py`
  - `tests/test_menu_service_tree.py` → `tests/admin/test_menu_service_tree.py`
  - `tests/test_user_api_routes.py` → `tests/admin/test_user_api_routes.py`
  - `tests/test_user_model.py` → `tests/admin/test_user_model.py`
  - `tests/test_user_service_assign_roles.py` → `tests/admin/test_user_service_assign_roles.py`

- [ ] **Step 1: 移动 WorkLine 根目录测试**

Run:

```bash
git mv tests/test_workline_service_plugin_validation.py tests/workline_runtime/test_workline_service_plugin_validation.py
git mv tests/test_workline_routes.py tests/api/test_workline_routes.py
```

Expected:

- pytest 文件名仍为 `test_*.py`。
- 不修改测试函数内容。

- [ ] **Step 2: 验证 WorkLine 搬迁**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_workline_service_plugin_validation.py \
  tests/api/test_workline_routes.py \
  -q
```

Expected:

```text
65 passed
```

如果测试数量因当前分支变化不同，必须确认没有 import error、collection error 或 skipped 异常增长。

- [ ] **Step 3: 移动 Device 测试**

Run:

```bash
mkdir -p tests/device
git mv tests/test_device_context_service.py tests/device/test_device_context_service.py
git mv tests/test_device_service_runtime_state.py tests/device/test_device_service_runtime_state.py
git mv tests/test_device_command_service_contract.py tests/contracts/device/test_device_command_service_contract.py
```

Expected:

- `tests/device/` 下只包含 device service/model/runtime state 测试。
- device command 跨 ECS 契约测试归入 `tests/contracts/device/`。

- [ ] **Step 4: 验证 Device 搬迁**

Run:

```bash
rtk uv run pytest \
  tests/device/test_device_context_service.py \
  tests/device/test_device_service_runtime_state.py \
  tests/contracts/device/test_device_command_service_contract.py \
  -q
```

Expected:

```text
33 passed
```

- [ ] **Step 5: 移动 Admin/Menu/User 测试**

Run:

```bash
mkdir -p tests/admin
git mv tests/test_frontend_menu_parser.py tests/admin/test_frontend_menu_parser.py
git mv tests/test_menu_repository_superuser_filter.py tests/admin/test_menu_repository_superuser_filter.py
git mv tests/test_menu_service_tree.py tests/admin/test_menu_service_tree.py
git mv tests/test_user_api_routes.py tests/admin/test_user_api_routes.py
git mv tests/test_user_model.py tests/admin/test_user_model.py
git mv tests/test_user_service_assign_roles.py tests/admin/test_user_service_assign_roles.py
```

Expected:

- admin 领域测试不再散落在 `tests/` 根目录。
- 文件内部 import 不需要变化。

- [ ] **Step 6: 验证 Admin 搬迁**

Run:

```bash
rtk uv run pytest tests/admin -q
```

Expected:

```text
46 passed
```

- [ ] **Step 7: 收紧根目录允许清单**

修改 `tests/support/test_suite_topology.py`，从根目录允许清单中移除本任务已移动的 11 个文件。

允许暂留根目录的文件只保留基础框架、core/common、全局配置类测试，例如：

- `tests/test_base_api.py`
- `tests/test_base_repository_crud.py`
- `tests/test_base_repository_error_handling.py`
- `tests/test_base_repository_hooks.py`
- `tests/test_base_service_cache.py`
- `tests/test_exceptions.py`
- `tests/test_timezone.py`

- [ ] **Step 8: 验证拓扑和默认 collect**

Run:

```bash
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
rtk uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected:

- guardrail 通过。
- 默认 collect 无错误。
- collected 数量与任务开始前一致，除非当前分支已有测试增删。

- [ ] **Step 9: Commit**

Run:

```bash
rtk gitnexus detect-changes
git add tests
git commit -m "test: 归位根目录领域测试"
```

Expected:

- diff 主要是 rename。
- GitNexus 未报告业务源码符号变更。

---

## Task 3: 拆分 `test_workline_runtime_api.py`

**Files:**
- Modify: `tests/api/test_workline_runtime_api.py`
- Create: `tests/workline_runtime/test_runtime_query_projection_service.py`
- Modify: `tests/support/test_suite_topology.py`
- Test:
  - `tests/api/test_workline_runtime_api.py`
  - `tests/workline_runtime/test_runtime_query_projection_service.py`

- [ ] **Step 1: 运行影响分析**

Run:

```bash
rtk gitnexus impact TestRuntimeQueryService --direction upstream
```

Expected:

- 若 risk 为 LOW/MEDIUM，继续。
- 若 risk 为 HIGH/CRITICAL，停止并向用户汇报。

- [ ] **Step 2: 提取 service projection 测试类**

从 `tests/api/test_workline_runtime_api.py` 移出 `TestRuntimeQueryService` 类。

目标文件：`tests/workline_runtime/test_runtime_query_projection_service.py`

移动边界：

- 从 `class TestRuntimeQueryService:` 开始。
- 到文件末尾或该类结束为止。
- 保留 `tests/api/test_workline_runtime_api.py` 中的 route permission、integration debug API、trace API、runtime API facade 测试。

目标文件导入只保留该类实际使用的依赖。执行后用 Ruff 自动整理 import。

- [ ] **Step 3: 验证 API 文件仍只测 API 边界**

Run:

```bash
rtk rg -n "class TestRuntimeQueryService|RuntimeQueryService\\(" tests/api/test_workline_runtime_api.py
```

Expected:

```text
no matches
```

API 文件中仍允许出现 `runtime_query_service.get_*` patch，因为 API facade 测试需要验证 route 调用 service。

- [ ] **Step 4: 验证拆分后的两个文件**

Run:

```bash
rtk uv run ruff format tests/api/test_workline_runtime_api.py tests/workline_runtime/test_runtime_query_projection_service.py
rtk uv run ruff check tests/api/test_workline_runtime_api.py tests/workline_runtime/test_runtime_query_projection_service.py
rtk uv run pytest \
  tests/api/test_workline_runtime_api.py \
  tests/workline_runtime/test_runtime_query_projection_service.py \
  -q
```

Expected:

- Ruff 无错误。
- 两个测试文件全部通过。

- [ ] **Step 5: 收紧超大文件清单**

修改 `tests/support/test_suite_topology.py`：

- 从超大文件允许清单中移除 `tests/api/test_workline_runtime_api.py`。
- 保留 `tests/workline_runtime/test_runtime_intent_effects.py`，它会在 Task 4 拆分。

- [ ] **Step 6: 验证拓扑**

Run:

```bash
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 7: Commit**

Run:

```bash
rtk gitnexus detect-changes
git add tests/api/test_workline_runtime_api.py tests/workline_runtime/test_runtime_query_projection_service.py tests/support/test_suite_topology.py
git commit -m "test: 拆分 WorkLine runtime API 与 projection service 测试"
```

Expected:

- diff 显示测试代码移动为主。
- API 测试文件行数低于 `3000`。

---

## Task 4: 拆分 `test_runtime_intent_effects.py`

**Files:**
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Create: `tests/workline_runtime/support/runtime_intent_effects.py`
- Create: `tests/workline_runtime/test_runtime_intent_material_unit_effects.py`
- Create: `tests/workline_runtime/test_runtime_intent_completion_effects.py`
- Create: `tests/workline_runtime/test_runtime_intent_resource_effects.py`
- Create: `tests/workline_runtime/test_runtime_intent_command_effects.py`
- Create: `tests/workline_runtime/test_runtime_intent_external_operation_effects.py`
- Modify: `tests/support/test_suite_topology.py`

- [ ] **Step 1: 运行影响分析**

Run:

```bash
rtk gitnexus impact RuntimeIntentEffectApplier --direction upstream
rtk gitnexus impact RuntimeIntent --direction upstream
```

Expected:

- 这是测试拆分，不应修改业务符号。
- 若 GitNexus 认为风险 HIGH/CRITICAL，记录调用面并向用户确认是否继续。

- [ ] **Step 2: 抽出共享测试支撑**

创建 `tests/workline_runtime/support/runtime_intent_effects.py`。

从原文件顶部移动这些测试支撑对象：

- `_session`
- `_ctx`
- `RecordingResourceProjectionService`
- `RecordingBinCellReservationService`
- `RecordingHandlingOperationService`
- `RecordingDb`
- `MaterialUnitDb`
- `FakeTerminalRepository`
- `RecordingRackOperationStatusService`
- `_MATERIAL_UNIT_STATUS_TRANSITION_WARNING`

支撑文件不定义 `test_*` 函数，不直接执行 pytest 断言。

- [ ] **Step 3: 拆 material unit 测试**

创建 `tests/workline_runtime/test_runtime_intent_material_unit_effects.py`。

移动这些测试组：

- `test_create_material_unit_effect_*`
- `test_update_material_unit_status_*`
- `material_unit_effect_session` fixture

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_material_unit_effects.py -q
```

Expected:

- material unit 相关测试全部通过。

- [ ] **Step 4: 拆 completion/terminal 测试**

创建 `tests/workline_runtime/test_runtime_intent_completion_effects.py`。

移动这些测试组：

- `test_empty_intents_complete_new_event_session_as_noop`
- `test_update_context_and_complete`
- `test_complete_intent_*`
- `test_source_pick_*`
- `test_target_place_*`
- `test_ng_place_*`
- `test_cleanup_completed_material_unit_*`
- `test_clear_session_reference_*`
- `test_terminal_conflict_*`
- `test_non_smt_material_mounted_*`
- `test_smt_material_mounted_*`
- `test_handoff_terminal_result_*`

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_completion_effects.py -q
```

Expected:

- completion/terminal 相关测试全部通过。

- [ ] **Step 5: 拆 resource effect 测试**

创建 `tests/workline_runtime/test_runtime_intent_resource_effects.py`。

移动这些测试组：

- `test_resource_fact_*`
- `test_reconciling_resource_fact_*`
- `test_duplicate_resource_fact_*`
- `test_resource_reservation_*`
- `test_reconciling_resource_reservation_*`
- `test_consume_bin_cell_owner_mismatch_*`
- `test_mark_ng_writes_business_decision_timeline`
- `test_block_intent_holds_session_without_command_creation`
- `test_apply_resource_wait_*`
- `test_resource_wait_*`

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_resource_effects.py -q
```

Expected:

- resource effect 相关测试全部通过。

- [ ] **Step 6: 拆 command effect 测试**

创建 `tests/workline_runtime/test_runtime_intent_command_effects.py`。

移动这些测试组：

- `test_command_intent_*`
- `test_command_destination_*`
- `test_command_result_timeout_resolution`
- `test_command_before_terminal_intent_is_rejected_before_side_effects`
- `test_invalid_combinations_are_rejected_before_side_effects`

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_command_effects.py -q
```

Expected:

- command effect 相关测试全部通过。

- [ ] **Step 7: 拆 external/rack/bin/device event 测试**

创建 `tests/workline_runtime/test_runtime_intent_external_operation_effects.py`。

移动这些测试组：

- `test_external_request_*`
- `test_rack_operation_*`
- `test_single_layer_rack_operation_*`
- `test_bin_operation_*`
- `test_rack_bin_exchange_*`
- `test_device_event_intent_*`
- `test_resource_fact_then_device_event_*`
- `test_resource_fact_duplicate_storage_retry_device_event_*`
- `test_result_requires_outbox_dispatch_*`
- `test_wait_session_status_maps_rack_operation_to_external_wait`

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_external_operation_effects.py -q
```

Expected:

- external/rack/bin/device event 相关测试全部通过。

- [ ] **Step 8: 收束原文件**

处理 `tests/workline_runtime/test_runtime_intent_effects.py`：

- 若只剩 `test_apply_orchestrator_effects_dispatches_runtime_intents`，将其移动到 `tests/workline_runtime/test_orchestrator_write_back_service.py`。
- 若原文件无测试，删除该文件。
- 若保留原文件，文件长度必须低于 `500` 行，且文件名与内容一致。

- [ ] **Step 9: 运行拆分组完整验证**

Run:

```bash
rtk uv run ruff format \
  tests/workline_runtime/support/runtime_intent_effects.py \
  tests/workline_runtime/test_runtime_intent_material_unit_effects.py \
  tests/workline_runtime/test_runtime_intent_completion_effects.py \
  tests/workline_runtime/test_runtime_intent_resource_effects.py \
  tests/workline_runtime/test_runtime_intent_command_effects.py \
  tests/workline_runtime/test_runtime_intent_external_operation_effects.py

rtk uv run ruff check \
  tests/workline_runtime/support/runtime_intent_effects.py \
  tests/workline_runtime/test_runtime_intent_material_unit_effects.py \
  tests/workline_runtime/test_runtime_intent_completion_effects.py \
  tests/workline_runtime/test_runtime_intent_resource_effects.py \
  tests/workline_runtime/test_runtime_intent_command_effects.py \
  tests/workline_runtime/test_runtime_intent_external_operation_effects.py

rtk uv run pytest \
  tests/workline_runtime/test_runtime_intent_material_unit_effects.py \
  tests/workline_runtime/test_runtime_intent_completion_effects.py \
  tests/workline_runtime/test_runtime_intent_resource_effects.py \
  tests/workline_runtime/test_runtime_intent_command_effects.py \
  tests/workline_runtime/test_runtime_intent_external_operation_effects.py \
  -q
```

Expected:

- Ruff 无错误。
- RuntimeIntentEffect 拆分后的测试全部通过。

- [ ] **Step 10: 收紧超大文件清单**

修改 `tests/support/test_suite_topology.py`：

- 从超大文件允许清单中移除 `tests/workline_runtime/test_runtime_intent_effects.py`。

Run:

```bash
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 11: Commit**

Run:

```bash
rtk gitnexus detect-changes
git add tests/workline_runtime tests/support/test_suite_topology.py
git commit -m "test: 拆分 RuntimeIntentEffect 大型测试文件"
```

Expected:

- 不修改 `src/` 业务代码。
- `tests/workline_runtime/test_runtime_intent_effects.py` 不再出现在超大文件清单中。

---

## Task 5: 收敛 WorkLine runtime fixture 和 mock 支撑

**Files:**
- Create: `tests/workline_runtime/conftest.py`
- Create or Modify: `tests/workline_runtime/support/runtime_builders.py`
- Modify:
  - `tests/workline_runtime/test_outbox_dispatch_service.py`
  - `tests/workline_runtime/test_session_resolver.py`
  - `tests/workline_runtime/test_timeout_scanner.py`
  - `tests/workline_runtime/test_inbox_batch_processor.py`
  - `tests/workline_runtime/test_plugin_context.py`
  - `tests/workline_runtime/test_plugin_context_builder.py`

- [ ] **Step 1: 运行影响分析**

Run:

```bash
rtk gitnexus impact registered_test_workline_plugin --direction upstream
rtk gitnexus impact MockSessionRepository --direction upstream
```

Expected:

- 如果 GitNexus 无法定位测试 fixture，记录为工具限制并继续本地 grep 检查。
- 若 HIGH/CRITICAL，停止并汇报。

- [ ] **Step 2: 创建 runtime builder 支撑模块**

创建 `tests/workline_runtime/support/runtime_builders.py`，集中这些无副作用 builder：

- `make_mock_db`
- `make_mock_workline`
- `make_mock_session`
- `make_mock_device`
- `make_mock_outbox`
- `make_mock_inbox`

命名要求：

- builder 返回普通对象或 `SimpleNamespace`。
- builder 不访问数据库。
- builder 不 patch 业务模块。

- [ ] **Step 3: 创建 runtime conftest**

创建 `tests/workline_runtime/conftest.py`，集中 fixture：

- `workline_runtime_mock_db`
- `workline_runtime_session`
- `workline_runtime_workline`
- `workline_runtime_devices_by_role`

不要复用短名 `mock_db`，避免和文件内 fixture 冲突。

- [ ] **Step 4: 迁移 outbox dispatch fixture**

修改 `tests/workline_runtime/test_outbox_dispatch_service.py`：

- 将类内 `mock_db` fixture 改用 `workline_runtime_mock_db`。
- 保留该文件特有的 `mock_outbox_repo`、`mock_device_repo`，因为它们绑定 OutboxDispatchService 交互细节。
- 先不改测试断言。

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py -q
```

Expected:

- 全部通过。

- [ ] **Step 5: 迁移 session resolver fixture**

修改 `tests/workline_runtime/test_session_resolver.py`：

- 将类内 `mock_db` fixture 改用 `workline_runtime_mock_db`。
- 将通用 session/inbox builder 调整到 `runtime_builders.py`。
- 保留该文件专用的 repository fake 类，因为它们表达 resolver 查询路径。

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_session_resolver.py -q
```

Expected:

- 全部通过。

- [ ] **Step 6: 迁移 timeout/inbox/plugin context 小文件 fixture**

修改这些文件中重复的 `mock_db`、`mock_session`、`mock_workline`：

- `tests/workline_runtime/test_timeout_scanner.py`
- `tests/workline_runtime/test_inbox_batch_processor.py`
- `tests/workline_runtime/test_plugin_context.py`
- `tests/workline_runtime/test_plugin_context_builder.py`

使用 `tests/workline_runtime/conftest.py` 的长名 fixture，避免短名冲突。

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_timeout_scanner.py \
  tests/workline_runtime/test_inbox_batch_processor.py \
  tests/workline_runtime/test_plugin_context.py \
  tests/workline_runtime/test_plugin_context_builder.py \
  -q
```

Expected:

- 全部通过。

- [ ] **Step 7: 验证重复 fixture 收敛**

Run:

```bash
rtk uv run python - <<'PY'
from __future__ import annotations
import ast
from pathlib import Path
from collections import defaultdict

fixtures = defaultdict(list)
for path in Path("tests/workline_runtime").rglob("test_*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                text = ast.unparse(dec)
                if "pytest.fixture" in text or "pytest_asyncio.fixture" in text:
                    fixtures[node.name].append(str(path))

for name, paths in sorted(fixtures.items()):
    if len(paths) > 1 and name in {"mock_db", "mock_session", "mock_workline"}:
        raise SystemExit(f"duplicated fixture {name}: {paths}")
print("runtime fixture duplicates cleaned")
PY
```

Expected:

```text
runtime fixture duplicates cleaned
```

- [ ] **Step 8: Commit**

Run:

```bash
rtk gitnexus detect-changes
git add tests/workline_runtime
git commit -m "test: 收敛 WorkLine runtime 共享测试支撑"
```

Expected:

- diff 主要是 fixture 参数名和 helper import 调整。
- 不改变业务断言语义。

---

## Task 6: 修复显式 mock 测试收集噪音

**Files:**
- Modify: `tests/mock/test_data_generator.py`
- Test: `tests/mock/`

- [ ] **Step 1: 运行影响分析**

Run:

```bash
rtk gitnexus impact TestDataScenario --direction upstream
```

Expected:

- 若 risk 为 LOW/MEDIUM，继续。
- 若 HIGH/CRITICAL，停止并汇报。

- [ ] **Step 2: 重命名 Enum**

修改 `tests/mock/test_data_generator.py`：

- `class TestDataScenario(str, Enum)` 改为 `class DataScenario(str, Enum)`
- 同文件内所有 `TestDataScenario` 引用改为 `DataScenario`

不要改 enum value。

- [ ] **Step 3: 验证 mock collect warning 消失**

Run:

```bash
rtk proxy uv run pytest --collect-only -q -o addopts='' tests/mock 2>&1 | tee /tmp/wes-mock-collect.txt
rtk rg "PytestCollectionWarning|cannot collect test class" /tmp/wes-mock-collect.txt
```

Expected:

- 第一条命令收集 `tests/mock`。
- 第二条命令无匹配。

- [ ] **Step 4: 运行 mock 测试**

Run:

```bash
rtk uv run pytest tests/mock -q
```

Expected:

- `tests/mock` 全部通过。

- [ ] **Step 5: Commit**

Run:

```bash
rtk gitnexus detect-changes
git add tests/mock/test_data_generator.py
git commit -m "test: 修复 mock 测试收集噪音"
```

Expected:

- 只改测试 helper enum 命名。

---

## Task 7: 最终验证与收尾文档

**Files:**
- Modify: `tests/README.md`
- Modify: `tests/support/test_suite_topology.py`
- Test: full default collect and targeted directories

- [ ] **Step 1: 更新最终基线数字**

重新扫描当前测试套件：

```bash
rtk proxy find tests -type f -name 'test_*.py' | sort | wc -l
rtk proxy find tests -type f -name 'test_*.py' -print0 | xargs -0 wc -l | sort -nr | head -20
rtk uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected:

- 没有 collection error。
- 超过 `3000` 行的测试文件为 `0`。
- 默认 collect 数量与执行前接近；如果数量变化，必须能由文件搬迁或拆分解释。

- [ ] **Step 2: 更新 `tests/README.md` 的治理结果**

在 `tests/README.md` 增加“当前治理约束”：

- 新增测试默认不要放在 `tests/` 根目录。
- 单个测试文件目标低于 `1000` 行。
- 超过 `3000` 行的测试文件会触发 guardrail。
- API 文件只测 route/facade/response contract。
- service/projection/builder 测试放回对应领域目录。

- [ ] **Step 3: 验证默认快速回归入口**

Run:

```bash
rtk uv run pytest --collect-only -q -o addopts='' | tail -5
rtk uv run pytest tests/architecture tests/workline_runtime tests/api tests/admin tests/device tests/contracts/device -q
```

Expected:

- collect 无错误。
- listed 目录测试全部通过。

- [ ] **Step 4: 运行 lint**

Run:

```bash
rtk uv run ruff format tests
rtk uv run ruff check tests
```

Expected:

- Ruff format 完成。
- Ruff check 无错误。

- [ ] **Step 5: GitNexus 变更检测**

Run:

```bash
rtk gitnexus detect-changes
```

Expected:

- 变更范围只涉及测试文件、测试支撑文件、测试文档。
- 若显示 src 业务符号被修改，停止并检查 diff。

- [ ] **Step 6: Commit**

Run:

```bash
git add tests/README.md tests/support/test_suite_topology.py
git commit -m "docs(test): 记录测试套件瘦身治理规则"
```

Expected:

- 最终文档和 guardrail 状态已提交。

---

## 执行注意事项

- 不要在本计划中删除测试覆盖，除非能证明同一行为已经由同层级测试覆盖，并且用户明确确认。
- 文件拆分时优先保持测试函数名不变，减少历史定位成本。
- 每次移动文件后立即跑对应文件或目录测试，不要等所有移动完成后再统一修。
- 如果某个测试失败是因为 import 路径变更，先修测试 import；如果失败暴露业务行为变化，停止并单独调查。
- `tests/e2e`、`tests/resilience`、`tests/load`、`tests/mock` 仍然是显式运行目录，不纳入默认快速回归。

## 自检

**Spec coverage:**

- 测试过重判断：Task 1 固化基线和治理规则。
- 可合并/清理：Task 2 清理根目录散落测试，Task 3/4 拆超大文件，Task 5 收敛 fixture/mock。
- 不破坏默认回归：每个任务都有 collect 和定向 pytest 验证。
- 项目规则：计划使用 `rtk uv run ...`，Commit 前包含 `rtk gitnexus detect-changes`，涉及符号重命名前包含 GitNexus impact。

**Placeholder scan:**

- 本计划不包含占位式执行项。
- 每个任务包含明确文件、命令和期望结果。

**Type consistency:**

- 共享支撑命名固定为 `tests/workline_runtime/support/runtime_intent_effects.py` 和 `tests/workline_runtime/support/runtime_builders.py`。
- runtime 共享 fixture 使用 `workline_runtime_*` 前缀，避免和现有 `mock_db`、`mock_session` 短名冲突。
