# Stale Test Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 `tests/` 中重构期间生效、现在已经不应作为长期回归入口的过渡测试，移除 callback 入站仍委托旧 Workline inbox 的过渡路径，修复全量 `uv run basedpyright .` 门禁，并保留长期架构边界的守护能力。

**Architecture:** 采用“先替换长期守护，再删除过渡测试和过渡委托”的顺序。所有仍有业务价值的断言迁移到稳定命名的 architecture / callback / topology guardrail；callback ACK 以 RuntimeInbox 作为唯一幂等和入站权威，旧 Workline inbox 不再作为 callback delegate；单纯验证 mirror 存在、cutover 阶段顺序、phase readiness 的测试退出默认快速回归和 quality gate。

**Tech Stack:** Python 3.13, pytest, Ruff, Bash quality gate, GitNexus, uv。

**Implementation Status:** 已完成、已 Review、已合入 `develop`，时间：2026-07-09。

**Implementation Branch:** `codex/stale-test-cleanup`

**Implementation Commits:** `2494ac9e759b8d8926ab6b208fe986b53040902c`、`dfde1f59a293c7aa9db0a2fe1dca7007619dc03c`、`627ad173e1d07faa01773ad7d4c34a15efa2daae`、`363cd4ce30dfdc16ebf122dd227dfca8fa5e2991`

**Landed PR:** [#83](https://github.com/kaizhoumasha/wes_backend/pull/83)

**Merge Commit:** `059afbdc4b9ceceb2416806e0bf33f23a64b1d56`

**Release Version:** `v0.15.3.0`

**Review Status:** 已按 merge base `2494ac9e759b8d8926ab6b208fe986b53040902c` 审查 `codex/stale-test-cleanup` 的完整 diff；未发现需要作者修改的 actionable issues。

**Land/Deploy Status:** PR 已合并到 `develop`；无 GitHub deploy workflow 或 production URL canary，按当前 Jenkins/GitLab 部署边界记录为 `DEPLOYED (UNVERIFIED)`。生产发布仍按 `docs/devops/prod-release-deploy.md` 从 `main` 与手动 runbook 执行。

**Status Sync Note:** 本次同步只更新计划文档的任务状态、合入信息和执行记录；实现代码、测试和提交内容保持不变。

---

## Scope Check

本计划主要处理后端仓库测试治理，并包含两个明确的工程收束：callback 入站 ACK 不再写旧 Workline inbox delegate，且全量 `uv run basedpyright .` 必须恢复通过。除此之外，不修改 API / Service / Repository / Database 业务分层行为；若执行过程中发现更多 legacy 依赖，只记录为后续工作，不扩大到业务重构。

本计划遵守项目规划文档约束：计划中只给短片段、命令和验收标准，不粘贴完整函数或大段测试实现。

## Current Evidence

- 当前 `tests/README.md` 写入的治理基线已经过期：文档写 `271` 个测试文件、`2669` 个 collect；调查时实际为 `227` 个 `test_*.py`，默认 collect 为 `1856`。
- `tests/contracts/test_workline_restructuring_readiness_gate.py` 仍测试重构 readiness gate，且 `scripts/git-quality-gate.sh` 的 quality profile 仍运行 `check_workline_restructuring_readiness_gate.py --scope technical`。
- `tests/architecture/test_orchestration_bridges_mirror.py` 与 `tests/architecture/test_workline_compat_mirror.py` 主要验证 mirror 文件存在和导出，不验证运行时行为。
- `tests/callback/test_callback_runtime_inbox_cutover.py` 仍用“先 RuntimeInbox，再 legacy inbox 过渡消费”的 cutover 语言锁定过渡顺序；生产代码也仍在 `CallbackOrchestrationService` 中委托旧 Workline inbox。
- `tests/architecture/test_phase0_legacy_matrix_contract.py` 与 `tests/architecture/test_phase2_runtime_status_owner_guardrail.py` 有长期守护价值，但文件名仍是 phase 过程命名。

## File Structure

- Modify: `scripts/git-quality-gate.sh`
  - 新增稳定 `runtime-production-closure` active check；移除 `workline-restructuring-readiness` active check 和 quality profile 调用。
- Delete: `scripts/check_workline_restructuring_readiness_gate.py`
  - readiness gate 已由长期 guardrails 接替。
- Delete: `tests/contracts/test_workline_restructuring_readiness_gate.py`
  - 删除 gate 自测，避免默认回归继续执行重构期 gate。
- Modify: `tests/architecture/test_git_quality_gate_architecture_profile.py`
  - 增加 quality gate 不再暴露 retired check 的断言。
- Modify: `src/app/callback/services/callback_orchestration_service.py`
  - 移除 callback 入站对旧 Workline inbox 的 delegate 写入；RuntimeInbox duplicate 是唯一 ACK duplicate 来源。
- Modify: `src/app/callback/services/callback_ingress_service.py`
  - 移除向 callback orchestration 传递 `inbox_service` 的过渡参数。
- Rename: `tests/callback/test_callback_runtime_inbox_cutover.py` -> `tests/callback/test_callback_runtime_inbox_authority.py`
  - 将 cutover 顺序断言改为 RuntimeInbox authority 断言：accepted 继续业务处理，duplicate 跳过下游处理，不再调用旧 Workline inbox delegate。
- Delete: `tests/architecture/test_orchestration_bridges_mirror.py`
  - 由稳定 runtime surface import guardrail 接替。
- Delete: `tests/architecture/test_workline_compat_mirror.py`
  - 由 callback contract、diagnostics import、legacy absence guardrails 接替。
- Modify: `tests/architecture/test_legacy_runtime_import_guardrail.py`
  - 增加稳定 runtime/workline public surface import 检查。
- Rename: `tests/architecture/test_workline_domain_mirror.py` -> `tests/architecture/test_workline_domain_boundary.py`
  - 文件名去除 mirror 语义，内容维持“legacy business contracts absent”边界。
- Rename: `tests/architecture/test_phase0_legacy_matrix_contract.py` -> `tests/architecture/test_legacy_matrix_contract.py`
  - 保留 legacy matrix 审计合同，去除 phase 文件名。
- Rename: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py` -> `tests/architecture/test_runtime_status_owner_guardrail.py`
  - 保留 runtime status owner guardrail，去除 phase 文件名。
- Modify: `tests/architecture/test_process_naming_guardrail.py`
  - 删除已重命名文件的 process naming allowlist。
- Modify: `tests/architecture/test_test_suite_topology_guardrail.py`
  - 增加 README 不再固化易过期测试数量的断言。
- Modify: `tests/README.md`
  - 删除过期固定数量，改为实时查询命令和当前清理约定。
- Modify: `docs/architecture/file_index.md`
  - 同步测试文件索引与已退役 gate。
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
  - 将 active readiness gate 引用改为历史记录或稳定 quality gate。
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
  - 将 active technical gate 引用改为已完成历史说明，不再作为当前执行入口。

---

### Task 1: Baseline And GitNexus Guard

**Files:**
- Read: `tests/README.md`
- Read: `scripts/git-quality-gate.sh`
- Read: `tests/architecture/test_git_quality_gate_architecture_profile.py`

- [x] **Step 1: Confirm clean worktree**

Run:

```bash
git status --short
```

Expected: no output. If output exists, inspect it and do not overwrite unrelated user changes.

- [x] **Step 2: Refresh GitNexus index**

Run:

```bash
gitnexus analyze
```

Expected: exit `0` and output containing `Repository indexed successfully`.

- [x] **Step 3: Record current test inventory**

Run:

```bash
find tests -type f -name 'test_*.py' | wc -l
uv run pytest --collect-only -q -o addopts='' | tail -5
find tests -maxdepth 1 -type f -name 'test_*.py' -print
```

Expected:

```text
227
1856 tests collected
```

The root-level `find` command should print no `tests/test_*.py` files.

- [x] **Step 4: Run GitNexus impact before editing quality gate symbols**

Use GitNexus impact analysis for these targets:

```text
run_workline_restructuring_readiness_gate
run_quality_profile
usage
```

Expected: no HIGH or CRITICAL impact. If GitNexus reports HIGH or CRITICAL, stop and report the affected callers before editing.

---

### Task 2: Retire WorkLine Restructuring Readiness Gate

**Files:**
- Modify: `tests/architecture/test_git_quality_gate_architecture_profile.py`
- Modify: `scripts/git-quality-gate.sh`
- Delete: `scripts/check_workline_restructuring_readiness_gate.py`
- Delete: `tests/contracts/test_workline_restructuring_readiness_gate.py`

- [x] **Step 1: Add the failing quality gate retirement test**

Append this focused test to `tests/architecture/test_git_quality_gate_architecture_profile.py`:

```python
def test_quality_gate_no_longer_exposes_workline_restructuring_readiness() -> None:
    """quality gate 不再暴露已退役的 WorkLine restructuring readiness check。"""
    text = QUALITY_GATE.read_text(encoding="utf-8")

    assert "workline-restructuring-readiness" not in text
    assert "check_workline_restructuring_readiness_gate.py" not in text
```

- [x] **Step 2: Run the new test and verify it fails**

Run:

```bash
uv run pytest tests/architecture/test_git_quality_gate_architecture_profile.py::test_quality_gate_no_longer_exposes_workline_restructuring_readiness -q
```

Expected: FAIL because `scripts/git-quality-gate.sh` still contains `workline-restructuring-readiness`.

- [x] **Step 3: Edit `scripts/git-quality-gate.sh`**

Remove these active surfaces:

```text
usage block entry:
  workline-restructuring-readiness
            Run only WorkLine restructuring technical-scope readiness gate.

function:
  run_workline_restructuring_readiness_gate() { ... }

quality profile call:
  run_workline_restructuring_readiness_gate

case branch:
  workline-restructuring-readiness)
      run_workline_restructuring_readiness_gate
      ;;
```

Add this stable runtime closure gate before removing the wrapper call:

```text
usage block entry:
  runtime-production-closure
            Run only runtime production closure gate.

function:
  run_runtime_production_closure_gate() {
      log_step "runtime-production-closure" "check_runtime_production_closure_gate.py"
      run_tool python scripts/check_runtime_production_closure_gate.py
  }

quality profile call:
  run_runtime_production_closure_gate

case branch:
  runtime-production-closure)
      run_runtime_production_closure_gate
      ;;
```

Keep `runtime-evidence-readiness`, `business-legacy-absence`, `process-naming`, `architecture`, and `import-linter` unchanged.

- [x] **Step 4: Add quality profile coverage for the stable runtime closure gate**

Append this focused test to `tests/architecture/test_git_quality_gate_architecture_profile.py`:

```python
def test_quality_profile_runs_runtime_production_closure_gate(tmp_path):
    """quality profile 必须调用 runtime production closure 门禁。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"scripts/check_runtime_production_closure_gate.py"* ]]; then
  echo "runtime production closure gate reached" >&2
  exit 26
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--profile", "quality"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 26
    assert "[runtime-production-closure] check_runtime_production_closure_gate.py" in result.stdout
    assert "runtime production closure gate reached" in result.stderr
```

Run:

```bash
uv run pytest tests/architecture/test_git_quality_gate_architecture_profile.py::test_quality_profile_runs_runtime_production_closure_gate -q
```

Expected: PASS after `scripts/git-quality-gate.sh` exposes and calls the stable runtime closure gate.

- [x] **Step 5: Remove retired gate script and self-test**

Run:

```bash
git rm scripts/check_workline_restructuring_readiness_gate.py
git rm tests/contracts/test_workline_restructuring_readiness_gate.py
```

Expected: both files staged as deletions.

- [x] **Step 6: Verify quality gate profile tests**

Run:

```bash
uv run pytest tests/architecture/test_git_quality_gate_architecture_profile.py -q
```

Expected: PASS.

- [x] **Step 7: Verify direct unsupported check behavior**

Run:

```bash
./scripts/git-quality-gate.sh --check workline-restructuring-readiness
```

Expected: exit `2` and stderr containing:

```text
Unsupported check: workline-restructuring-readiness
```

- [x] **Step 8: Check Task 2 diff scope**

Run:

```bash
git status --short
git diff --name-status
git diff --cached --name-status
```

Expected: Task 2 changes are limited to `scripts/git-quality-gate.sh`, `tests/architecture/test_git_quality_gate_architecture_profile.py`, and deletion of the retired readiness gate script/self-test. Do not commit here; final batched commit happens in Task 8.

---

### Task 3: Remove Callback Legacy Inbox Delegate

**Files:**
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Rename: `tests/callback/test_callback_runtime_inbox_cutover.py` -> `tests/callback/test_callback_runtime_inbox_authority.py`
- Modify: `tests/callback/test_callback_runtime_inbox_authority.py`
- Modify: `tests/architecture/test_process_naming_guardrail.py`

- [x] **Step 1: Run GitNexus impact for callback orchestration and ingress symbols**

Use GitNexus impact analysis for these targets:

```text
CallbackOrchestrationService.process_result
CallbackOrchestrationService.process_event
CallbackOrchestrationService.process_external
handle_callback_result
handle_callback_event
handle_callback_external
```

Expected: no unexpected HIGH or CRITICAL impact. If risk is HIGH or CRITICAL, report the direct callers and confirm the affected callback flows before changing production code.

- [x] **Step 2: Rename the file**

Run:

```bash
git mv tests/callback/test_callback_runtime_inbox_cutover.py tests/callback/test_callback_runtime_inbox_authority.py
```

- [x] **Step 3: Add a failing naming guard test**

Append this test to `tests/architecture/test_process_naming_guardrail.py`:

```python
def test_callback_runtime_inbox_tests_do_not_use_cutover_names() -> None:
    active_callback_tests = {path.as_posix() for path in Path("tests/callback").glob("test_*.py")}

    assert "tests/callback/test_callback_runtime_inbox_cutover.py" not in active_callback_tests
    assert "tests/callback/test_callback_runtime_inbox_authority.py" in active_callback_tests
```

- [x] **Step 4: Replace cutover ordering assertions with RuntimeInbox authority assertions**

In `tests/callback/test_callback_runtime_inbox_authority.py`, rename these tests:

```text
test_process_result_writes_runtime_inbox_before_legacy_workline_inbox
  -> test_process_result_uses_runtime_inbox_as_authority

test_process_event_writes_runtime_inbox_before_legacy_workline_inbox
  -> test_process_event_uses_runtime_inbox_as_authority

test_process_external_writes_runtime_inbox_before_legacy_transition_delegate
  -> test_process_external_uses_runtime_inbox_as_authority
```

Change the assertions from ordering against legacy delegate to absence of legacy delegate:

```python
assert call_order == ["runtime"]
legacy_inbox_service.create_command_result_inbox.assert_not_awaited()
legacy_inbox_service.create_device_event_inbox.assert_not_awaited()
legacy_inbox_service.create_external_http_inbox.assert_not_awaited()
```

For accepted callbacks, assert the expected downstream non-legacy behavior still happens:

```text
result callback -> command_service.handle_callback_result awaited, workline processing enqueue path called when applicable
event callback -> RuntimeInbox accepted, workline processing enqueue path called when applicable
external callback -> rack/handling lifecycle service still records the callback when applicable
```

Replace the legacy duplicate tests with RuntimeInbox duplicate tests:

```text
test_process_result_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources
test_process_event_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources
test_process_external_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources
```

Each duplicate test must use `created=False` from the RuntimeInbox writer and assert no command service, rack/handling lifecycle service, workline enqueue, or legacy inbox delegate is called.

- [x] **Step 5: Remove legacy delegate from callback orchestration**

In `src/app/callback/services/callback_orchestration_service.py`:

```text
- Remove the type-checking import for WorklineInboxService if it becomes unused.
- Remove the `inbox_service` parameter from process_result/process_event/process_external.
- Delete calls to create_command_result_inbox, create_device_event_inbox, create_external_http_inbox, and mark_as_processed.
- Delete legacy_duplicate / duplicate_inbox branches that only exist for old Workline inbox duplicate handling.
- Keep RuntimeInbox duplicate early returns (`created=False`) unchanged.
- Keep command result handling, event enqueue, rack/handling lifecycle recording, commit, SSE publish, and outbox dispatch behavior unchanged.
```

The resulting authority flow:

```text
callback request
  -> RuntimeInbox writer
     -> created=False: ACK duplicate, stop
     -> created=True: run the non-legacy business side effect
        -> result: command callback handling + workline processing
        -> event: workline processing when bound
        -> external: rack/handling lifecycle recording + optional workline processing
```

- [x] **Step 6: Remove legacy delegate argument passing from callback ingress**

In `src/app/callback/services/callback_ingress_service.py`:

```text
- Remove the `inbox_service` import if no longer used in this file.
- Remove `inbox_service=inbox_service` from process_result/process_event/process_external calls.
```

- [x] **Step 7: Run the callback authority tests**

Run:

```bash
uv run pytest tests/callback/test_callback_runtime_inbox_authority.py -q
```

Expected: PASS.

- [x] **Step 8: Run callback ingress focused tests**

Run:

```bash
uv run pytest tests/api/test_callback_route_contracts.py tests/callback/test_callback_mirror_integration.py -q
```

Expected: PASS. If these tests do not cover all three ingress wrappers, add focused callback ingress tests before proceeding.

- [x] **Step 9: Run process naming guardrail**

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Expected: PASS.

- [x] **Step 10: Run full static type check**

Run:

```bash
uv run basedpyright .
```

Expected: PASS. This is intentionally full-repo, not callback-only. Fix any remaining basedpyright errors before proceeding so the cleanup branch restores the type gate completely.

- [x] **Step 11: Check Task 3 diff scope**

Run:

```bash
git status --short
git diff --name-status
git diff --cached --name-status
```

Expected: Task 3 removes only callback legacy inbox delegate behavior and updates the callback RuntimeInbox authority tests. Do not commit here; final batched commit happens in Task 8.

---

### Task 4: Replace Mirror Smoke Tests With Stable Boundary Tests

**Files:**
- Modify: `tests/architecture/test_legacy_runtime_import_guardrail.py`
- Rename: `tests/architecture/test_workline_domain_mirror.py` -> `tests/architecture/test_workline_domain_boundary.py`
- Delete: `tests/architecture/test_orchestration_bridges_mirror.py`
- Delete: `tests/architecture/test_workline_compat_mirror.py`

- [x] **Step 1: Add stable runtime public-surface coverage**

In `tests/architecture/test_legacy_runtime_import_guardrail.py`, add this compact public-surface table and tests near the existing import guardrail tests:

```python
STABLE_RUNTIME_PUBLIC_SURFACES = (
    ("src.app.runtime.orchestration.enums", ("FailureDomain",)),
    ("src.app.runtime.orchestration.device_ordering", ("device_sort_key",)),
    (
        "src.app.runtime.orchestration.runtime_intent",
        ("RuntimeIntent", "BlockScope", "Destination", "RuntimeIntentKind"),
    ),
    ("src.app.runtime.orchestration.effect_result", ("RuntimeIntentEffectResult", "WriteBackDisposition")),
    ("src.app.runtime.orchestration.material_target_resolver", ("resolve_destination_device",)),
    ("src.app.runtime.orchestration.business_identity_bridge", ("resolve_payload_display_identity",)),
    ("src.app.runtime.orchestration.lock_bridge", ("RedisDistributedLock",)),
    ("src.app.runtime.orchestration.resource_wait_evidence_bridge", ("ResourceWaitEvidence",)),
    ("src.app.runtime.orchestration.sandbox_catalog_bridge", ("rough_sorter_scan_completed_payload",)),
    ("src.app.runtime.orchestration.events_bridge", ("assert_not_reserved_runtime_event", "RESERVED_RUNTIME_EVENTS")),
    ("src.app.runtime.orchestration.topology_bridge", ("WorklineTopologyView", "validate_topology_manifest")),
    ("src.app.runtime.orchestration.runtime_intent_effects", ("RuntimeIntentEffectApplier",)),
    ("src.app.workline.trace_context", ("TraceContext",)),
    ("src.app.workline.runtime_services", ("WorklineRuntimeServices", "build_workline_runtime_services")),
)


@pytest.mark.parametrize(("module_name", "required_symbols"), STABLE_RUNTIME_PUBLIC_SURFACES)
def test_stable_runtime_public_surfaces_import_without_legacy_runtime(
    module_name: str,
    required_symbols: tuple[str, ...],
) -> None:
    module = importlib.import_module(module_name)

    assert module.__name__ == module_name
    missing = [symbol for symbol in required_symbols if not hasattr(module, symbol)]
    assert not missing, f"{module_name} missing stable public symbols: {missing}"
```

Also add `import importlib` at the top of the file if not already present.

Also keep diagnostics facade coverage from the retired compat mirror test under a stable test name:

```python
def test_stable_runtime_diagnostics_facade_reexports_public_symbols() -> None:
    from src.app.runtime.orchestration import diagnostics

    expected = {
        "DiagnosticCard",
        "DiagnosticCodeDefinition",
        "DiagnosticContext",
        "DiagnosticEvent",
        "ErrorCode",
        "ErrorDomain",
        "ProblemClass",
        "Recoverability",
        "Severity",
        "build_diagnostic_card",
        "build_diagnostic_context",
        "build_diagnostic_event",
        "error_domain_for",
        "get_diagnostic_code_definition",
        "list_diagnostic_code_definitions",
        "map_failure_to_diagnostic",
    }
    exported = set(getattr(diagnostics, "__all__", [])) | {
        name for name in vars(diagnostics) if not name.startswith("_")
    }

    assert not expected - exported
```

- [x] **Step 2: Run the new public-surface coverage**

Run:

```bash
uv run pytest \
  tests/architecture/test_legacy_runtime_import_guardrail.py::test_stable_runtime_public_surfaces_import_without_legacy_runtime \
  tests/architecture/test_legacy_runtime_import_guardrail.py::test_stable_runtime_diagnostics_facade_reexports_public_symbols \
  -q
```

Expected: PASS.

- [x] **Step 3: Rename WorkLine domain boundary test**

Run:

```bash
git mv tests/architecture/test_workline_domain_mirror.py tests/architecture/test_workline_domain_boundary.py
```

In the renamed file, change the module docstring from:

```python
"""WorkLine domain mirrors are no longer runtime business contracts."""
```

to:

```python
"""WorkLine domain boundary keeps runtime business contracts out of workline."""
```

- [x] **Step 4: Delete weak mirror-only tests**

Run:

```bash
git rm tests/architecture/test_orchestration_bridges_mirror.py
git rm tests/architecture/test_workline_compat_mirror.py
```

- [x] **Step 5: Verify replacement coverage**

Run:

```bash
uv run pytest tests/architecture/test_legacy_runtime_import_guardrail.py tests/architecture/test_workline_domain_boundary.py -q
```

Expected: PASS.

- [x] **Step 6: Confirm deleted mirror files are not referenced from active tests**

Run:

```bash
rg -n "test_orchestration_bridges_mirror|test_workline_compat_mirror|test_workline_domain_mirror" tests scripts docs/architecture
```

Expected: no references in `tests/` or `scripts/`; active docs references are handled in Task 7.

- [x] **Step 7: Check Task 4 diff scope**

Run:

```bash
git status --short
git diff --name-status
git diff --cached --name-status
```

Expected: Task 4 adds stable runtime public-surface coverage, renames the domain boundary test, and deletes weak mirror-only tests. Do not commit here; final batched commit happens in Task 8.

---

### Task 5: Rename Long-Term Guardrails Away From Phase Names

**Files:**
- Rename: `tests/architecture/test_phase0_legacy_matrix_contract.py` -> `tests/architecture/test_legacy_matrix_contract.py`
- Rename: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py` -> `tests/architecture/test_runtime_status_owner_guardrail.py`
- Modify: `tests/architecture/test_process_naming_guardrail.py`

- [x] **Step 1: Add a failing process naming allowlist contraction test**

Append this test to `tests/architecture/test_process_naming_guardrail.py`:

```python
def test_active_guardrail_allowlist_no_longer_contains_retired_phase_test_paths() -> None:
    retired_paths = {
        Path("tests/architecture/test_phase0_legacy_matrix_contract.py"),
        Path("tests/architecture/test_phase2_runtime_status_owner_guardrail.py"),
    }

    assert retired_paths.isdisjoint(INTENTIONAL_PROCESS_NAMING_ALLOWLIST)
```

- [x] **Step 2: Run the new guard test and verify it fails**

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py::test_active_guardrail_allowlist_no_longer_contains_retired_phase_test_paths -q
```

Expected: FAIL because both old paths are still allowlisted.

- [x] **Step 3: Rename the files**

Run:

```bash
git mv tests/architecture/test_phase0_legacy_matrix_contract.py tests/architecture/test_legacy_matrix_contract.py
git mv tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/architecture/test_runtime_status_owner_guardrail.py
```

- [x] **Step 4: Update process naming allowlist**

In `tests/architecture/test_process_naming_guardrail.py`, remove these entries from `INTENTIONAL_PROCESS_NAMING_ALLOWLIST`:

```python
Path("tests/architecture/test_phase0_legacy_matrix_contract.py"): "historical matrix baseline contract",
Path("tests/architecture/test_phase2_runtime_status_owner_guardrail.py"): (
    "historical runtime-status ownership guardrail kept under original milestone name"
),
```

Do not add allowlist entries for the new names; the new names should pass without an exception.

- [x] **Step 5: Update docstrings in renamed tests**

In `tests/architecture/test_legacy_matrix_contract.py`, change the opening docstring to:

```python
"""Legacy cleanup matrix generation contract."""
```

In `tests/architecture/test_runtime_status_owner_guardrail.py`, change the opening docstring to:

```python
"""Runtime status ownership guardrail."""
```

Leave historical CSV fields such as `drop_phase` intact because they are audit data, not active process naming.

- [x] **Step 6: Run renamed guardrails**

Run:

```bash
uv run pytest tests/architecture/test_legacy_matrix_contract.py tests/architecture/test_runtime_status_owner_guardrail.py tests/architecture/test_process_naming_guardrail.py -q
```

Expected: PASS.

- [x] **Step 7: Check Task 5 diff scope**

Run:

```bash
git status --short
git diff --name-status
git diff --cached --name-status
```

Expected: Task 5 only renames the long-term guardrail tests and contracts the process naming allowlist. Do not commit here; final batched commit happens in Task 8.

---

### Task 6: Update Test Suite README And Topology Guardrail

**Files:**
- Modify: `tests/architecture/test_test_suite_topology_guardrail.py`
- Modify: `tests/README.md`

- [x] **Step 1: Add a failing README staleness guard**

Append this test to `tests/architecture/test_test_suite_topology_guardrail.py`:

```python
def test_readme_does_not_publish_fixed_test_inventory_counts() -> None:
    readme_text = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    stale_patterns = (
        r"下共有\s*`\d+`\s*个\s*`test_\*\.py`\s*文件",
        r"collect\s*为\s*`\d+`\s*个测试",
    )

    assert not any(re.search(pattern, readme_text) for pattern in stale_patterns)
```

Also add `import re` at the top of the file if not already present. The regex intentionally targets live inventory counts only; governance thresholds such as the `3000` line limit remain allowed.

- [x] **Step 2: Run the new topology test and verify it fails**

Run:

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py::test_readme_does_not_publish_fixed_test_inventory_counts -q
```

Expected: FAIL because `tests/README.md` still contains fixed test inventory and collect counts.

- [x] **Step 3: Replace fixed counts in `tests/README.md`**

Replace the “当前治理约束” count bullets with this text:

```markdown
本轮测试套件治理后的长期约束：

- `tests/` 根目录下不得新增 `test_*.py` 文件。
- 默认快速回归 collect 由 `pyproject.toml` 的 `norecursedirs` 和测试文件命名规则共同决定，不在文档中固化数量。
- 如需查看实时测试文件数量，运行 `find tests -type f -name 'test_*.py' | wc -l`。
- 如需查看实时默认 collect，运行 `uv run pytest --collect-only -q -o addopts='' | tail -5`。
- 单文件超过 `3000` 行会触发测试拓扑 guardrail。
```

- [x] **Step 4: Run topology guardrail**

Run:

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
```

Expected: PASS.

- [x] **Step 5: Verify current collect still works**

Run:

```bash
uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected: command exits `0` and prints the new collected test count.

- [x] **Step 6: Check Task 6 diff scope**

Run:

```bash
git status --short
git diff --name-status
git diff --cached --name-status
```

Expected: Task 6 only updates `tests/README.md` and `tests/architecture/test_test_suite_topology_guardrail.py`. Do not commit here; final batched commit happens in Task 8.

---

### Task 7: Sync Active Architecture Docs

**Files:**
- Modify: `docs/architecture/file_index.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Modify: `docs/architecture/phase3-phase4-production-evidence-bundle.md`
- Modify if referenced: `CHANGELOG.md`

- [x] **Step 1: Find active references to retired names**

Run:

```bash
rg -n "check_workline_restructuring_readiness_gate|test_workline_restructuring_readiness_gate|test_callback_runtime_inbox_cutover|test_orchestration_bridges_mirror|test_workline_compat_mirror|test_workline_domain_mirror|test_phase0_legacy_matrix_contract|test_phase2_runtime_status_owner_guardrail" docs/architecture tests scripts CHANGELOG.md
```

Expected: references appear only in files being edited in this task or in immutable historical changelog sections.

- [x] **Step 2: Update active docs to stable names**

Use these replacements in active docs:

```text
tests/callback/test_callback_runtime_inbox_cutover.py
  -> tests/callback/test_callback_runtime_inbox_authority.py

tests/architecture/test_workline_domain_mirror.py
  -> tests/architecture/test_workline_domain_boundary.py

tests/architecture/test_phase0_legacy_matrix_contract.py
  -> tests/architecture/test_legacy_matrix_contract.py

tests/architecture/test_phase2_runtime_status_owner_guardrail.py
  -> tests/architecture/test_runtime_status_owner_guardrail.py
```

For `check_workline_restructuring_readiness_gate.py`, do not replace with a new script. Rewrite active execution guidance to:

```text
当前提交前入口使用 ./scripts/git-quality-gate.sh --profile quality；runtime production closure、runtime evidence、business legacy absence、process naming、architecture guardrails 分别作为长期门禁执行。
```

- [x] **Step 3: Keep immutable historical records unchanged**

Do not edit archived plans under `docs/superpowers/plans/` or immutable release history rows that intentionally describe past PRs. If `CHANGELOG.md` references are clearly historical, leave them unchanged. Active architecture docs under `docs/architecture/` must not keep runnable command examples for deleted scripts; rewrite those lines as historical evidence or as stable quality-gate guidance.

- [x] **Step 4: Verify active reference cleanup**

Run:

```bash
rg -n "check_workline_restructuring_readiness_gate|test_workline_restructuring_readiness_gate|test_callback_runtime_inbox_cutover|test_orchestration_bridges_mirror|test_workline_compat_mirror|test_workline_domain_mirror|test_phase0_legacy_matrix_contract|test_phase2_runtime_status_owner_guardrail" docs/architecture tests scripts
```

Expected: no matches, except explanatory historical text that is clearly marked as historical and not an active command or active test index.

- [x] **Step 5: Run process naming guardrail**

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Expected: PASS.

- [x] **Step 6: Check Task 7 diff scope**

Run:

```bash
git status --short
git diff --name-status
git diff --cached --name-status
```

Expected: Task 7 only updates active architecture docs listed above. Do not commit here; final batched commit happens in Task 8.

---

### Task 8: Final Verification And Review

**Files:**
- Read: all changed files
- Run: quality gates and focused pytest suites

- [x] **Step 1: Run focused cleanup regression**

Run:

```bash
uv run pytest \
  tests/architecture/test_git_quality_gate_architecture_profile.py \
  tests/architecture/test_process_naming_guardrail.py \
  tests/architecture/test_test_suite_topology_guardrail.py \
  tests/architecture/test_legacy_runtime_import_guardrail.py \
  tests/architecture/test_workline_domain_boundary.py \
  tests/architecture/test_legacy_matrix_contract.py \
  tests/architecture/test_runtime_status_owner_guardrail.py \
  tests/callback/test_callback_runtime_inbox_authority.py \
  -q
```

Expected: PASS.

- [x] **Step 2: Run default collection**

Run:

```bash
uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected: exits `0`. The collected count should be lower than the pre-cleanup baseline because retired tests were deleted.

- [x] **Step 3: Run quality profile**

Run:

```bash
./scripts/git-quality-gate.sh --profile quality
```

Expected: PASS, no `workline-restructuring-readiness` step in stdout, and a `runtime-production-closure` step remains in stdout.

- [x] **Step 4: Run full static type check**

Run:

```bash
uv run basedpyright .
```

Expected: PASS.

- [x] **Step 5: Run GitNexus change detection before final commit**

Use GitNexus detect changes with scope `all`.

Expected: changed symbols and affected flows are limited to callback RuntimeInbox authority cleanup, test governance, quality gate, and docs. If GitNexus reports broader production impact, inspect before committing.

- [x] **Step 6: Review deleted files**

Run:

```bash
git status --short
git diff --stat
git diff --name-status
```

Expected deleted files:

```text
D scripts/check_workline_restructuring_readiness_gate.py
D tests/contracts/test_workline_restructuring_readiness_gate.py
D tests/architecture/test_orchestration_bridges_mirror.py
D tests/architecture/test_workline_compat_mirror.py
```

Expected renamed files:

```text
R tests/callback/test_callback_runtime_inbox_cutover.py -> tests/callback/test_callback_runtime_inbox_authority.py
R tests/architecture/test_workline_domain_mirror.py -> tests/architecture/test_workline_domain_boundary.py
R tests/architecture/test_phase0_legacy_matrix_contract.py -> tests/architecture/test_legacy_matrix_contract.py
R tests/architecture/test_phase2_runtime_status_owner_guardrail.py -> tests/architecture/test_runtime_status_owner_guardrail.py
```

- [x] **Step 7: Request code review**

Use `requesting-code-review` on the full diff. Required review focus:

```text
1. 是否误删仍有长期行为价值的测试。
2. callback ingress 是否已经只以 RuntimeInbox 作为 duplicate/ACK 权威，且未丢失 command result、event enqueue、rack/handling lifecycle 处理。
3. 全量 `uv run basedpyright .` 是否已恢复通过，且类型修复没有引入无关重构。
4. quality profile 是否仍覆盖 runtime evidence、business legacy absence、process naming、architecture、import-linter、test topology。
5. README 是否不再固化易过期数量。
6. active docs 是否没有引用 retired gate 或 retired test filenames。
```

- [x] **Step 8: Final commit if no review findings remain**

Verify the pending paths before staging the single batched commit:

```bash
git status --short
```

Expected: output contains only the files listed in this plan's File Structure section. If unrelated paths appear, including GitNexus-generated entrypoint metadata such as `AGENTS.md` or `CLAUDE.md`, stop and decide whether those changes belong in a separate commit before continuing.

Then commit:

```bash
git add -A
git diff --cached --name-status
git commit -m "test(cleanup): 清理过渡测试并收束回调入口"
```

Expected staged paths: only the planned callback services, tests, scripts, type-check fixes, and active documentation changes.

---

## Engineering Review Addendum

### NOT in scope

- 不清理除 callback 入站 legacy inbox delegate 之外的更多 legacy Workline 运行路径；若执行中发现其它依赖，只记录后续项。
- 不改 API 路由合同、权限模型或数据库 schema；callback result/event/external 的外部响应语义保持不变。
- 不重写 archived plans、历史 changelog 或不可变迁移记录；只更新 active architecture docs 中会误导执行的命令和索引。
- 不把 integration/e2e/resilience/load/mock 重测试目录纳入默认快速回归；默认收集边界仍由 `pyproject.toml` 和测试拓扑 guardrail 管理。

### What already exists

- `scripts/check_runtime_production_closure_gate.py` 已存在；计划将它接入 quality profile，而不是重新实现 production closure gate。
- `CallbackRuntimeInboxWriter` 已覆盖 result/event/external 三类 callback 的 RuntimeInbox 写入；Task 3 收束为复用该权威入口。
- `tests/architecture/test_legacy_runtime_import_guardrail.py` 已扫描 legacy runtime import；Task 4 在这里补长期 public-surface 断言，避免新增平行 guardrail。
- `tests/architecture/test_process_naming_guardrail.py` 已负责 active process naming；Task 3/5 复用它收敛 cutover/phase 文件名。
- `tests/support/test_suite_topology.py` 已集中测试套件拓扑扫描；Task 6 只补 README 防回潮规则。

### Test Coverage Diagram

```text
CODE PATHS / GATES                                      COVERAGE PLAN
[+] callback RuntimeInbox authority
  ├── result created=False duplicate                    [★★★] authority duplicate test, no legacy delegate/downstream calls
  ├── result created=True business processing           [★★★] command_service + enqueue assertions
  ├── event created=False duplicate                     [★★★] authority duplicate test, no legacy delegate/downstream calls
  ├── event created=True bound processing               [★★★] enqueue assertion
  ├── external created=False duplicate                  [★★★] authority duplicate test, no lifecycle/delegate calls
  └── external created=True lifecycle processing        [★★★] rack/handling lifecycle assertions

[+] retired readiness gate
  ├── old workline-restructuring check unavailable      [★★★] unsupported check exits 2
  └── runtime production closure remains in quality     [★★★] fake uv exit test + final quality profile

[+] retired mirror / phase / count debt
  ├── runtime public exports remain stable              [★★★] public-surface table + diagnostics facade
  ├── old mirror/phase/cutover paths disappear          [★★★] process naming + rg cleanup checks
  └── README avoids fixed live inventory counts         [★★★] regex guard + live command wording

[+] full static gate
  └── `uv run basedpyright .`                           [★★★] Task 3 and final verification

COVERAGE: 13/13 planned paths covered (100%)
QUALITY: ★★★:13  ★★:0  ★:0  |  GAPS: 0
```

### Failure Modes

- RuntimeInbox duplicate still triggers command/lifecycle side effects: covered by duplicate authority tests; expected behavior is early ACK duplicate return.
- Accepted callback stops doing business work after removing legacy delegate: covered by result/event/external accepted-path assertions and focused ingress tests.
- Deleting readiness gate silently drops production closure coverage: covered by `runtime-production-closure` quality profile test.
- Deleting mirror tests lets public runtime exports disappear: covered by stable public-surface assertions.
- README reintroduces fixed live test counts: covered by regex guard that forbids inventory/collect count phrasing.
- Type gate remains red after callback signature changes: covered by full `uv run basedpyright .`.

Critical silent gaps: 0.

### Worktree Parallelization Strategy

| Step | Modules touched | Depends on |
|------|-----------------|------------|
| Task 2 readiness gate retirement | `scripts/`, `tests/architecture/`, `tests/contracts/` | Task 1 |
| Task 3 callback authority | `src/app/callback/`, `tests/callback/`, `tests/architecture/` | Task 1 |
| Task 4-6 test governance | `tests/architecture/`, `tests/README.md` | Task 2 naming decisions |
| Task 7 docs sync | `docs/architecture/`, `CHANGELOG.md` if needed | Tasks 2-6 |
| Task 8 final verification | whole changed set | Tasks 2-7 |

Parallel lanes:

- Lane A: Task 2.
- Lane B: Task 3.
- Lane C: Task 4 -> Task 5 -> Task 6 sequentially, because they share `tests/architecture/`.
- Lane D: Task 7 after A/B/C land.

Execution order: launch A and B in parallel only if worktrees can reconcile `tests/architecture/test_process_naming_guardrail.py`; otherwise run A -> B -> C -> D sequentially. Task 8 always runs after all lanes merge.

Conflict flags: Task 3 and Task 5 both touch `tests/architecture/test_process_naming_guardrail.py`; coordinate or keep sequential.

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [x] **T1 (P1, human: ~3h / CC: ~25min)** — callback — 移除 callback legacy inbox delegate 并改为 RuntimeInbox authority
  - Surfaced by: Test/TODO review — 用户选择将 legacy delegate 清理纳入本计划。
  - Files: `src/app/callback/services/callback_orchestration_service.py`, `src/app/callback/services/callback_ingress_service.py`, `tests/callback/test_callback_runtime_inbox_authority.py`
  - Verify: `uv run pytest tests/callback/test_callback_runtime_inbox_authority.py -q`
- [x] **T2 (P1, human: ~45min / CC: ~10min)** — quality-gate — 用 runtime-production-closure 替换退役 readiness gate 的生产闭环覆盖
  - Surfaced by: Architecture review — 删除 readiness wrapper 会移除 mock production closure。
  - Files: `scripts/git-quality-gate.sh`, `tests/architecture/test_git_quality_gate_architecture_profile.py`
  - Verify: `uv run pytest tests/architecture/test_git_quality_gate_architecture_profile.py -q`
- [x] **T3 (P2, human: ~45min / CC: ~10min)** — tests — 迁移 mirror 测试中的长期 public-surface 断言
  - Surfaced by: Test review — 不能把关键导出合同降级成纯 import smoke。
  - Files: `tests/architecture/test_legacy_runtime_import_guardrail.py`
  - Verify: `uv run pytest tests/architecture/test_legacy_runtime_import_guardrail.py -q`
- [x] **T4 (P2, human: ~30min / CC: ~8min)** — docs — 同步 active architecture docs 的 retired gate/test 引用
  - Surfaced by: Architecture review — active docs 仍引用已删除脚本和旧测试名。
  - Files: `docs/architecture/file_index.md`, `docs/architecture/workline-and-plugin-restructuring.md`, `docs/architecture/legacy-cleanup-matrix.md`, `docs/architecture/legacy-cleanup-execution-plan.md`, `docs/architecture/phase3-phase4-production-evidence-bundle.md`
  - Verify: Task 7 `rg` reference cleanup command.
- [x] **T5 (P2, human: ~25min / CC: ~5min)** — tests — 用正则禁止 README 固化实时测试库存数量
  - Surfaced by: Test review — 禁止具体旧值不足以防止新固定数量再次过期。
  - Files: `tests/architecture/test_test_suite_topology_guardrail.py`, `tests/README.md`
  - Verify: `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q`
- [x] **T6 (P1, human: variable / CC: variable)** — typing — 修复全量 `uv run basedpyright .` 到通过
  - Surfaced by: Code/Test review — 用户选择 full basedpyright gate。
  - Files: any files required by basedpyright errors, kept within planned scope unless the error is pre-existing.
  - Verify: `uv run basedpyright .`

Task artifact: `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/tasks-eng-review-20260709-122246.jsonl`

### Completion Summary

- Step 0: Scope Challenge — full scope accepted; later expanded by user choice to include callback legacy delegate removal and full basedpyright gate.
- Architecture Review: 3 issues found, all folded into plan.
- Code Quality Review: 2 issues found, all folded into plan.
- Test Review: diagram produced, 3 gaps identified, all folded into plan.
- Performance Review: 0 issues found.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 1 item proposed; user chose build now instead of TODO.
- Failure modes: 0 critical gaps flagged.
- Outside voice: skipped.
- Parallelization: 4 lanes, A/B optionally parallel, C/D sequential.
- Lake Score: 7/8 recommendations chose the complete option; one staging-safety choice used guarded `git add -A`.

QA test plan artifact: `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/kaizhou-develop-eng-review-test-plan-20260709-122246.md`

---

## Self-Review Notes

- Spec coverage: 检查结论中的五类问题均有任务覆盖：README 过期基线 Task 6；readiness gate Task 2；mirror tests Task 4；callback cutover 与 legacy delegate 收束 Task 3；phase 文件名 Task 5；docs 同步 Task 7；最终验收 Task 8。
- Placeholder scan: 已扫描并避开 writing-plans skill 列出的禁用占位表达；每个修改步骤都给出文件、目标片段或命令。
- Type/name consistency: 新文件名在后续任务中统一使用 `test_callback_runtime_inbox_authority.py`、`test_workline_domain_boundary.py`、`test_legacy_matrix_contract.py`、`test_runtime_status_owner_guardrail.py`。

---

## Final Archive Note

本计划已执行完成并随 PR #83 合入 `develop`。保留本文档作为测试治理、callback RuntimeInbox authority 收束、quality gate 替换和 basedpyright 清理的决策/执行证据。

后续若继续清理测试目录，应新建独立计划；不要复用本计划追加无关 scope。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run for this plan | Not requested for backend cleanup plan |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not run | Outside voice skipped |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 8 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not applicable | Backend-only cleanup |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | Not needed for this cleanup |

- **VERDICT:** ENG CLEARED — ready to implement.
NO UNRESOLVED DECISIONS
