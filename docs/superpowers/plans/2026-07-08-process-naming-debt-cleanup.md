# 过程命名去除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 active code、active gate、active test 中的 `Phase/phase/wave/lane`、阶段编号、`final cleanup`、`burn-down` 等重构过程命名收敛为稳定业务/架构命名，并建立 guardrail 防止过程命名重新进入生产路径。`closure`、`readiness`、`cleanup` 这类通用词只有绑定阶段编号或重构过程语境时才禁止；`production_closure`、`runtime_evidence_readiness` 等稳定发布语义允许使用。

**Architecture:** 先把过程命名分成三类：生产路径必须改、active gate 必须稳定化、历史审计允许保留。生产 runtime capability 从 `phase4` 包迁入稳定的 `material_flow` 能力域；生产 closure/readiness gate 从阶段编号改为 production/evidence/workline restructuring 语义；历史 docs、archive 和 Alembic revision 名保留原样，因为它们记录已发生的迁移事实。

**Tech Stack:** Python 3.13, FastAPI backend, pytest, ruff, shell scripts under `scripts/`, Alembic migrations, GitNexus impact/detect changes, `uv run ...`.

**Implementation Status (2026-07-08):** 已实施完成。实际执行采用单一收口提交承载所有命名收敛、guardrail 接入、文档同步和验证证据，而不是按计划草案中的每个小 task 分拆提交。最终验证覆盖：

- `uv run pytest tests/ -o addopts='' -q --tb=short`：`1838 passed, 5 skipped, 3 warnings`
- `./scripts/git-quality-gate.sh --profile quality`：通过，且包含 `process-naming` guardrail
- `uv run pytest --collect-only -q -o addopts='' | tail -5`：`1843 tests collected`
- `git diff --check`：无输出
- GitNexus `detect_changes(scope=all)` 已运行；当前 MCP 仅登记主 checkout 且索引落后一提交，无法完整映射本 worktree 的 168-file diff，返回低风险但不完整结果。以本地 diff stat、全量 pytest 和 quality gate 作为最终提交前交叉验证。

---

## Investigation Summary

本计划来自 2026-07-08 的 `/investigate` 扫描。根因假设：

> 过程命名来自 WorkLine 重构的阶段化验收链。PR #74 到 PR #80 为了 evidence、ledger、gate 可复核，把 `Phase3/Phase4/Phase5` 等过程名固化到代码和脚本里；final cleanup 后缺少第二步“稳定域命名收敛”，导致部分生产路径仍表达历史阶段，而不是稳定业务能力。

当前扫描命中类型：

| 类型 | 当前例子 | 处理策略 |
| --- | --- | --- |
| 生产包路径 | `src/app/runtime/capabilities/phase4/` | 必须改为稳定域路径 |
| 生产类名/变量 | `Phase4RuntimeCapabilityPlan`, `Phase4SorterInboundRuntimeService`, `phase4_sorter_inbound_runtime_service` | 必须改为稳定命名，不保留生产别名 |
| 运行时标识 | `phase4:{request_id}:...`, `PHASE4_SORTER_INBOUND`, `PHASE4_RECONCILIATION` | 必须改为稳定 prefix/source；本项目未发布，可接受破坏性变更 |
| 生产 gate 模块 | `src/app/runtime/orchestration/phase3_closure_gate.py`, `RuntimePhase3ClosureGate` | 改为 production closure 命名 |
| active scripts | `check_runtime_production_closure_gate.py`, `check_runtime_evidence_readiness_gate.py`, `check_workline_restructuring_readiness_gate.py` | 改为 stable script names，并同步 quality profile |
| 注释残留 | `Phase 1/2 burn-down`, `阶段 2 镜像`, `Phase5 后...` | 保留有价值解释，删掉阶段编号，改写为当前架构事实 |
| 允许保留 | Alembic revision 文件名、`docs/archive/**`、历史计划/spec/review、release evidence bundle | 保留，因为它们是历史审计事实 |

稳定命名目标：

| 当前命名 | 目标命名 |
| --- | --- |
| `src.app.runtime.capabilities.phase4` | `src.app.runtime.capabilities.material_flow` |
| `Phase4RuntimeCapabilityPlan` | `RuntimeCapabilityPlan` |
| `Phase4SorterInboundRuntimeService` | `SorterInboundRuntimeService` |
| `Phase4SorterInboundPreviewService` | `SorterInboundPreviewService` |
| `phase4_sorter_inbound_runtime_service` | `sorter_inbound_runtime_service` |
| `phase4_sorter_inbound_preview_service` | `sorter_inbound_preview_service` |
| `phase4:` idempotency prefix | `material-flow:` |
| `PHASE4_SORTER_INBOUND` | `SORTER_INBOUND_RUNTIME` |
| `PHASE4_RECONCILIATION` | `SMT_NG_WMS_RECONCILIATION` |
| `RuntimePhase3ClosureGate` | `RuntimeProductionClosureGate` |
| `RuntimePhase3ClosureValidation` | `RuntimeProductionClosureValidation` |
| `runtime_phase3_closure_gate` | `runtime_production_closure_gate` |
| `default_phase3_benchmark_scenarios` | `default_runtime_benchmark_scenarios` |
| `check_runtime_production_closure_gate.py` | `check_runtime_production_closure_gate.py` |
| `check_runtime_production_e2e_gate.py` | `check_runtime_production_e2e_gate.py` |
| `compose_runtime_production_e2e_artifact.py` | `compose_runtime_production_e2e_artifact.py` |
| `compose_runtime_benchmark_artifact.py` | `compose_runtime_benchmark_artifact.py` |
| `run_runtime_benchmarks.py` | `run_runtime_benchmarks.py` |
| `check_runtime_evidence_readiness_gate.py` | `check_runtime_evidence_readiness_gate.py` |
| `compose_runtime_evidence_artifact.py` | `compose_runtime_evidence_artifact.py` |
| `check_workline_restructuring_readiness_gate.py` | `check_workline_restructuring_readiness_gate.py` |
| `check_business_legacy_absence_gate.py` | `check_business_legacy_absence_gate.py` |
| `runtime-evidence-readiness` quality profile | `runtime-evidence-readiness` |
| `workline-restructuring-readiness` quality profile | `workline-restructuring-readiness` |
| `business-legacy-absence` quality profile | `business-legacy-absence` |

## Non-Goals

- 不重写 WorkLine/runtime 业务行为。
- 不改 Alembic revision ID 或已合并 migration 文件名；只允许改 migration 内非必要阶段化注释。
- 不清理 `docs/archive/**` 和 `docs/superpowers/archive/**`。
- 不把历史 evidence bundle 改写成“从未发生过 Phase3/Phase4/Phase5”；历史文档只补充“当前 active 名称”。

## Guardrail Policy

新增 active-code guardrail 后，以下位置不得出现重构过程命名：

- `src/**` active production code，排除 `src/static/**`。
- active scripts under `scripts/**`；`scripts/architecture-guardrails.sh` 的 active CLI 也必须改为稳定 enforcement mode 命名。
- `scripts/architecture-guardrails.allowlist` 的 active path 字段必须随代码移动更新；`drop_phase`、`legacy_entry_id`、历史 reason 字段允许保留，因为它们是 legacy matrix 审计事实。
- default pytest 收集的 active tests，除明确验证历史 docs 或 migration 的测试。

以下位置允许保留：

- `migrations/versions/*.py` 文件名。
- `docs/archive/**`, `docs/superpowers/archive/**`。
- 2026-07-07 及以前的历史 plan/spec/review 文档。
- release/evidence 历史记录中不可重写的 artifact path。

---

### Task 1: Add Process Naming Guardrail Baseline

**Files:**
- Create: `tests/architecture/test_process_naming_guardrail.py`
- Modify: `tests/README.md`

- [x] **Step 1: Write the failing guardrail test**

Create `tests/architecture/test_process_naming_guardrail.py`.

The test must:

- Scan active code paths: `src/`, `scripts/`, `tests/architecture/`, `tests/contracts/`, `tests/workline_runtime/`, `tests/api/`, `tests/runtime/`, `tests/unit/`, `tests/core/`, `tests/database/`.
- Ignore: `src/static/`, `migrations/versions/`, `docs/archive/`, `docs/superpowers/archive/`, `reports/`, `__pycache__/`.
- Fail on process tokens in active paths:
  - path or import tokens: `phase[0-9]`, `phase_[0-9]`, `wave[0-9]`, `phase4`, `phase5`
  - symbol/string tokens: `Phase4`, `Phase5`, `PHASE4_`, `PHASE5_`, `phase4:`, `phase5:`
  - refactor-process phrases: `burn-down`, `technical lane`, `business lane`, `final cleanup`
- Allow documented historical tests only when their filename starts with a migration/doc guardrail purpose, for example `tests/migrations/test_phase1_device_fk_ring_dissolve.py`.

Use a local allowlist constant named `INTENTIONAL_PROCESS_NAMING_ALLOWLIST`. Each entry must include a short reason string. This is not for hiding offenders; it is for immutable historical artifacts.

Expected current failure examples:

```text
src/app/runtime/capabilities/phase4/sorter_inbound_runtime_service.py: phase4 path token
src/app/runtime/capabilities/phase4/sorter_inbound_runtime_service.py: Phase4 symbol token
src/app/runtime/capabilities/phase4/sorter_inbound_runtime_service.py: phase4: runtime key prefix
src/app/runtime/orchestration/phase3_closure_gate.py: phase3 path token
scripts/check_workline_restructuring_readiness_gate.py: phase5 script token
```

- [x] **Step 2: Run the guardrail and prove it fails**

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Expected:

```text
FAILED tests/architecture/test_process_naming_guardrail.py::test_active_code_does_not_use_process_phase_names
```

- [x] **Step 3: Document the guardrail in the test guide**

Modify `tests/README.md` under “当前治理约束” and add one bullet:

```markdown
- Active production code and active gates must not introduce process-stage names such as `phase4`, `Phase5`, `wave2`, `technical lane`, or `final cleanup`; use stable domain names instead. Historical docs, archived plans, and Alembic revision filenames are allowed.
```

- [x] **Step 4: Run topology guardrail**

Run:

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py tests/architecture/test_process_naming_guardrail.py -q
```

Expected:

```text
test_process_naming_guardrail.py ... FAILED
test_test_suite_topology_guardrail.py ... passed
```

The new guardrail must still fail at this point. The failure is the baseline for the cleanup.

- [x] **Step 5: Keep the red proof uncommitted**

Do not commit the failing guardrail baseline. The red run proves the test can detect current offenders, but the first commit containing `tests/architecture/test_process_naming_guardrail.py` must be a green commit after cleanup.

Record the failing examples in the implementation notes or PR description, then continue to Task 2 without staging this failing test.

---

### Task 2: Rename Runtime Capability Package From `phase4` To `material_flow`

**Files:**
- Move: `src/app/runtime/capabilities/phase4/` to `src/app/runtime/capabilities/material_flow/`
- Modify: all imports currently matching `src.app.runtime.capabilities.phase4`
- Modify: `scripts/architecture-guardrails.allowlist` active path fields that point into the moved package
- Test: all tests currently importing `src.app.runtime.capabilities.phase4`

- [x] **Step 1: Run GitNexus impact analysis for affected symbols**

Before editing classes, run impact analysis for:

```text
Phase4RuntimeCapabilityPlan
Phase4SorterInboundRuntimeService
Phase4SorterInboundPreviewService
SmtNgWmsReconciliationRuntimeService
SmtNgWmsReconciliationPreviewService
WorkLineStartAdmissionService
WorklineBinCellReservationService
NgReturnItemService
WorklineStationLeaseService
SmtInboundHandoffRouteService
SingleLayerRackOrchestrationService
```

Required outcome:

- If GitNexus returns HIGH or CRITICAL risk, stop and report the blast radius before editing.
- Otherwise record the impacted direct callers in the PR notes.
- Also run a package blast-radius sweep for exported classes, services, contract modules, and singleton instances under the moved package. The package move is wider than the sorter/reconciliation runtime services, so do not rely on the four original Phase4 symbols alone.

- [x] **Step 2: Move the package**

Run:

```bash
git mv src/app/runtime/capabilities/phase4 src/app/runtime/capabilities/material_flow
```

Expected:

```text
src/app/runtime/capabilities/material_flow/__init__.py exists
src/app/runtime/capabilities/phase4 no longer exists in git
```

- [x] **Step 3: Rename public runtime capability types**

Modify:

- `src/app/runtime/capabilities/material_flow/__init__.py`
- `src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py`
- `src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py`
- `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py`

Required changes:

```text
Phase4RuntimeCapabilityPlan -> RuntimeCapabilityPlan
Phase4SorterInboundRuntimeService -> SorterInboundRuntimeService
Phase4SorterInboundPreviewService -> SorterInboundPreviewService
phase4_sorter_inbound_runtime_service -> sorter_inbound_runtime_service
phase4_sorter_inbound_preview_service -> sorter_inbound_preview_service
```

Keep `SmtNgWmsReconciliationRuntimeService` and `SmtNgWmsReconciliationPreviewService`; those are business names and do not need process cleanup.

- [x] **Step 4: Update internal imports under the moved package**

Run:

```bash
rg -n "src\\.app\\.runtime\\.capabilities\\.phase4|from \\.|Phase4|phase4_sorter" src/app/runtime/capabilities/material_flow
```

Expected before edits:

```text
matches exist for phase4 import path and Phase4 symbols
```

Edit the moved files so internal imports use:

```python
from src.app.runtime.capabilities.material_flow...
```

Expected after edits:

```bash
rg -n "src\\.app\\.runtime\\.capabilities\\.phase4|Phase4|phase4_sorter" src/app/runtime/capabilities/material_flow
```

returns no output.

- [x] **Step 5: Update production callers**

Modify every production caller returned by:

```bash
rg -l "src\\.app\\.runtime\\.capabilities\\.phase4|Phase4|phase4_sorter" src scripts
```

Known production callers from investigation:

```text
scripts/data/repair_runtime_holds.py
scripts/data/sync_test_workline_devices.py
src/app/callback/services/callback_ingress_service.py
src/app/rack/services/operation_service.py
src/app/resource/services/smt_rack_bin_scheduling_service.py
src/app/runtime/capability_catalog.py
src/app/runtime/normalization/normalizers/input_normalizer.py
src/app/runtime/orchestration/orchestrator_bridge.py
src/app/runtime/orchestration/repositories/smt_inbound_handoff_repository.py
src/app/runtime/orchestration/runtime_intent_effects.py
src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py
src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py
src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py
src/app/runtime/orchestration/services/query/runtime_query_service.py
src/app/runtime/runtime_capability_catalog.py
src/app/workline/domain/models/__init__.py
src/app/workline/domain/models/barcode_decision.py
src/app/workline/domain/services/barcode_decision_service.py
src/app/workline/runtime_services.py
src/app/workline/services/write_back_service.py
src/app/workline/v1/operation.py
```

Required import target:

```python
from src.app.runtime.capabilities.material_flow...
```

- [x] **Step 6: Update architecture guardrail allowlist path fields**

Modify `scripts/architecture-guardrails.allowlist` path fields that point to the moved package:

```text
src/app/runtime/capabilities/phase4/... -> src/app/runtime/capabilities/material_flow/...
```

Keep historical governance metadata unchanged:

```text
drop_phase
legacy_entry_id
historical reason text
```

Run:

```bash
uv run pytest \
  tests/architecture/test_ri3_capability_injection_guardrail.py \
  tests/architecture/test_wlr_import_guardrail.py \
  tests/architecture/test_cleanup_matrix_guardrail.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 7: Update active tests to stable imports**

Modify every test caller returned by:

```bash
rg -l "src\\.app\\.runtime\\.capabilities\\.phase4|Phase4|phase4_sorter" tests
```

Required stable test names:

```text
tests/workline_runtime/test_sorter_inbound_runtime_service.py
tests/workline_runtime/test_sorter_inbound_preview_service.py
tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py
tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py
tests/workline_runtime/test_runtime_capability_dispatcher.py
```

These files should import `SorterInboundRuntimeService`, `SorterInboundPreviewService`, and `RuntimeCapabilityPlan`.

- [x] **Step 8: Run import and behavior tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_sorter_inbound_runtime_service.py \
  tests/workline_runtime/test_sorter_inbound_preview_service.py \
  tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py \
  tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py \
  tests/workline_runtime/test_runtime_capability_dispatcher.py \
  tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py \
  tests/api/test_workline_safety_operation_api.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 9: Confirm old package cannot be imported**

Run:

```bash
uv run python - <<'PY'
import importlib
for module in (
    "src.app.runtime.capabilities.phase4",
    "src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service",
):
    try:
        importlib.import_module(module)
    except ModuleNotFoundError:
        print(f"ABSENT {module}")
    else:
        raise SystemExit(f"unexpected importable module: {module}")
PY
```

Expected:

```text
ABSENT src.app.runtime.capabilities.phase4
ABSENT src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service
```

- [x] **Step 10: Commit package rename**

Run:

```bash
git add src/app/runtime/capabilities tests/workline_runtime tests/contracts tests/api tests/helpers scripts/data scripts/architecture-guardrails.allowlist src/app src/core
git commit -m "refactor(runtime): rename phase capability package"
```

---

### Task 3: Replace Runtime `phase4` Source Values And Idempotency Prefixes

**Files:**
- Modify: `src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py`
- Modify: `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py`
- Modify: `src/app/runtime/orchestration/runtime_intent_effects.py`
- Modify: tests asserting `phase4:` or `PHASE4_`

- [x] **Step 1: Add explicit regression assertions for stable runtime identifiers**

Modify:

- `tests/workline_runtime/test_sorter_inbound_runtime_service.py`
- `tests/workline_runtime/test_phase4_runtime_intent_effect_applier.py`, later renamed in Task 6
- `tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py`

Add or update assertions so generated runtime identifiers use:

```text
material-flow:<request_id>:location-fact
material-flow:<request_id>:pkg-binding
material-flow:<request_id>:inventory-confirm
material-flow:<request_id>:cell-reservation
material-flow:<request_id>:sorter-ready
material-flow:<request_id>:full-box-exchange
material-flow:<source_event_id>:reconciliation-evidence
SORTER_INBOUND_RUNTIME
SMT_NG_WMS_RECONCILIATION
```

- [x] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_sorter_inbound_runtime_service.py \
  tests/workline_runtime/test_phase4_runtime_intent_effect_applier.py \
  tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py \
  -q
```

Expected:

```text
FAILED with old phase4:/PHASE4_ values
```

- [x] **Step 3: Replace generated runtime identifiers**

Modify `src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py`:

- Add a module-level constant:

```python
MATERIAL_FLOW_IDEMPOTENCY_PREFIX = "material-flow"
SORTER_INBOUND_RUNTIME_SOURCE = "SORTER_INBOUND_RUNTIME"
```

- Replace every generated `phase4:` idempotency key with the prefix constant.
- Replace every `PHASE4_SORTER_INBOUND` source with `SORTER_INBOUND_RUNTIME_SOURCE`.

Modify `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py`:

- Use the same `MATERIAL_FLOW_IDEMPOTENCY_PREFIX`.
- Use `SMT_NG_WMS_RECONCILIATION` for reconciliation source names.

Modify `src/app/runtime/orchestration/runtime_intent_effects.py`:

- Replace `PHASE4_RECONCILIATION` with `SMT_NG_WMS_RECONCILIATION`.

- [x] **Step 4: Verify no active runtime `phase4` identifiers remain**

Run:

```bash
rg -n "phase4:|PHASE4_|Phase4|phase4_sorter" src tests scripts
```

Expected:

```text
no matches in active src/tests/scripts
```

If matches remain only in historical docs, do not change them in this task.

- [x] **Step 5: Run targeted runtime tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_sorter_inbound_runtime_service.py \
  tests/workline_runtime/test_phase4_runtime_intent_effect_applier.py \
  tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py \
  tests/workline_runtime/test_runtime_capability_dispatcher.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 6: Commit runtime identifier cleanup**

Run:

```bash
git add src/app/runtime/capabilities/material_flow src/app/runtime/orchestration/runtime_intent_effects.py tests/workline_runtime
git commit -m "refactor(runtime): stabilize material flow identifiers"
```

---

### Task 4: Rename Production Closure And Benchmark Gate Modules

**Files:**
- Move: `src/app/runtime/orchestration/phase3_closure_gate.py` to `src/app/runtime/orchestration/production_closure_gate.py`
- Modify: `src/app/runtime/orchestration/benchmark_gate.py`
- Modify: `src/app/runtime/orchestration/benchmark_artifact_composer.py`
- Modify: `src/app/runtime/orchestration/p0_e2e_gate.py`
- Modify: `src/app/runtime/orchestration/p0_e2e_artifact_composer.py`
- Modify: scripts and tests importing closure/benchmark gate symbols

- [x] **Step 1: Run GitNexus impact analysis**

Before editing classes/functions, run impact analysis for:

```text
RuntimePhase3ClosureGate
RuntimePhase3ClosureValidation
default_phase3_benchmark_scenarios
RuntimeBenchmarkGate
RuntimeP0E2EGate
```

Required outcome:

- HIGH/CRITICAL risk stops execution for user confirmation.
- MEDIUM or lower can proceed with targeted tests in this task.

- [x] **Step 2: Move closure gate module**

Run:

```bash
git mv src/app/runtime/orchestration/phase3_closure_gate.py src/app/runtime/orchestration/production_closure_gate.py
```

- [x] **Step 3: Rename closure gate symbols**

Modify `src/app/runtime/orchestration/production_closure_gate.py`:

```text
RuntimePhase3ClosureGate -> RuntimeProductionClosureGate
RuntimePhase3ClosureValidation -> RuntimeProductionClosureValidation
runtime_phase3_closure_gate -> runtime_production_closure_gate
MOCK_PHASE3_CLOSURE -> MOCK_PRODUCTION_CLOSURE
UNKNOWN_PHASE3_CLOSURE_PROFILE -> UNKNOWN_PRODUCTION_CLOSURE_PROFILE
MISSING_PHASE3_CLOSURE_ARTIFACTS -> MISSING_PRODUCTION_CLOSURE_ARTIFACTS
UNKNOWN_PHASE3_CLOSURE_ARTIFACTS -> UNKNOWN_PRODUCTION_CLOSURE_ARTIFACTS
INVALID_PHASE3_CLOSURE_ARTIFACTS -> INVALID_PRODUCTION_CLOSURE_ARTIFACTS
MISSING_PHASE3_CLOSURE_EVIDENCE_FILES -> MISSING_PRODUCTION_CLOSURE_EVIDENCE_FILES
MISMATCHED_PHASE3_CLOSURE_EVIDENCE_FILES -> MISMATCHED_PRODUCTION_CLOSURE_EVIDENCE_FILES
```

Keep `closure_profile` as an argument name; “closure profile” is a stable release concept when not tied to a phase number.

- [x] **Step 4: Rename benchmark default function**

Modify `src/app/runtime/orchestration/benchmark_gate.py`:

```text
default_phase3_benchmark_scenarios -> default_runtime_benchmark_scenarios
```

Modify `src/app/runtime/orchestration/benchmark_artifact_composer.py` imports and calls accordingly.

- [x] **Step 5: Update imports in scripts and tests**

Run before editing:

```bash
rg -n "phase3_closure_gate|RuntimePhase3Closure|runtime_phase3_closure_gate|default_phase3_benchmark_scenarios" src scripts tests
```

Update all matches to stable names.

Expected after editing:

```bash
rg -n "phase3_closure_gate|RuntimePhase3Closure|runtime_phase3_closure_gate|default_phase3_benchmark_scenarios" src scripts tests
```

returns no output.

- [x] **Step 6: Run closure and benchmark tests**

Run:

```bash
uv run pytest \
  tests/runtime/orchestration/test_phase3_closure_evidence_gate.py \
  tests/runtime/orchestration/test_phase3_p0_closure_contract.py \
  tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py \
  -q
```

Expected initially after import updates:

```text
passed
```

The test file names are cleaned in Task 6; this task keeps behavior green while changing production symbols.

- [x] **Step 7: Commit production gate module rename**

Run:

```bash
git add src/app/runtime/orchestration scripts tests/runtime/orchestration tests/contracts
git commit -m "refactor(runtime): rename production closure gates"
```

---

### Task 5: Stabilize Active Gate Script Names And Quality Profiles

**Files:**
- Move: `scripts/check_runtime_production_closure_gate.py` to `scripts/check_runtime_production_closure_gate.py`
- Move: `scripts/check_runtime_production_e2e_gate.py` to `scripts/check_runtime_production_e2e_gate.py`
- Move: `scripts/compose_runtime_production_e2e_artifact.py` to `scripts/compose_runtime_production_e2e_artifact.py`
- Move: `scripts/compose_runtime_benchmark_artifact.py` to `scripts/compose_runtime_benchmark_artifact.py`
- Move: `scripts/run_runtime_benchmarks.py` to `scripts/run_runtime_benchmarks.py`
- Move: `scripts/check_runtime_evidence_readiness_gate.py` to `scripts/check_runtime_evidence_readiness_gate.py`
- Move: `scripts/compose_runtime_evidence_artifact.py` to `scripts/compose_runtime_evidence_artifact.py`
- Move: `scripts/check_workline_restructuring_readiness_gate.py` to `scripts/check_workline_restructuring_readiness_gate.py`
- Move: `scripts/check_business_legacy_absence_gate.py` to `scripts/check_business_legacy_absence_gate.py`
- Modify: `scripts/git-quality-gate.sh`
- Modify: tests referencing old script names

- [x] **Step 1: Move active scripts**

Run the `git mv` commands exactly:

```bash
git mv scripts/check_runtime_production_closure_gate.py scripts/check_runtime_production_closure_gate.py
git mv scripts/check_runtime_production_e2e_gate.py scripts/check_runtime_production_e2e_gate.py
git mv scripts/compose_runtime_production_e2e_artifact.py scripts/compose_runtime_production_e2e_artifact.py
git mv scripts/compose_runtime_benchmark_artifact.py scripts/compose_runtime_benchmark_artifact.py
git mv scripts/run_runtime_benchmarks.py scripts/run_runtime_benchmarks.py
git mv scripts/check_runtime_evidence_readiness_gate.py scripts/check_runtime_evidence_readiness_gate.py
git mv scripts/compose_runtime_evidence_artifact.py scripts/compose_runtime_evidence_artifact.py
git mv scripts/check_workline_restructuring_readiness_gate.py scripts/check_workline_restructuring_readiness_gate.py
git mv scripts/check_business_legacy_absence_gate.py scripts/check_business_legacy_absence_gate.py
```

- [x] **Step 2: Update script internals**

Required stable CLI argument renames:

```text
--production-e2e-artifact -> --production-e2e-artifact
--runtime-benchmark-artifact -> --runtime-benchmark-artifact
--runtime-evidence-artifact -> --runtime-evidence-artifact
--runtime-evidence-artifact -> --runtime-evidence-artifact
--lane technical -> --scope technical
--lane business -> --scope business
scripts/architecture-guardrails.sh --phase phase0|phase1|phase2 -> --mode warn|enforced|expiry-check
ARCHITECTURE_PHASE -> ARCHITECTURE_GUARDRAIL_MODE
```

Required business contract guardrail internal renames in `scripts/check_business_legacy_absence_gate.py` after the file move:

```text
PHASE4_CONTRACTS_ROOT -> MATERIAL_FLOW_CONTRACTS_ROOT
PHASE4_CONTRACTS_PACKAGE -> MATERIAL_FLOW_CONTRACTS_PACKAGE
FORBIDDEN_PHASE4_CONTRACT_IMPORT_PREFIXES -> FORBIDDEN_MATERIAL_FLOW_CONTRACT_IMPORT_PREFIXES
phase4_contract_layer_violations -> material_flow_contract_layer_violations
```

Update `tests/architecture/test_phase5_business_contract_no_cycle_guardrail.py` in the same task so it imports the stable function and still proves that a relative import from `material_flow/contracts` into an implementation service is rejected.

Required stable output examples:

```text
Runtime production closure evidence passed
Runtime production E2E artifact passed
Runtime benchmark artifact written
Runtime evidence readiness gate passed
WorkLine restructuring readiness passed: scope=technical
Business legacy absence gate passed: mode=final
architecture-guardrails.sh --mode enforced
```

Keep `--closure-profile production` and `--readiness-profile production`; these are stable release concepts.
Keep `drop_phase` in `scripts/architecture-guardrails.allowlist` and `docs/architecture/legacy-cleanup-matrix.csv`; it is historical governance data, not an active CLI mode.

- [x] **Step 3: Update quality gate profiles**

Modify `scripts/git-quality-gate.sh`:

```text
runtime-evidence-readiness -> runtime-evidence-readiness
workline-restructuring-readiness -> workline-restructuring-readiness
business-legacy-absence -> business-legacy-absence
run_runtime_evidence_readiness_gate -> run_runtime_evidence_readiness_gate
run_workline_restructuring_readiness_gate -> run_workline_restructuring_readiness_gate
run_business_legacy_absence_gate -> run_business_legacy_absence_gate
run_architecture_check must call architecture-guardrails.sh --mode ${ARCHITECTURE_GUARDRAIL_MODE:-enforced}
```

Do not leave old active profile aliases in `git-quality-gate.sh`; the repo is unreleased and the old names would keep process naming active.

- [x] **Step 4: Update active tests and docs references required by tests**

Run:

```bash
rg -n "check_phase3|compose_phase3|run_phase3|check_phase4|compose_phase4|check_phase5|runtime-evidence-readiness|workline-restructuring-readiness|business-legacy-absence" scripts tests docs/architecture docs/superpowers/plans docs/superpowers/specs
```

Modify active tests and current architecture docs to use stable names. Leave historical dated plans/specs from 2026-07-07 and earlier as historical records unless an active test asserts them.

- [x] **Step 5: Run gate script tests**

Run:

```bash
uv run pytest \
  tests/contracts/test_phase5_readiness_gate.py \
  tests/contracts/test_phase4_runtime_readiness_gate.py \
  tests/contracts/test_runtime_evidence_artifact_composer.py \
  tests/contracts/test_phase5_business_destructive_cleanup_ledger.py \
  tests/architecture/test_phase5_business_contract_no_cycle_guardrail.py \
  tests/architecture/test_git_quality_gate_architecture_profile.py \
  tests/architecture/test_wlr_import_guardrail.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 6: Run stable script commands**

Run:

```bash
uv run python scripts/check_runtime_production_closure_gate.py
uv run python scripts/check_runtime_evidence_readiness_gate.py
uv run python scripts/check_workline_restructuring_readiness_gate.py --scope technical
uv run python scripts/check_business_legacy_absence_gate.py --mode final
```

Expected:

```text
Runtime production closure mock evidence passed
Runtime evidence readiness mock gate passed
WorkLine restructuring readiness passed: scope=technical
Business legacy absence gate passed: mode=final
```

- [x] **Step 7: Commit active gate rename**

Run:

```bash
git add scripts tests docs/architecture docs/superpowers
git commit -m "refactor(scripts): stabilize restructuring gate names"
```

---

### Task 6: Rename Active Test Files Carrying Process Names

**Files:**
- Move active test files whose filename contains `phase3`, `phase4`, or `phase5` but does not test immutable migration filenames or historical docs.
- Modify imports and test references after each move.

- [x] **Step 1: Move runtime behavior tests to stable names**

Run:

```bash
git mv tests/workline_runtime/test_phase4_runtime_intent_effect_applier.py tests/workline_runtime/test_runtime_intent_effect_applier.py
git mv tests/runtime/orchestration/test_phase3_closure_evidence_gate.py tests/runtime/orchestration/test_production_closure_evidence_gate.py
git mv tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py
git mv tests/runtime/orchestration/test_production_e2e_artifact_composer.py tests/runtime/orchestration/test_runtime_production_e2e_artifact_composer.py
git mv tests/runtime/orchestration/test_phase3_p0_closure_contract.py tests/runtime/orchestration/test_runtime_production_closure_contract.py
git mv tests/runtime/orchestration/test_phase3_operational_contracts.py tests/runtime/orchestration/test_runtime_operational_contracts.py
git mv tests/runtime/orchestration/test_phase3_recovery_policies.py tests/runtime/orchestration/test_runtime_recovery_policies.py
git mv tests/runtime/orchestration/test_runtime_inbox_phase3_service.py tests/runtime/orchestration/test_runtime_inbox_consumer_service.py
```

- [x] **Step 2: Move contract/API/architecture tests to stable names**

Run:

```bash
git mv tests/api/test_phase4_read_model_routes.py tests/api/test_runtime_read_model_routes.py
git mv tests/contracts/test_runtime_evidence_artifact_composer.py tests/contracts/test_runtime_evidence_artifact_composer.py
git mv tests/contracts/test_phase4_runtime_readiness_gate.py tests/contracts/test_runtime_evidence_readiness_gate.py
git mv tests/contracts/test_phase5_readiness_gate.py tests/contracts/test_workline_restructuring_readiness_gate.py
git mv tests/contracts/test_phase5_business_destructive_cleanup_ledger.py tests/contracts/test_business_legacy_absence_ledger.py
git mv tests/contracts/test_phase5_business_lane_matrix_closure.py tests/contracts/test_business_legacy_matrix_closure.py
git mv tests/architecture/test_phase5_legacy_absence_guardrail.py tests/architecture/test_legacy_absence_guardrail.py
git mv tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/architecture/test_business_legacy_absence_guardrail.py
git mv tests/architecture/test_phase5_business_contract_no_cycle_guardrail.py tests/architecture/test_business_contract_no_cycle_guardrail.py
git mv tests/architecture/test_backend_ci_phase3_benchmark_env.py tests/architecture/test_backend_ci_runtime_benchmark_env.py
```

Keep these migration/history tests unchanged:

```text
tests/migrations/test_phase1_device_fk_ring_dissolve.py
tests/migrations/test_phase4_runtime_location_reservation_migration.py
tests/architecture/test_phase0_legacy_matrix_contract.py
tests/architecture/test_phase2_runtime_status_owner_guardrail.py
```

Reason: they validate immutable migration/history semantics.

- [x] **Step 3: Move explicit heavy-test paths**

Run:

```bash
git mv tests/mock/material_flow tests/mock/material_flow
git mv tests/resilience/test_phase3_scenario_replay.py tests/resilience/test_runtime_scenario_replay.py
git mv tests/resilience/test_phase3_integration_lab.py tests/resilience/test_runtime_integration_lab.py
git mv tests/integration/test_phase3_conveyor_queue_membership_concurrency.py tests/integration/test_conveyor_queue_membership_concurrency.py
git mv tests/load/phase3_benchmark_scenarios.py tests/load/runtime_benchmark_scenarios.py
```

Fixture filenames under `tests/resilience/fixtures/phase3_*.json` and `tests/load/fixtures/runtime_benchmark_artifact.json` may be renamed in the same task if they are generated test data, not historical evidence. Use:

```bash
git mv tests/resilience/fixtures/phase3_runtime_replay_fixture.json tests/resilience/fixtures/runtime_replay_fixture.json
git mv tests/resilience/fixtures/phase3_simulator_replay_fixture.json tests/resilience/fixtures/runtime_simulator_replay_fixture.json
git mv tests/resilience/fixtures/phase3_integration_lab_fixture.json tests/resilience/fixtures/runtime_integration_lab_fixture.json
git mv tests/load/fixtures/runtime_benchmark_artifact.json tests/load/fixtures/runtime_benchmark_artifact.json
```

- [x] **Step 4: Update references after test moves**

Run:

```bash
rg -n "test_phase3|test_phase4|test_phase5|tests/mock/material_flow|phase3_benchmark|phase3_runtime|phase3_integration|phase3_simulator" tests scripts src docs/architecture
```

Update active references to the new filenames. Historical docs may keep old names only when describing past verification logs.

- [x] **Step 5: Run renamed tests**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_intent_effect_applier.py \
  tests/runtime/orchestration/test_production_closure_evidence_gate.py \
  tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py \
  tests/runtime/orchestration/test_runtime_production_e2e_artifact_composer.py \
  tests/runtime/orchestration/test_runtime_production_closure_contract.py \
  tests/contracts/test_runtime_evidence_artifact_composer.py \
  tests/contracts/test_runtime_evidence_readiness_gate.py \
  tests/contracts/test_workline_restructuring_readiness_gate.py \
  tests/contracts/test_business_legacy_absence_ledger.py \
  tests/architecture/test_legacy_absence_guardrail.py \
  tests/architecture/test_business_legacy_absence_guardrail.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 6: Commit active test rename**

Run:

```bash
git add tests scripts docs/architecture
git commit -m "refactor(tests): remove process names from active tests"
```

---

### Task 7: Rewrite Remaining Phase/Burn-Down Comments In Production Code

**Files:**
- Modify comments and docstrings only in active production files returned by the command below.

- [x] **Step 1: Generate the current production comment residual list**

Run:

```bash
rg -n "Phase 0|Phase 1|Phase 2|Phase 3|Phase 4|Phase 5|Phase1|Phase2|Phase3|Phase4|Phase5|phase1|phase2|phase3|phase4|phase5|burn-down|阶段 2|阶段 3|final cleanup|technical lane|business lane" src/app src/core src/celery_app --glob '!src/static/**'
```

Expected residual categories:

```text
runtime/orchestration bridge comments
wms_integration port docstrings
callback contract mirror comments
workline shim comments
external_contract_profile placeholder comments
```

- [x] **Step 2: Rewrite comments without removing business rationale**

Apply these replacements by meaning, not raw text:

| Old process wording | Stable wording |
| --- | --- |
| `Phase 1 CEO-001` | `WMS port contract` |
| `Phase 2 burn-down` | `runtime ownership migration` or `legacy import boundary cleanup` |
| `阶段 2 镜像` | `从旧 runtime 模块迁入的正式实现` |
| `阶段 3 整体删除时` | `旧 runtime 入口删除后` |
| `Phase5 后` | `legacy plugin runtime 删除后` |
| `technical lane/business lane` | `technical cleanup scope/business legacy cleanup scope` only in docs; remove from production comments |
| `final cleanup` | `legacy cleanup completed` only in docs; remove from production comments |

Do not delete comments that explain:

- why a bridge exists,
- why a local mirror exists,
- why a security profile is still required,
- why a compatibility exclusion exists,
- how a WorkLine/runtime boundary is enforced.

- [x] **Step 3: Verify comments are clean in active production code**

Run:

```bash
rg -n "Phase 0|Phase 1|Phase 2|Phase 3|Phase 4|Phase 5|Phase1|Phase2|Phase3|Phase4|Phase5|phase1|phase2|phase3|phase4|phase5|burn-down|technical lane|business lane|final cleanup" src/app src/core src/celery_app --glob '!src/static/**'
```

Expected:

```text
no matches, except business algorithm wording that does not identify restructuring milestones
```

If an algorithm legitimately has “read phase/write phase”, keep it only if it does not use numbered restructuring wording.

- [x] **Step 4: Run lint for touched files**

Run:

```bash
uv run ruff check src/app src/core src/celery_app
uv run ruff format --check src/app src/core src/celery_app
```

Expected:

```text
All checks passed
```

- [x] **Step 5: Commit production comment cleanup**

Run:

```bash
git add src/app src/core src/celery_app
git commit -m "docs(code): remove process names from production comments"
```

---

### Task 8: Update Current Architecture Docs And Add Naming Policy

**Files:**
- Create: `docs/architecture/process-naming-policy.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/phase3-phase4-production-evidence-bundle.md`
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
- Modify: `docs/architecture/business-legacy-absence-ledger.md`
- Modify: `docs/architecture/business-legacy-absence-ledger.csv` only if active script paths are stored there

- [x] **Step 1: Write the naming policy**

Create `docs/architecture/process-naming-policy.md` with these sections:

```markdown
# Process Naming Policy

## Active Code Rule

Active production code, active gate scripts, and default-regression tests must use stable domain or architecture names. They must not use implementation milestone names such as `phase4`, `Phase5`, `wave2`, `business lane`, or `final cleanup`.

Generic release words such as `closure`, `readiness`, and `cleanup` are allowed only when they are not tied to a numbered phase, wave, lane, burn-down, or final-cleanup process marker.

## Allowed Historical Records

Historical plans, archived specs, release logs, and Alembic revision filenames may preserve process names because they describe facts that already happened.

## Stable Replacement Vocabulary

Use `material_flow`, `production_closure`, `runtime_evidence`, `workline_restructuring`, `business_legacy_absence`, and `runtime_benchmark` for active names.

## Verification

Run `uv run pytest tests/architecture/test_process_naming_guardrail.py -q` before claiming this debt is closed.
```

- [x] **Step 2: Update the main restructuring doc with current names**

Modify `docs/architecture/workline-and-plugin-restructuring.md`:

- Add a short 2026-07-08 note that active code has been renamed from phase-process names to stable names.
- Replace current active path references:
  - `src/app/runtime/capabilities/phase4/` -> `src/app/runtime/capabilities/material_flow/`
  - `RuntimePhase3ClosureGate` -> `RuntimeProductionClosureGate`
  - old active script names -> new script names
- Keep historical PR summaries intact when they describe PR #74 to #80 history.

- [x] **Step 3: Update current evidence/ledger docs**

Modify active docs so their runnable commands use the new script names:

```text
docs/architecture/phase3-phase4-production-evidence-bundle.md
docs/architecture/legacy-cleanup-execution-plan.md
docs/architecture/legacy-cleanup-matrix.md
docs/architecture/business-legacy-absence-ledger.md
```

Historical section headings may still mention Phase3/Phase4/Phase5 if they refer to completed historical milestones.

- [x] **Step 4: Run doc reference scan**

Run:

```bash
rg -n "src/app/runtime/capabilities/phase4|RuntimePhase3ClosureGate|check_phase3|check_phase4|check_phase5|compose_phase3|compose_phase4|run_phase3" docs/architecture docs/superpowers/plans docs/superpowers/specs --glob '!docs/superpowers/archive/**'
```

Expected:

```text
only historical dated plan/spec references remain
```

- [x] **Step 5: Commit docs update**

Run:

```bash
git add docs/architecture docs/superpowers
git commit -m "docs(architecture): define stable naming policy"
```

---

### Task 9: Final Guardrail Integration And Verification

**Files:**
- Modify: `tests/architecture/test_process_naming_guardrail.py`
- Modify: `scripts/git-quality-gate.sh`
- Modify: any docs/tests needed after the final guardrail pass

- [x] **Step 1: Tighten the process naming guardrail**

Update `tests/architecture/test_process_naming_guardrail.py` so it fails on:

```text
src/app/runtime/capabilities/phase4
src.app.runtime.capabilities.phase4
Phase4
Phase5
PHASE4_
PHASE5_
phase4:
phase5:
check_phase3
check_phase4
check_phase5
compose_phase3
compose_phase4
run_phase3
runtime-evidence-readiness
workline-restructuring-readiness
business-legacy-absence
technical lane
business lane
final cleanup
burn-down
architecture-guardrails.sh --phase
ARCHITECTURE_PHASE
```

Allow only:

```text
migrations/versions/*.py
docs/archive/**
docs/superpowers/archive/**
historical dated plans/specs/reviews that are not active commands
scripts/architecture-guardrails.allowlist historical metadata fields such as drop_phase and legacy_entry_id
```

- [x] **Step 2: Add guardrail to the quality profile**

Modify `scripts/git-quality-gate.sh` quality profile so it runs:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Expected log label:

```text
process-naming-guardrail
```

- [x] **Step 3: Run final active naming scans**

Run the process naming guardrail as the authoritative final scan:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Then run optional manual scans using the same active-path allowlist policy as the guardrail. Do not use an unfiltered `rg` over all of `tests/`, because immutable migration/history tests intentionally retain historical phase names.

```bash
rg -n "src/app/runtime/capabilities/phase4|src\\.app\\.runtime\\.capabilities\\.phase4|Phase4|Phase5|PHASE4_|PHASE5_|phase4:|phase5:" src scripts tests/architecture tests/contracts tests/workline_runtime tests/api tests/runtime --glob '!src/static/**' --glob '!**/__pycache__/**'
rg -n "check_phase3|check_phase4|check_phase5|compose_phase3|compose_phase4|run_phase3|runtime-evidence-readiness|workline-restructuring-readiness|business-legacy-absence|architecture-guardrails\\.sh --phase|ARCHITECTURE_PHASE" scripts tests/architecture tests/contracts docs/architecture
rg -n "burn-down|technical lane|business lane|final cleanup" src scripts tests/architecture tests/contracts tests/workline_runtime tests/api tests/runtime
```

Expected:

```text
no unallowed active-code matches; allowed historical matches must be represented in `INTENTIONAL_PROCESS_NAMING_ALLOWLIST` with a reason
```

- [x] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest \
  tests/architecture/test_process_naming_guardrail.py \
  tests/architecture/test_test_suite_topology_guardrail.py \
  tests/workline_runtime/test_runtime_intent_effect_applier.py \
  tests/workline_runtime/test_sorter_inbound_runtime_service.py \
  tests/workline_runtime/test_runtime_capability_dispatcher.py \
  tests/contracts/test_runtime_evidence_artifact_composer.py \
  tests/contracts/test_runtime_evidence_readiness_gate.py \
  tests/contracts/test_workline_restructuring_readiness_gate.py \
  tests/contracts/test_business_legacy_absence_ledger.py \
  tests/runtime/orchestration/test_production_closure_evidence_gate.py \
  tests/runtime/orchestration/test_runtime_production_closure_contract.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 5: Run stable gates**

Run:

```bash
uv run python scripts/check_runtime_production_closure_gate.py
uv run python scripts/check_runtime_evidence_readiness_gate.py
uv run python scripts/check_workline_restructuring_readiness_gate.py --scope technical
uv run python scripts/check_business_legacy_absence_gate.py --mode final
```

Expected:

```text
Runtime production closure mock evidence passed
Runtime evidence readiness mock gate passed
WorkLine restructuring readiness passed: scope=technical
Business legacy absence gate passed: mode=final
```

- [x] **Step 6: Run full quality checks**

Run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest tests/ -q
./scripts/git-quality-gate.sh --profile quality
```

Expected:

```text
ruff format passes
ruff check passes
pytest passes
quality gate passes
```

- [x] **Step 7: Run GitNexus detect changes before final commit**

Run GitNexus detect changes comparing against `develop`:

```text
detect_changes({scope: "compare", base_ref: "develop"})
```

Required result:

- Changed symbols should be limited to runtime capability naming, production/evidence gate naming, active scripts/tests/docs, and process naming guardrail.
- If GitNexus reports unrelated HIGH/CRITICAL flow impact, stop and review before committing.

- [x] **Step 8: Commit final guardrail integration**

Run:

```bash
git add tests/architecture/test_process_naming_guardrail.py tests/README.md scripts/git-quality-gate.sh
git commit -m "test(architecture): enforce stable process-free names"
```

---

## Rollback Plan

Each task is independently committed. If a later task fails:

1. Keep the already-passing earlier commits.
2. Revert only the latest failing task commit with `git revert <commit>`.
3. Re-run the targeted tests for the previous task.
4. Do not reintroduce `src.app.runtime.capabilities.phase4` aliases as a workaround. Fix imports to stable names instead.

## Acceptance Criteria

- `src/app/runtime/capabilities/phase4/` is absent.
- Active production imports use `src.app.runtime.capabilities.material_flow`.
- No active production symbol starts with `Phase4`, `Phase5`, or `RuntimePhase3`.
- Runtime generated keys use `material-flow:` rather than `phase4:`.
- Runtime source values use stable names such as `SORTER_INBOUND_RUNTIME` and `SMT_NG_WMS_RECONCILIATION`.
- Active scripts use production/evidence/workline restructuring names, not phase-number names.
- Active tests and default regression paths use stable names, except immutable migration/history tests.
- `tests/architecture/test_process_naming_guardrail.py` passes and is part of the quality profile.
- `uv run pytest tests/ -q` passes.
- `./scripts/git-quality-gate.sh --profile quality` passes.

## Self-Review

Spec coverage:

- User asked for process naming cleanup beyond Phase4/Phase5. Covered by scanning and guardrailing `phase`, `wave`, `lane`, `readiness`, `closure`, `cleanup`, `residual`, `burn-down`, and runtime key/source names.
- User asked for an implementation plan. Covered as task-by-task plan with files, commands, expected results, and commit points.
- Project requires preserving valuable comments. Covered by Task 7, which rewrites comments by meaning instead of deleting rationale.
- Project requires GitNexus impact before symbol edits and detect changes before commit. Covered by Tasks 2, 4, and 9.

Placeholder scan:

- No forbidden placeholder markers.
- No “add appropriate error handling”.
- No unbounded “write tests for the above”.
- Commands and expected outputs are explicit.

Type consistency:

- `RuntimeCapabilityPlan`, `SorterInboundRuntimeService`, `SorterInboundPreviewService`, `RuntimeProductionClosureGate`, and `default_runtime_benchmark_scenarios` are used consistently across tasks.

## NOT in scope

- 不重命名 Alembic revision ID、migration 文件名或 migration 历史语义测试；它们是不可重写的迁移事实。
- 不重写 `docs/archive/**`、`docs/superpowers/archive/**`、历史 release evidence 路径或 2026-07-07 及以前的历史计划/spec/review。
- 不为旧 `phase*` import path、旧 gate script 名或旧 quality profile 名保留生产兼容 alias；本项目未发布，active surface 直接稳定化。
- 不改变 WorkLine/runtime 业务行为、数据库模型、API contract 或调度语义；本计划只做命名、治理和验证口径收敛。
- 不引入新的分发 artifact、CLI 包、镜像或外部发布流程；所有脚本仍在仓库内由 `uv run ...` 或现有 quality gate 调用。

## What already exists

- `src/app/runtime/capabilities/phase4/` 已包含目标业务能力、contracts、runtime service 和 singleton；计划复用并移动这些实现，不重写业务逻辑。
- `scripts/check_phase3_*`、`scripts/check_phase4_*`、`scripts/check_phase5_*` 已实现 closure/evidence/readiness/absence gates；计划改稳定名称和参数，不并行构建第二套 gate。
- `scripts/git-quality-gate.sh` 已接入 runtime toggle、runtime readiness、readiness、business cleanup、architecture guardrails、import-linter 和 test topology；计划复用该入口并更新 profile/check 名。
- `scripts/architecture-guardrails.sh` 与 `scripts/architecture-guardrails.allowlist` 已承担架构治理；计划稳定 active CLI，并保留 allowlist 的历史 `drop_phase`/`legacy_entry_id` 审计字段。
- 现有 `tests/workline_runtime/`、`tests/runtime/orchestration/`、`tests/contracts/`、`tests/architecture/` 已覆盖 runtime behavior、gate contracts、quality profile 和 architecture guardrails；计划重命名并补稳定命名断言。

## Test Coverage Diagram

```text
CODE PATHS / GATES                                      PLANNED COVERAGE
[+] process naming guardrail
  |-- red proof detects current offenders               [planned] Task 1, not committed while red
  |-- historical migration/docs exceptions              [planned] INTENTIONAL_PROCESS_NAMING_ALLOWLIST
  `-- quality profile integration                       [planned] Task 9 + git-quality-gate test

[+] package move: phase4 -> material_flow
  |-- imports and public exports                        [planned] targeted runtime/API/import tests
  |-- old package absent                                [planned] importlib negative test
  |-- architecture allowlist path fields                [planned] architecture guardrail tests
  `-- broad service blast radius                        [planned] GitNexus sweep + caller tests

[+] runtime identifiers
  |-- material-flow idempotency keys                    [planned] runtime service assertions
  |-- SORTER_INBOUND_RUNTIME source                     [planned] runtime service assertions
  `-- SMT_NG_WMS_RECONCILIATION source                  [planned] intent-effect assertions

[+] gate and script stabilization
  |-- production closure/benchmark module names         [planned] orchestration contract tests
  |-- runtime evidence/workline restructuring scripts   [planned] contract tests + stable CLI commands
  |-- business legacy absence contract guardrail        [planned] no-cycle guardrail regression
  `-- architecture guardrail mode CLI                   [planned] architecture guardrail + quality tests

[+] test/docs/comment cleanup
  |-- active test filenames and references              [planned] renamed test subset + topology guardrail
  |-- production comments preserve rationale            [planned] ruff check/format + process scan
  `-- current architecture docs use stable commands     [planned] doc reference scan

COVERAGE AFTER REVIEW FIXES: planned 18/18 paths
GAPS: 0 unresolved; D2-D8 accepted changes convert prior gaps into explicit tasks.
```

## Failure Modes

- Package move leaves stale imports in API/callback/runtime callers: covered by GitNexus blast-radius sweep, targeted runtime/API tests, and old-package negative import check.
- Architecture allowlist points to deleted `phase4` paths: covered by Task 2 allowlist path update and architecture guardrail tests.
- Historical migration tests make final scan fail: covered by reusing the process naming guardrail allowlist instead of unfiltered `rg tests`.
- Active architecture guardrail keeps `--phase phase1` CLI: covered by Task 5 stable `--mode` rename, quality gate tests, and final process naming guardrail.
- Business contract guardrail loses layer-cycle protection during renaming: covered by `material_flow_contract_layer_violations` regression test with a relative service import fixture.
- Runtime idempotency/source values drift back to `phase4`/`PHASE4_`: covered by explicit runtime assertions and final guardrail.
- Script/profile rename breaks local quality gate: covered by `tests/architecture/test_git_quality_gate_architecture_profile.py` and final `./scripts/git-quality-gate.sh --profile quality`.

Critical silent gaps after review: none identified.

## Worktree Parallelization Strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Task 1 guardrail red proof | `tests/architecture/`, `tests/README.md` | — |
| Task 2-3 runtime package and identifiers | `src/app/runtime/capabilities/`, runtime callers, API/callback tests | Task 1 |
| Task 4-5 gates/scripts/profiles | `src/app/runtime/orchestration/`, `scripts/`, gate tests | Task 1 |
| Task 6 active test moves | `tests/`, references in `scripts/` and docs | Task 2-5 |
| Task 7 production comments | `src/app/`, `src/core/`, `src/celery_app/` | Task 2-5 |
| Task 8 docs/policy | `docs/architecture/`, `docs/superpowers/` | Task 2-5 |
| Task 9 final guardrail/quality | `tests/architecture/`, `scripts/git-quality-gate.sh` | Task 2-8 |

Lane A: Task 2 -> Task 3 (sequential, shared runtime package and callers).

Lane B: Task 4 -> Task 5 (sequential, shared gate scripts and quality profile).

Lane C: Task 7 and Task 8 can start after A/B produce stable names; they may run in parallel if one owner handles code comments and another handles docs.

Final lane: Task 6 and Task 9 should run after A/B/C converge because they touch global test/doc references and final quality gates.

Conflict flags: Lane A and Lane B both touch tests and `scripts/`; if executed in separate worktrees, merge A+B before Task 6 to avoid filename/reference conflicts.

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Codex or Claude Code; checkbox as you ship.

- [x] **T1 (P1, human: ~20min / CC: ~5min)** — final verification — align final scans with process guardrail allowlist.
  - Surfaced by: Architecture review D2 — unfiltered `rg tests` conflicts with preserved migration/history tests.
  - Files: `tests/architecture/test_process_naming_guardrail.py`, `docs/superpowers/plans/2026-07-08-process-naming-debt-cleanup.md`
  - Verify: `uv run pytest tests/architecture/test_process_naming_guardrail.py -q`
- [x] **T2 (P1, human: ~30min / CC: ~8min)** — governance data — update architecture allowlist path fields after package move.
  - Surfaced by: Architecture review D3 — allowlist paths still point at `src/app/runtime/capabilities/phase4`.
  - Files: `scripts/architecture-guardrails.allowlist`, architecture guardrail tests
  - Verify: `uv run pytest tests/architecture/test_ri3_capability_injection_guardrail.py tests/architecture/test_wlr_import_guardrail.py tests/architecture/test_cleanup_matrix_guardrail.py -q`
- [x] **T3 (P1, human: ~45min / CC: ~10min)** — runtime package — expand GitNexus blast-radius sweep to all moved package exports.
  - Surfaced by: Architecture review D4 — package move is wider than the four originally listed symbols.
  - Files: `src/app/runtime/capabilities/material_flow/**`, runtime/API/callback callers and tests
  - Verify: GitNexus impact/context results plus targeted runtime/API tests from Task 2.
- [x] **T4 (P1, human: ~2h / CC: ~25min)** — architecture guardrail CLI — rename active `--phase` mode to stable enforcement mode.
  - Surfaced by: Architecture review D5 — active `architecture-guardrails.sh --phase phase1` conflicts with process naming policy.
  - Files: `scripts/architecture-guardrails.sh`, `scripts/git-quality-gate.sh`, architecture tests
  - Verify: `uv run pytest tests/architecture/test_git_quality_gate_architecture_profile.py tests/architecture/test_wlr_import_guardrail.py -q`
- [x] **T5 (P2, human: ~15min / CC: ~5min)** — commit hygiene — keep process guardrail red proof uncommitted.
  - Surfaced by: Code quality review D6 — committing a deliberately failing guardrail conflicts with green commit and rollback expectations.
  - Files: plan/checklist only
  - Verify: first commit containing `test_process_naming_guardrail.py` passes.
- [x] **T6 (P2, human: ~20min / CC: ~5min)** — naming policy — ban process-context terms, not stable generic release words.
  - Surfaced by: Code quality review D7 — `readiness/closure/cleanup` conflicted with stable replacement vocabulary.
  - Files: `docs/architecture/process-naming-policy.md`, `tests/architecture/test_process_naming_guardrail.py`
  - Verify: process guardrail allows `production_closure` and rejects `phase3_closure`.
- [x] **T7 (P2, human: ~40min / CC: ~10min)** — business contract guardrail — rename internal guardrail API and keep behavior regression.
  - Surfaced by: Test review D8 — business contract guardrail internals were not explicit in script rename task.
  - Files: `scripts/check_business_legacy_absence_gate.py`, `tests/architecture/test_business_contract_no_cycle_guardrail.py`
  - Verify: no-cycle guardrail rejects a `material_flow/contracts` relative service import.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | Not requested for this backend naming-debt cleanup plan |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not run | Outside voice skipped; no cross-model findings folded |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 7 issues, 0 critical gaps; D2-D8 accepted and folded into the plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not applicable | Backend-only code/scripts/docs naming cleanup |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | Stable CLI/profile naming covered by Eng Review |

- **VERDICT:** ENG CLEARED — ready to implement after the accepted D2-D8 plan revisions.

NO UNRESOLVED DECISIONS
