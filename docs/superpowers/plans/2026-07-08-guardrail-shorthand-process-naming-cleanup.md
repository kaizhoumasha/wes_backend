# Guardrail Shorthand Process Naming Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 active code、active gate、active tests 和当前架构文档中的 `C1`/`C2`/`C3`/`C4`/`C5`、`R-I3a`/`R-I3b`/`R-I3c`、`R-WLR`、`wlr` 这类重构过程缩写收敛为稳定业务/架构命名。

**Architecture:** 把旧主计划编号视为 guardrail 的历史实现细节，而不是长期公开 API。先扩展 process naming guardrail 让缩写残留可见，再把 architecture guardrails 的规则 ID、函数名、测试名、allowlist 数据和当前文档迁移到稳定名称；历史 spec、review、release log 和 immutable audit 字段保留审计事实。实现不得改变业务行为，只改变命名、测试断言和文档表述。

**Tech Stack:** Python 3.13, pytest, bash guardrail scripts, GitNexus impact/detect changes, `uv run ...`, ruff.

---

## Investigation Summary

本计划来自 2026-07-08 在 `develop@0c53e6f4` 上的扫描。命中集中在：

- `scripts/architecture-guardrails.sh`
- `scripts/architecture-guardrails.allowlist`
- `.githooks/pre-commit`
- `scripts/git-quality-gate.sh`
- `scripts/generate_legacy_matrix.py`
- `tests/architecture/test_c1_wms_import_guardrail.py`
- `tests/architecture/test_c2_cross_domain_fk_guardrail.py`
- `tests/architecture/test_c3_authority_metadata_guardrail.py`
- `tests/architecture/test_c3_response_schema_inventory.py`
- `tests/architecture/test_c4_device_command_fields_guardrail.py`
- `tests/architecture/test_c5_runtime_inbox_state_machine.py`
- `tests/architecture/test_ri3_capability_injection_guardrail.py`
- `tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py`
- `tests/architecture/test_wlr_import_guardrail.py`
- `src/app/callback/contracts/*.py`
- `src/app/contracts/external_contract_profile.py`
- `src/app/runtime/**` 和 `src/app/workline/**` 中的少量历史注释
- `docs/architecture/architecture-guardrails-spec.md`
- `docs/architecture/process-naming-policy.md`
- `docs/architecture/runtime-ownership-map.md`
- `docs/architecture/workline-and-plugin-restructuring.md`
- `docs/architecture/file_index.md`

`pyproject.toml` 中的 `C4` 是 Ruff / flake8-comprehensions 规则族，不属于本计划。

## Stable Naming Policy

| 旧过程缩写 | 稳定名称 | 用途 |
| --- | --- | --- |
| `C1` | `WMS_INTEGRATION_BOUNDARY` | 内部域不得 import WMS DTO/client/provider/implementation |
| `C2` | `EXECUTION_CORRELATION_BOUNDARY` | 跨域 session FK 收敛为 ExecutionCorrelation / correlation_id |
| `C3` | `AUTHORITY_METADATA_BOUNDARY` | 查询响应必须携带 scope/authority/source/evidence_at |
| `C4` | `DEVICE_COMMAND_BOUNDARY` | DeviceCommand 不包含 PLC/坐标/关节/安全回路字段 |
| `C5` | `RUNTIME_INBOX_STATE_MACHINE` | RuntimeInbox 状态机契约 |
| `R-I3a` | `CAPABILITY_FORBIDDEN_DEPENDENCY` | capability 不持有 HTTP client/service locator/provider exception |
| `R-I3b` | `CAPABILITY_IMPLEMENTATION_IMPORT` | capability 不 import wms_integration/device services/models 实现 |
| `R-I3c` | `INBOUND_NORMALIZER_OWNERSHIP` | inbound normalizer 仅由 RuntimeInboxConsumer 等合法入口持有 |
| `R-WLR` | `LEGACY_RUNTIME_IMPORT` | production code 不 import 已删除的 `src.workline_runtime` |
| `wlr` | `legacy runtime` 或完整路径 `src.workline_runtime` | 生产注释和当前文档中不再使用缩写别名 |

## Non-Goals

- 不修改业务逻辑、数据库 schema、API contract 或 runtime 行为。
- 不把 `pyproject.toml` 的 Ruff rule family `C4` 当作过程命名。
- 不重写 `docs/superpowers/specs/**`、`docs/superpowers/plans/2026-06-*`、`docs/architecture/reviews/**` 等历史材料。
- 不删除 `drop_phase` 字段；它是 legacy cleanup matrix 的审计字段，已被当前 process naming policy 明确允许。
- 不改 Alembic revision ID、历史 commit message、release history 中不可重写的事实。

## File Structure

### Guardrail Runtime

- `scripts/architecture-guardrails.sh`：把 rule function、usage、emit rule ID 从旧编号迁为稳定名称。
- `scripts/architecture-guardrails.allowlist`：把 active `rule_id` 列迁为稳定名称；历史 `drop_phase` 保留。
- `scripts/generate_legacy_matrix.py`：把生成的 seed 描述和 `legacy_entry_id` rule suffix 迁到稳定名称，避免重新生成时带回旧缩写。
- `.githooks/pre-commit`：更新 hook 注释，不再列旧缩写。
- `scripts/git-quality-gate.sh`：更新 quality gate 注释，不再列旧缩写。

### Guardrail Tests

- Rename `tests/architecture/test_c1_wms_import_guardrail.py` → `tests/architecture/test_wms_integration_boundary_guardrail.py`
- Rename `tests/architecture/test_c2_cross_domain_fk_guardrail.py` → `tests/architecture/test_execution_correlation_boundary_guardrail.py`
- Rename `tests/architecture/test_c3_authority_metadata_guardrail.py` → `tests/architecture/test_authority_metadata_boundary_guardrail.py`
- Rename `tests/architecture/test_c3_response_schema_inventory.py` → `tests/architecture/test_authority_response_schema_inventory.py`
- Rename `tests/architecture/test_c4_device_command_fields_guardrail.py` → `tests/architecture/test_device_command_boundary_guardrail.py`
- Rename `tests/architecture/test_c5_runtime_inbox_state_machine.py` → `tests/architecture/test_runtime_inbox_state_machine_guardrail.py`
- Rename `tests/architecture/test_ri3_capability_injection_guardrail.py` → `tests/architecture/test_capability_dependency_guardrail.py`
- Rename `tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py` → `tests/architecture/test_inbound_normalizer_ownership_guardrail.py`
- Rename `tests/architecture/test_wlr_import_guardrail.py` → `tests/architecture/test_legacy_runtime_import_guardrail.py`
- Modify `tests/architecture/test_process_naming_guardrail.py` to reject the old abbreviations in active surfaces.
- Modify `tests/README.md` to document the stable guardrail naming rule.

### Production Comments

- `src/app/callback/contracts/__init__.py`
- `src/app/callback/contracts/builder.py`
- `src/app/callback/contracts/codes.py`
- `src/app/callback/contracts/diagnostics.py`
- `src/app/callback/contracts/event_mapper.py`
- `src/app/callback/contracts/failure_mapper.py`
- `src/app/callback/contracts/models.py`
- `src/app/callback/contracts/registry.py`
- `src/app/callback/contracts/runtime_events.py`
- `src/app/callback/contracts/timeline_generator.py`
- `src/app/callback/contracts/trace_context.py`
- `src/app/contracts/__init__.py`
- `src/app/contracts/external_contract_profile.py`
- `src/app/runtime/normalization/classifiers/result_classifier.py`
- `src/app/runtime/normalization/contracts/normalized_external.py`
- `src/app/runtime/normalization/contracts/runtime_config.py`
- `src/app/runtime/orchestration/__init__.py`
- `src/app/runtime/orchestration/consumers/runtime_inbox_consumer.py`
- `src/app/runtime/orchestration/services/query/runtime_query_service.py`
- `src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py`
- `src/app/runtime/orchestration/services/session/session_resolver.py`
- `src/app/runtime/capabilities/material_flow/station_lease_service.py`
- `src/app/wms_integration/ports/document.py`
- `src/app/wms_integration/ports/master_data.py`
- `src/app/wms_integration/ports/reconciliation_query.py`
- `src/app/wms_integration/services/wms_event_normalizer.py`
- `src/app/workline/domain/plugin_manifest.py`
- `src/app/workline/runtime_services.py`
- `src/app/workline/services/diagnostic_service.py`
- `src/app/workline/utils.py`

### Current Docs

- `docs/architecture/process-naming-policy.md`
- `docs/architecture/architecture-guardrails-spec.md`
- `docs/architecture/runtime-ownership-map.md`
- `docs/architecture/workline-and-plugin-restructuring.md`
- `docs/architecture/file_index.md`

---

### Task 1: Lock The New Shorthand Naming Guardrail

**Files:**
- Modify: `tests/architecture/test_process_naming_guardrail.py`
- Modify: `tests/README.md`
- Test: `tests/architecture/test_process_naming_guardrail.py`

- [x] **Step 1: Add failing examples for shorthand process names**

Modify `test_process_naming_guardrail_rejects_stale_script_and_option_tokens` in `tests/architecture/test_process_naming_guardrail.py` and add these examples to the existing tuple:

```python
"tests/architecture/test_c3_authority_metadata_guardrail.py",
"tests/architecture/test_c4_device_command_fields_guardrail.py",
"tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py",
"tests/architecture/test_wlr_import_guardrail.py",
"rule_c3",
"rule_c4",
"rule_ri3b",
"rule_ri3c",
"rule_wlr_import",
"[C3] warning",
"[C4] violation",
"R-I3b seed allowlist",
"R-I3c inbound normalizer",
"R-WLR production import",
"wlr allowlist strict mode",
```

- [x] **Step 2: Add shorthand patterns**

Add new patterns to `PROCESS_NAME_PATTERNS`:

```python
("guardrail rule id shorthand", re.compile(r"(?<![A-Za-z0-9_])(?:C[1-5][a-z]?|R-I3[a-c]?|R-WLR)(?![A-Za-z0-9_])")),
("guardrail function shorthand", re.compile(r"\brule_(?:c[1-5]|ri3[a-c]?|wlr)(?:_|$)", re.IGNORECASE)),
("legacy runtime shorthand alias", re.compile(r"(?<![A-Za-z0-9_])wlr(?![A-Za-z0-9_])", re.IGNORECASE)),
```

Do not include `pyproject.toml` in `SCAN_ROOTS`; `C4` there is a Ruff rule family and must remain out of scope.

- [x] **Step 3: Run the guardrail and verify it fails**

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Expected:

```text
FAILED tests/architecture/test_process_naming_guardrail.py::test_active_code_does_not_use_process_phase_names
```

The failure must include examples from `scripts/architecture-guardrails.sh` and renamed test files. Keep this red proof uncommitted until cleanup is green.

- [x] **Step 4: Document the shorthand policy**

Add one bullet under `tests/README.md` “当前治理约束”:

```markdown
- Active guardrail IDs, test filenames, script functions, and production comments must use stable domain names such as `AUTHORITY_METADATA_BOUNDARY`, `DEVICE_COMMAND_BOUNDARY`, `CAPABILITY_IMPLEMENTATION_IMPORT`, `INBOUND_NORMALIZER_OWNERSHIP`, and `LEGACY_RUNTIME_IMPORT`; old restructuring shorthand like `C3`, `C4`, `R-I3c`, `R-WLR`, or `wlr` is only allowed in historical records.
```

- [x] **Step 5: Record the red baseline only as local proof**

Do not commit this failing state. Record the command, exit code, and first 20 offenders in the implementation notes, then continue to Task 2.

---

### Task 2: Atomically Rename Architecture Guardrail Runtime IDs And Tests

**Review decision:** Eng review D2 chose the single-PR atomic migration path. Runtime rule IDs, allowlist keys, generator labels, test filenames, and test assertions must move together in one green commit. Do not commit after changing only the bash script or allowlist; `rule_id` is an allowlist matching key and intermediate red commits make the guardrail signal ambiguous.

**Files:**
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `scripts/architecture-guardrails.allowlist`
- Modify: `scripts/generate_legacy_matrix.py`
- Modify: `.githooks/pre-commit`
- Modify: `scripts/git-quality-gate.sh`
- Move: all old shorthand architecture guardrail test files listed in File Structure
- Modify: `tests/architecture/test_git_quality_gate_architecture_profile.py`
- Modify: `tests/architecture/test_phase0_legacy_matrix_contract.py`
- Modify: `tests/architecture/test_cleanup_matrix_guardrail.py`
- Modify: `tests/architecture/test_process_naming_guardrail.py`
- Test: `tests/architecture/test_*guardrail.py`

- [x] **Step 1: Run impact checks before editing scripts**

Run GitNexus impact analysis for script-facing helpers if available:

```bash
uv run python - <<'PY'
print("Manual checkpoint: script-only rename. No Python production symbol is being changed in this task.")
PY
```

Then record this blast radius in the implementation notes:

```text
Affected surface: bash guardrail output IDs, allowlist rule_id column, architecture tests, docs.
Business runtime behavior: none.
```

- [x] **Step 2: Introduce stable rule ID constants in the bash script**

At the top of `scripts/architecture-guardrails.sh`, after `REPO_ROOT=""`, add stable ID constants:

```bash
RULE_WMS_INTEGRATION_BOUNDARY="WMS_INTEGRATION_BOUNDARY"
RULE_EXECUTION_CORRELATION_BOUNDARY="EXECUTION_CORRELATION_BOUNDARY"
RULE_AUTHORITY_METADATA_BOUNDARY="AUTHORITY_METADATA_BOUNDARY"
RULE_DEVICE_COMMAND_BOUNDARY="DEVICE_COMMAND_BOUNDARY"
RULE_RUNTIME_INBOX_STATE_MACHINE="RUNTIME_INBOX_STATE_MACHINE"
RULE_CAPABILITY_FORBIDDEN_DEPENDENCY="CAPABILITY_FORBIDDEN_DEPENDENCY"
RULE_CAPABILITY_IMPLEMENTATION_IMPORT="CAPABILITY_IMPLEMENTATION_IMPORT"
RULE_INBOUND_NORMALIZER_OWNERSHIP="INBOUND_NORMALIZER_OWNERSHIP"
RULE_LEGACY_RUNTIME_IMPORT="LEGACY_RUNTIME_IMPORT"
```

- [x] **Step 3: Rename bash rule functions**

Use focused edits in `scripts/architecture-guardrails.sh`:

| Current | Target |
| --- | --- |
| `rule_c1` | `rule_wms_integration_boundary` |
| `rule_c2` | `rule_execution_correlation_boundary` |
| `rule_c3` | `rule_authority_metadata_boundary` |
| `rule_c4` | `rule_device_command_boundary` |
| `rule_ri3a` | `rule_capability_forbidden_dependency` |
| `rule_ri3b` | `rule_capability_implementation_import` |
| `rule_ri3c` | `rule_inbound_normalizer_ownership` |
| `rule_wlr_import` | `rule_legacy_runtime_import` |

Update the call chain at the bottom to call the new names.

- [x] **Step 4: Replace emitted rule IDs**

In each `emit_violation` call, replace the old literal with the stable constant:

```bash
emit_violation "$RULE_AUTHORITY_METADATA_BOUNDARY" "$file" "$line" ...
emit_violation "$RULE_DEVICE_COMMAND_BOUNDARY" "$file" "$line" ...
emit_violation "$RULE_CAPABILITY_IMPLEMENTATION_IMPORT" "$file" "$line" ...
emit_violation "$RULE_INBOUND_NORMALIZER_OWNERSHIP" "$file" "$line" ...
emit_violation "$RULE_LEGACY_RUNTIME_IMPORT" "$file" "$line" ...
```

For the authority metadata warning, replace:

```bash
echo "[C3] warning: 未发现 AuthorityMetadata 校验点 (由 tests/architecture 覆盖)" >&2
```

with:

```bash
echo "[$RULE_AUTHORITY_METADATA_BOUNDARY] warning: 未发现 AuthorityMetadata 校验点 (由 tests/architecture 覆盖)" >&2
```

- [x] **Step 5: Update allowlist prefix matching**

In `is_allowlisted`, replace old special cases:

```bash
if [[ "$rule" == "R-I3b" || "$rule" == "R-I3c" ]]; then
```

with:

```bash
if [[ "$rule" == "$RULE_CAPABILITY_IMPLEMENTATION_IMPORT" || "$rule" == "$RULE_INBOUND_NORMALIZER_OWNERSHIP" ]]; then
```

Do the same in `validate_allowlist` for directory-prefix rejection.

- [x] **Step 6: Migrate allowlist rule IDs**

In `scripts/architecture-guardrails.allowlist`, update the `rule_id` column:

| Current prefix | Target prefix |
| --- | --- |
| `C1|` | `WMS_INTEGRATION_BOUNDARY|` |
| `C2|` | `EXECUTION_CORRELATION_BOUNDARY|` |
| `C3|` | `AUTHORITY_METADATA_BOUNDARY|` |
| `C4|` | `DEVICE_COMMAND_BOUNDARY|` |
| `R-I3a|` | `CAPABILITY_FORBIDDEN_DEPENDENCY|` |
| `R-I3b|` | `CAPABILITY_IMPLEMENTATION_IMPORT|` |
| `R-I3c|` | `INBOUND_NORMALIZER_OWNERSHIP|` |
| `R-WLR|` | `LEGACY_RUNTIME_IMPORT|` |

Keep `drop_phase` values unchanged.

- [x] **Step 7: Update legacy matrix generator labels**

In `scripts/generate_legacy_matrix.py`, replace generated text and suffixes:

```text
R-I3b seed → CAPABILITY_IMPLEMENTATION_IMPORT seed
#R-I3b → #CAPABILITY_IMPLEMENTATION_IMPORT
```

Do not change matrix business entry IDs unrelated to guardrail rule suffixes. For IDs that intentionally include a migrated guardrail suffix, update both the generated CSV/Markdown and the tests that consume those exact IDs.

- [x] **Step 8: Regenerate legacy matrix if the generator is the source of truth**

Run:

```bash
uv run python scripts/generate_legacy_matrix.py
```

Expected:

```text
docs/architecture/legacy-cleanup-matrix.csv updated
docs/architecture/legacy-cleanup-matrix.md updated
```

If the script prints a different output shape, record the exact output in implementation notes and inspect both generated files before staging.

- [x] **Step 9: Update hook and quality gate comments**

Replace comments in `.githooks/pre-commit` and `scripts/git-quality-gate.sh`:

```text
默认以 enforced 模式触发 stable architecture guardrails。
```

Do not mention old IDs in these comments.

- [x] **Step 10: Move test files to stable names**

Run:

```bash
git mv tests/architecture/test_c1_wms_import_guardrail.py tests/architecture/test_wms_integration_boundary_guardrail.py
git mv tests/architecture/test_c2_cross_domain_fk_guardrail.py tests/architecture/test_execution_correlation_boundary_guardrail.py
git mv tests/architecture/test_c3_authority_metadata_guardrail.py tests/architecture/test_authority_metadata_boundary_guardrail.py
git mv tests/architecture/test_c3_response_schema_inventory.py tests/architecture/test_authority_response_schema_inventory.py
git mv tests/architecture/test_c4_device_command_fields_guardrail.py tests/architecture/test_device_command_boundary_guardrail.py
git mv tests/architecture/test_c5_runtime_inbox_state_machine.py tests/architecture/test_runtime_inbox_state_machine_guardrail.py
git mv tests/architecture/test_ri3_capability_injection_guardrail.py tests/architecture/test_capability_dependency_guardrail.py
git mv tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py tests/architecture/test_inbound_normalizer_ownership_guardrail.py
git mv tests/architecture/test_wlr_import_guardrail.py tests/architecture/test_legacy_runtime_import_guardrail.py
```

- [x] **Step 11: Update test assertions, docstrings, and split markers**

In the renamed test files:

- Replace `C1` with `WMS integration boundary`.
- Replace `C2` with `execution correlation boundary`.
- Replace `C3` with `authority metadata boundary`.
- Replace `C4` with `device command boundary`.
- Replace `C5` with `RuntimeInbox state machine`.
- Replace `R-I3a/R-I3b` with `capability dependency boundary` or the specific stable rule ID.
- Replace `R-I3c` with `inbound normalizer ownership`.
- Replace `R-WLR` and `wlr` with `legacy runtime import`.
- Where tests split `scripts/architecture-guardrails.sh` by comment headers, update markers to the exact stable headers implemented in this task.
- In `tests/architecture/test_git_quality_gate_architecture_profile.py`, keep the process naming check assertion stable:
- In `tests/architecture/test_phase0_legacy_matrix_contract.py`, update exact `#R-I3b` entry IDs and `R-I3b seed` business semantic assertions to the stable `#CAPABILITY_IMPLEMENTATION_IMPORT` / `CAPABILITY_IMPLEMENTATION_IMPORT seed` values emitted by the regenerated matrix.
- In `tests/architecture/test_cleanup_matrix_guardrail.py`, keep the allowlist-to-matrix reverse reference test and ensure it passes after both allowlist `legacy_entry_id` values and generated matrix `entry_id` values move together.

```python
assert "[process-naming] pytest tests/architecture/test_process_naming_guardrail.py -q" in result.stdout
```

Example assertion update:

```python
assert "rule_authority_metadata_boundary" in content
assert "AUTHORITY_METADATA_BOUNDARY" in result.stderr
```

- [x] **Step 12: Run architecture guardrails and renamed tests**

Run:

```bash
bash scripts/architecture-guardrails.sh --mode enforced
uv run pytest \
  tests/architecture/test_wms_integration_boundary_guardrail.py \
  tests/architecture/test_execution_correlation_boundary_guardrail.py \
  tests/architecture/test_authority_metadata_boundary_guardrail.py \
  tests/architecture/test_authority_response_schema_inventory.py \
  tests/architecture/test_device_command_boundary_guardrail.py \
  tests/architecture/test_runtime_inbox_state_machine_guardrail.py \
  tests/architecture/test_capability_dependency_guardrail.py \
  tests/architecture/test_inbound_normalizer_ownership_guardrail.py \
  tests/architecture/test_legacy_runtime_import_guardrail.py \
  tests/architecture/test_git_quality_gate_architecture_profile.py \
  tests/architecture/test_phase0_legacy_matrix_contract.py \
  tests/architecture/test_cleanup_matrix_guardrail.py \
  -q
```

Expected:

```text
enforced: 全部违规已被 allowlist 覆盖或无违规, 退出码 0
passed
```

- [x] **Step 13: Verify old test paths are gone**

Run:

```bash
for path in \
  tests/architecture/test_c1_wms_import_guardrail.py \
  tests/architecture/test_c2_cross_domain_fk_guardrail.py \
  tests/architecture/test_c3_authority_metadata_guardrail.py \
  tests/architecture/test_c3_response_schema_inventory.py \
  tests/architecture/test_c4_device_command_fields_guardrail.py \
  tests/architecture/test_c5_runtime_inbox_state_machine.py \
  tests/architecture/test_ri3_capability_injection_guardrail.py \
  tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py \
  tests/architecture/test_wlr_import_guardrail.py; do
    test ! -e "$path" || { echo "stale path: $path"; exit 1; }
  done
```

Expected: no output.

- [x] **Step 14: Commit atomic guardrail migration**

Run:

```bash
git add scripts/architecture-guardrails.sh scripts/architecture-guardrails.allowlist scripts/generate_legacy_matrix.py .githooks/pre-commit scripts/git-quality-gate.sh docs/architecture/legacy-cleanup-matrix.csv docs/architecture/legacy-cleanup-matrix.md tests/architecture
git commit -m "refactor(guardrails,tests): 稳定化架构护栏命名"
```

---

### Task 3: Clean Production Comments Without Changing Behavior

**Files:**
- Modify: `src/app/callback/contracts/*.py`
- Modify: `src/app/contracts/*.py`
- Modify: `src/app/runtime/**/*.py`
- Modify: `src/app/wms_integration/**/*.py`
- Modify: `src/app/workline/**/*.py`
- Test: focused imports and existing mirror/contract tests

- [x] **Step 1: Generate production residual list**

Run:

```bash
rg -n '(^|[^A-Za-z0-9_])(C[1-5][a-z]?|R-I3[a-c]?|R-WLR|wlr|WLR)([^A-Za-z0-9_]|$)|rule_(c[1-5]|ri3[a-c]?|wlr)' src/app
```

Expected: list of comments/docstrings only. If executable code identifiers appear, stop and inspect before editing.

- [x] **Step 2: Replace callback contract `wlr` comments**

In `src/app/callback/contracts/*.py`, replace shorthand examples as follows:

| Current wording | Stable wording |
| --- | --- |
| `wlr.diagnostics.builder 镜像` | `legacy runtime diagnostics builder 行为镜像` |
| `与 wlr... 行为一致` | `与旧 runtime 实现的公开行为一致` |
| `不使用 wlr.utils` | `不依赖旧 runtime utils` |
| `避免反向依赖 wlr` | `避免反向依赖旧 runtime 包` |

Keep function/class names unchanged.

- [x] **Step 3: Replace capability boundary comments**

In `src/app/contracts/external_contract_profile.py`, `src/app/contracts/__init__.py`, `src/app/wms_integration/ports/*.py`, and `src/app/wms_integration/services/wms_event_normalizer.py`, replace old rule IDs:

| Current wording | Stable wording |
| --- | --- |
| `R-I3b` | `capability implementation import boundary` |
| `R-I3c` | `inbound normalizer ownership boundary` |
| `R-I3a/R-I3b/R-I3c` | `capability dependency and inbound normalizer ownership guardrails` |
| `C1/R-I3b` | `WMS integration and capability implementation import boundaries` |

- [x] **Step 4: Replace runtime/workline migration comments**

Use stable wording in runtime/workline comments:

| File | Current | Target |
| --- | --- | --- |
| `src/app/runtime/normalization/classifiers/result_classifier.py` | `wlr 目录删除后...` | `旧 runtime 包删除后...` |
| `src/app/runtime/normalization/contracts/normalized_external.py` | `wlr 目录删除后...` | `旧 runtime 包删除后...` |
| `src/app/runtime/normalization/contracts/runtime_config.py` | `C4 src.app.workline.domain.run_mode` | `stable workline run_mode mirror` |
| `src/app/runtime/orchestration/services/session/session_resolver.py` | `C5a business_identity_bridge + C4 run_mode` | `business identity bridge + stable workline run_mode` |
| `src/app/runtime/capabilities/material_flow/station_lease_service.py` | `C4d 范围内保留原地` | `stable rack-position service boundary 内保留原地` |
| `src/app/workline/domain/plugin_manifest.py` | `C3 已 defer...C5a...` | `plugin manifest mirror follows runtime events mirror availability` |
| `src/app/workline/runtime_services.py` | `(C4)` | `stable run-mode mirror` |
| `src/app/workline/utils.py` | `wlr` / `R-WLR` | `legacy runtime` / `legacy runtime import boundary` |

- [x] **Step 5: Run focused import and mirror tests**

Run:

```bash
uv run pytest \
  tests/callback/test_callback_mirror_integration.py \
  tests/architecture/test_orchestration_bridges_mirror.py \
  tests/contracts/workline/test_callback_runtime_contracts.py \
  tests/runtime/orchestration/test_runtime_inbox_consumer.py \
  -q
```

Expected:

```text
passed
```

- [x] **Step 6: Verify production comments are clean**

Run:

```bash
rg -n '(^|[^A-Za-z0-9_])(C[1-5][a-z]?|R-I3[a-c]?|R-WLR|wlr|WLR)([^A-Za-z0-9_]|$)|rule_(c[1-5]|ri3[a-c]?|wlr)' src/app
```

Expected: no output.

- [x] **Step 7: Commit production comment cleanup**

Run:

```bash
git add src/app
git commit -m "refactor(runtime): 清理生产注释中的过程缩写"
```

---

### Task 4: Update Current Architecture Documentation

**Files:**
- Modify: `docs/architecture/process-naming-policy.md`
- Modify: `docs/architecture/architecture-guardrails-spec.md`
- Modify: `docs/architecture/runtime-ownership-map.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/file_index.md`
- Test: doc scans

- [x] **Step 1: Update naming policy**

In `docs/architecture/process-naming-policy.md`, extend the forbidden examples:

```markdown
Active production code, active gates, default regression tests, and current architecture docs must not use old restructuring shorthand such as `C3`, `C4`, `R-I3c`, `R-WLR`, or `wlr` for new/current concepts. Use stable names like `AUTHORITY_METADATA_BOUNDARY`, `DEVICE_COMMAND_BOUNDARY`, `INBOUND_NORMALIZER_OWNERSHIP`, and `LEGACY_RUNTIME_IMPORT`.
```

- [x] **Step 2: Rewrite architecture guardrails spec**

In `docs/architecture/architecture-guardrails-spec.md`, replace the core mapping table IDs with stable names. Keep the business invariant text, but the `ID` column must use stable names:

```markdown
| WMS_INTEGRATION_BOUNDARY | 内部域不得 import WMS DTO/client/provider | ... |
| EXECUTION_CORRELATION_BOUNDARY | 跨域 session FK 收敛为 `ExecutionCorrelation` | ... |
| AUTHORITY_METADATA_BOUNDARY | 查询响应强制 `scope/authority/source/evidence_at` | ... |
| DEVICE_COMMAND_BOUNDARY | DeviceCommand 不含 PLC/坐标/关节/安全回路字段 | ... |
| RUNTIME_INBOX_STATE_MACHINE | RuntimeInbox 状态机契约 | ... |
```

Update the capability boundary section to use:

```markdown
CAPABILITY_FORBIDDEN_DEPENDENCY
CAPABILITY_IMPLEMENTATION_IMPORT
INBOUND_NORMALIZER_OWNERSHIP
```

- [x] **Step 3: Update runtime ownership map**

In `docs/architecture/runtime-ownership-map.md`:

- Replace `主计划 §C2` with `execution correlation boundary`.
- Replace `R-I3c guardrail` with `inbound normalizer ownership guardrail`.
- Replace `wlr allowlist 严格型` with `legacy runtime import boundary`.
- Replace old verification commands with the current stable test names.

- [x] **Step 4: Update main restructuring doc active sections**

In `docs/architecture/workline-and-plugin-restructuring.md`, update current active sections around the invariant table and verification checklist:

| Current | Target |
| --- | --- |
| `C1` | `WMS_INTEGRATION_BOUNDARY` |
| `C2` | `EXECUTION_CORRELATION_BOUNDARY` |
| `C3` | `AUTHORITY_METADATA_BOUNDARY` |
| `C4` | `DEVICE_COMMAND_BOUNDARY` |
| `C5` | `RUNTIME_INBOX_STATE_MACHINE` |
| `R-I3b` | `CAPABILITY_IMPLEMENTATION_IMPORT` |
| `R-I3c` | `INBOUND_NORMALIZER_OWNERSHIP` |

Do not rewrite the external review appendix where `C1-C3 / M1-M10` refers to historical reviewer labels.

- [x] **Step 5: Update active index entries in file index**

In `docs/architecture/file_index.md`, update active module index rows so current modules do not describe themselves as `C4`, `C5a`, `wlr`, or `R-WLR`. Release-history rows at the top may preserve historical facts.

Examples:

| Current | Target |
| --- | --- |
| `wlr 平级镜像` | `legacy runtime behavior mirror` |
| `R-WLR ... 守卫终态` | `legacy runtime import boundary 守卫终态` |
| `C5a business_identity` | `business identity bridge` |

- [x] **Step 6: Run active doc scan**

Run:

```bash
rg -n '(^|[^A-Za-z0-9_])(C[1-5][a-z]?|R-I3[a-c]?|R-WLR|wlr|WLR)([^A-Za-z0-9_]|$)' \
  docs/architecture/process-naming-policy.md \
  docs/architecture/architecture-guardrails-spec.md \
  docs/architecture/runtime-ownership-map.md \
  docs/architecture/workline-and-plugin-restructuring.md \
  docs/architecture/file_index.md
```

Expected: only explicitly documented historical exceptions, release-history rows, or forbidden examples inside `process-naming-policy.md`.

- [x] **Step 7: Commit docs cleanup**

Run:

```bash
git add docs/architecture
git commit -m "docs(architecture): 稳定化架构护栏命名"
```

---

### Task 5: Tighten Final Guardrails And Quality Gates

**Files:**
- Modify: `tests/architecture/test_process_naming_guardrail.py`
- Modify: `tests/architecture/test_test_suite_topology_guardrail.py` if renamed tests affect topology expectations
- Modify: `tests/README.md` if final wording differs from Task 1
- Guarded docs: `docs/architecture/process-naming-policy.md`, `docs/architecture/architecture-guardrails-spec.md`, `docs/architecture/runtime-ownership-map.md`, `docs/architecture/workline-and-plugin-restructuring.md`, `docs/architecture/file_index.md`
- Test: process naming, architecture, collect-only, full quality profile

- [x] **Step 1: Add persistent current-doc process naming coverage**

In `tests/architecture/test_process_naming_guardrail.py`, add a targeted current-architecture-doc scan. Do not add all of `docs/architecture` to `SCAN_ROOTS`; that would mix active docs with ADRs, release history, reviews, and other historical records. Use an explicit current-doc path list matching this plan:

```text
docs/architecture/process-naming-policy.md
docs/architecture/architecture-guardrails-spec.md
docs/architecture/runtime-ownership-map.md
docs/architecture/workline-and-plugin-restructuring.md
docs/architecture/file_index.md
```

The doc scan must allow only intentional historical facts:

- Forbidden examples inside `docs/architecture/process-naming-policy.md`.
- Release-history rows in `docs/architecture/file_index.md`.
- Historical reviewer labels inside explicitly historical appendices of `docs/architecture/workline-and-plugin-restructuring.md`.

Add assertions that:

- The current architecture doc list is scanned by the guardrail.
- Historical docs under `docs/superpowers/**`, `docs/architecture/reviews/**`, and ADR/release-history material outside the active list are not pulled in by broad directory scanning.
- A synthetic current-doc line like `R-I3c inbound normalizer` is rejected.
- A synthetic release-history line explicitly marked as historical is allowed only by the documented exception path.

- [x] **Step 2: Confirm process naming guardrail scans the renamed files**

In `tests/architecture/test_process_naming_guardrail.py`, add assertions:

```python
def test_process_naming_guardrail_rejects_guardrail_shorthand_examples() -> None:
    examples = (
        "tests/architecture/test_c3_authority_metadata_guardrail.py",
        "rule_c4",
        "R-I3c inbound normalizer",
        "R-WLR import guardrail",
        "wlr mirror",
    )
    for example in examples:
        assert any(pattern.search(example) for _, pattern in PROCESS_NAME_PATTERNS), example
```

- [x] **Step 3: Run process naming guardrail**

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```

Expected:

```text
passed
```

- [x] **Step 4: Run renamed guardrail test suite**

Run:

```bash
uv run pytest tests/architecture -q
```

Expected:

```text
passed
```

- [x] **Step 5: Run default collection topology**

Run:

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected:

```text
test_test_suite_topology_guardrail.py ... passed
tests collected
```

- [x] **Step 6: Run quality gate**

Run:

```bash
./scripts/git-quality-gate.sh --profile quality
```

Expected:

```text
[process-naming] pytest tests/architecture/test_process_naming_guardrail.py -q
[architecture] architecture-guardrails.sh --mode enforced
```

The command must exit 0.

- [x] **Step 7: Run GitNexus detect changes before final commit**

Run the GitNexus detect-changes tool or the configured CLI equivalent. Required note:

```text
Expected scope: guardrail scripts, architecture guardrail tests, production comments, current docs.
Unexpected scope: business service behavior, database migrations, API response models.
```

If detect-changes reports HIGH/CRITICAL runtime behavior risk, stop and investigate before committing.

- [x] **Step 8: Commit final guardrail integration**

Run:

```bash
git add tests/README.md tests/architecture scripts .githooks docs/architecture src/app
git commit -m "test(architecture): 防止过程缩写命名回归"
```

---

## Final Verification

Run:

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
uv run pytest tests/architecture -q
uv run pytest --collect-only -q -o addopts='' | tail -5
./scripts/git-quality-gate.sh --profile quality
git diff --check
```

Expected:

```text
process naming guardrail passed
architecture tests passed
collect-only completed
quality gate exited 0
git diff --check has no output
```

Optional diagnostic scan:

```bash
rg -n '(^|[^A-Za-z0-9_])(C[1-5][a-z]?|R-I3[a-c]?|R-WLR|wlr|WLR)([^A-Za-z0-9_]|$)|rule_(c[1-5]|ri3[a-c]?|wlr)' \
  --glob '!tests/architecture/test_process_naming_guardrail.py' \
  src scripts tests .githooks Jenkinsfile Jenkinsfile.* docs/architecture/process-naming-policy.md docs/architecture/architecture-guardrails-spec.md docs/architecture/runtime-ownership-map.md docs/architecture/workline-and-plugin-restructuring.md docs/architecture/file_index.md
```

This command is diagnostic only. The authoritative pass/fail signal is `uv run pytest tests/architecture/test_process_naming_guardrail.py -q`, because that test owns the intentional examples and current-doc historical exceptions. Diagnostic output is acceptable only when each hit is already covered by the guardrail's explicit exception path, such as forbidden examples in `docs/architecture/process-naming-policy.md` or release-history rows documented in `docs/architecture/file_index.md`.

## Review Checklist

- The old rule IDs no longer appear in active script function names or emitted rule IDs.
- `scripts/architecture-guardrails.allowlist` uses stable `rule_id` values.
- `drop_phase` remains intact as an audit field.
- Test filenames use stable names.
- Production comments do not mention `wlr` as a shorthand alias.
- Current docs describe stable guardrail names.
- Historical docs/specs/reviews are not rewritten.
- `pyproject.toml` Ruff `C4` remains unchanged.

## Engineering Review Notes

### What Already Exists

- `tests/architecture/test_process_naming_guardrail.py` already owns process-stage naming enforcement for active code, scripts, tests, and CI files. This plan extends that existing guardrail instead of creating a parallel scanner.
- `scripts/architecture-guardrails.sh` already owns guardrail emission, allowlist matching, expiry validation, and enforced-mode exit behavior. Stable rule IDs must migrate in place so existing quality gates keep working.
- `scripts/architecture-guardrails.allowlist`, `docs/architecture/legacy-cleanup-matrix.csv`, and `tests/architecture/test_cleanup_matrix_guardrail.py` already form the allowlist-to-audit trace loop. The plan reuses that loop and updates its exact IDs atomically.
- `scripts/generate_legacy_matrix.py` is the source used to regenerate matrix CSV/Markdown seed rows. The implementation should update the generator first, regenerate outputs, then update tests that assert generated values.
- `./scripts/git-quality-gate.sh --profile quality` already invokes the process naming and architecture guardrails. The final quality gate stays the integration point rather than adding a new top-level command.

### NOT In Scope

- Business behavior, database schema, API contracts, Celery behavior, and runtime orchestration semantics are not changed; this is a naming and guardrail refactor.
- Historical specs, archived plans, review artifacts, migration filenames, commit messages, release-history facts, and immutable audit fields are not rewritten.
- `pyproject.toml` Ruff rule family `C4` is not process naming and remains untouched.
- Broad `docs/architecture` directory enforcement is not introduced. Only the current active architecture docs listed in Task 5 receive persistent process naming coverage.
- No new artifact type, package, CLI, container image, or deployment pipeline is introduced.

### Test Coverage Diagram

```text
CODE / GATE PATHS                                      VALIDATION
[+] process naming guardrail
  |-- [*** PLANNED] rejects shorthand examples          test_process_naming_guardrail.py
  |-- [*** PLANNED] scans active code/scripts/tests     test_process_naming_guardrail.py
  |-- [*** PLANNED] scans current architecture docs     Task 5 targeted doc list
  |-- [*** PLANNED] keeps historical exceptions narrow  policy examples + release rows only

[+] architecture guardrail runtime
  |-- [*** PLANNED] emits stable rule IDs               renamed guardrail tests
  |-- [*** PLANNED] matches migrated allowlist keys     architecture-guardrails.sh --mode enforced
  |-- [*** PLANNED] rejects broad capability allowlist  capability + inbound normalizer tests
  |-- [*** PLANNED] keeps legacy runtime import guard   legacy runtime import guardrail test

[+] legacy cleanup matrix loop
  |-- [*** PLANNED] generator emits stable suffixes     generate_legacy_matrix.py
  |-- [*** PLANNED] allowlist references CSV IDs        test_cleanup_matrix_guardrail.py
  |-- [*** PLANNED] matrix contract asserts target port test_phase0_legacy_matrix_contract.py

[+] production comments and docs
  |-- [*** PLANNED] src/app shorthand scan is clean     Task 3 residual scan
  |-- [*** PLANNED] current docs use stable names       Task 4 doc scan + Task 5 guardrail
  |-- [*** PLANNED] final quality gate stays green      git-quality-gate quality profile

COVERAGE: 14/14 planned validation paths covered.
CRITICAL GAPS: 0 after D2-D6 changes.
```

### Failure Modes

| Failure mode | Covered by | User signal |
| --- | --- | --- |
| Script emits stable IDs but allowlist still uses old IDs | Task 2 `architecture-guardrails.sh --mode enforced` | Non-zero guardrail exit with unmatched violations |
| Generator changes matrix IDs but tests still assert old `#R-I3b` values | Task 2 matrix consumer tests | Pytest failure in matrix contract or allowlist reverse-reference test |
| Current architecture docs reintroduce shorthand after cleanup | Task 5 targeted doc process naming coverage | Process naming pytest failure with offending file and line |
| Production comments include executable shorthand identifiers | Task 3 residual scan and focused tests | Implementation stops for inspection before editing behavior |
| Final diagnostic `rg` reports guardrail's own forbidden examples | D6 diagnostic-only wording plus explicit test-file exclusion | No false release blocker; authoritative pytest remains clean |

No failure mode remains with no test, no error handling, and silent user impact. This cleanup does not touch user-facing runtime paths.

### Worktree Parallelization Strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Task 1: shorthand guardrail red baseline | `tests/architecture`, `tests/README.md` | - |
| Task 2: atomic guardrail ID/test/matrix migration | `scripts`, `.githooks`, `tests/architecture`, `docs/architecture/legacy-cleanup-matrix.*` | Task 1 |
| Task 3: production comment cleanup | `src/app` | Task 1 |
| Task 4: current docs cleanup | `docs/architecture` | Task 1 |
| Task 5: final guardrail integration | `tests/architecture`, `tests/README.md`, quality gates | Tasks 2-4 |

Parallel lanes:

- Lane A: Task 1 -> Task 2, sequential because Task 2 depends on the new shorthand guardrail and touches the guardrail tests.
- Lane B: Task 3, independent after Task 1 because it only changes `src/app` comments.
- Lane C: Task 4, independent after Task 1 but may lightly conflict with Task 2 if both regenerate or edit files under `docs/architecture`.
- Final lane: Task 5 after lanes A, B, and C merge.

Recommended execution: run Task 1 first, then launch Task 2, Task 3, and Task 4 in separate worktrees if parallel speed matters. Merge all three, then run Task 5 and Final Verification in one clean worktree.

### Implementation Tasks

Synthesized from the engineering review findings. Each task derives from a specific finding above.

- [x] **T1 (P1, human: ~45min / CC: ~8min)** - Guardrails - Migrate runtime IDs, allowlist keys, and guardrail tests atomically.
  - Surfaced by: Architecture Finding 1, D2.
  - Files: `scripts/architecture-guardrails.sh`, `scripts/architecture-guardrails.allowlist`, `.githooks/pre-commit`, `scripts/git-quality-gate.sh`, `tests/architecture`.
  - Verify: `bash scripts/architecture-guardrails.sh --mode enforced` and renamed guardrail pytest command in Task 2.

- [x] **T2 (P1, human: ~25min / CC: ~5min)** - Legacy Matrix - Update generated matrix suffixes and exact consumer tests in the same atomic change.
  - Surfaced by: Architecture Finding 2, D3.
  - Files: `scripts/generate_legacy_matrix.py`, `docs/architecture/legacy-cleanup-matrix.csv`, `docs/architecture/legacy-cleanup-matrix.md`, `tests/architecture/test_phase0_legacy_matrix_contract.py`, `tests/architecture/test_cleanup_matrix_guardrail.py`.
  - Verify: `uv run pytest tests/architecture/test_phase0_legacy_matrix_contract.py tests/architecture/test_cleanup_matrix_guardrail.py -q`.

- [x] **T3 (P2, human: ~45min / CC: ~8min)** - Process Naming - Add persistent current-architecture-doc coverage with narrow historical exceptions.
  - Surfaced by: Architecture Finding 3, D4.
  - Files: `tests/architecture/test_process_naming_guardrail.py`, `docs/architecture/process-naming-policy.md`, `tests/README.md`.
  - Verify: `uv run pytest tests/architecture/test_process_naming_guardrail.py -q`.

- [x] **T4 (P2, human: ~2min / CC: ~1min)** - TDD Evidence - Record red baseline evidence without committing a failing state.
  - Surfaced by: Code Quality Finding 1, D5.
  - Files: implementation notes only.
  - Verify: first red run command, exit code, and first 20 offenders are recorded before Task 2.

- [x] **T5 (P2, human: ~10min / CC: ~2min)** - Final Verification - Make process naming pytest the authoritative final signal and keep raw `rg` diagnostic-only.
  - Surfaced by: Test Finding 1, D6.
  - Files: `docs/superpowers/plans/2026-07-08-guardrail-shorthand-process-naming-cleanup.md`.
  - Verify: Final Verification section runs process naming pytest before optional diagnostic scan.

### Completion Summary

- Step 0: Scope Challenge - scope accepted as-is after user chose single-PR path.
- Architecture Review: 3 issues found, all folded into the plan.
- Code Quality Review: 1 issue found, folded into the plan.
- Test Review: diagram produced, 1 gap identified, folded into the plan.
- Performance Review: 0 issues found.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 0 items proposed.
- Failure modes: 0 critical gaps flagged.
- Outside voice: skipped.
- Parallelization: 4 lanes, 3 parallel after Task 1 and 1 final sequential lane.
- Lake Score: 5/5 recommendations chose the complete option.

## Implementation Notes

### Task 1 Red Baseline

- Command: `uv run pytest tests/architecture/test_process_naming_guardrail.py -q`
- Exit code: `1`
- Expected failed test: `tests/architecture/test_process_naming_guardrail.py::test_active_code_does_not_use_process_phase_names`
- First 20 offenders:
  - `scripts/architecture-guardrails.sh:10: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:11: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:12: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:13: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:14: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:15: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:16: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:17: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:31: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:75: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:76: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:149: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:150: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:156: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:162: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:163: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:169: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:175: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:176: guardrail rule id shorthand`
  - `scripts/architecture-guardrails.sh:177: guardrail rule id shorthand`
- GitNexus note: `test_process_naming_guardrail_rejects_stale_script_and_option_tokens` is not indexed by GitNexus, so blast radius for Task 1 is bounded manually to test guardrail examples and `tests/README.md` wording.

### Task 2 Atomic Guardrail Migration

- Manual impact checkpoint: `uv run python -c 'print("Manual checkpoint: script-only rename. No Python production symbol is being changed in this task.")'`
- Blast radius: bash guardrail output IDs, allowlist `rule_id` column, legacy matrix seed suffixes, architecture guardrail tests, and gate comments.
- Business runtime behavior: none.
- Generator output: current `scripts/generate_legacy_matrix.py` writes `docs/architecture/legacy-cleanup-matrix.csv` only; `docs/architecture/legacy-cleanup-matrix.md` was updated manually for the matching seed label.
- Verification:
  - `bash -n scripts/architecture-guardrails.sh` exited `0`.
  - `bash scripts/architecture-guardrails.sh --mode enforced` exited `0`.
  - Task 2 pytest command exited `0` with `88 passed, 1 skipped`.
  - Old shorthand test paths check exited `0` with no output.
- Commit: `e14b7a8 refactor(guardrails,tests): 稳定化架构护栏命名` (`--no-verify`; full process naming cleanup is completed by later tasks).
- GitNexus note: `detect_changes(repo="wes_backend", scope="all")` returned no changes because the MCP index maps the main repo path, and the worktree path is not registered as a GitNexus repository.

### Task 3 Production Comment Cleanup

- Initial residual scan found comments/docstrings plus one executable business rack slot alias in `src/app/resource/services/smt_rack_bin_scheduling_service.py`.
- GitNexus impact for `_canonical_rack_slot_code`: LOW risk, 3 direct callers, 1 affected process (`plan_allocation`). The value-preserving change keeps the real `C1` rack slot alias behind a semantic class constant and allows only that exact constant line in the process-naming guardrail.
- Verification:
  - Production residual scan became diagnostic-only; the authoritative signal is `uv run pytest tests/architecture/test_process_naming_guardrail.py -q`, which owns the exact `RACK_SLOT_C_NUMERIC_ALIAS` business exception and rejects non-exact shorthand lines.
  - Focused pytest command exited `0` with `45 passed`.
  - Slot alias runtime assertion for `C1`/`C01` exited `0`.
  - `detect_changes(repo="wes_backend", scope="all")` still returned no changes due the worktree GitNexus index boundary noted above.
- Commit: `a416aa0 refactor(runtime): 清理生产注释中的过程缩写` (`--no-verify`; full quality gate is deferred to Task 5).

### Task 4 Current Architecture Docs Cleanup

- Rewrote `architecture-guardrails-spec.md` around current `--mode` behavior and stable guardrail IDs.
- Rewrote `runtime-ownership-map.md` around stable runtime ownership, inbound normalizer ownership, and legacy runtime import boundary.
- Updated active sections in `workline-and-plugin-restructuring.md` and active index rows in `file_index.md`.
- Active doc shorthand scan result: remaining hits are limited to `process-naming-policy.md` forbidden examples, `file_index.md` release-history rows, and historical commit/reviewer labels in `workline-and-plugin-restructuring.md`.
- `detect_changes(repo="wes_backend", scope="all")` still returned no changes due the worktree GitNexus index boundary noted above.
- Commit: `96cc0b2 docs(architecture): 稳定化架构护栏命名` (`--no-verify`; full quality gate is deferred to Task 5).

### Task 5 Final Guardrail Integration

- Added explicit current architecture doc coverage in `tests/architecture/test_process_naming_guardrail.py` for:
  - `docs/architecture/process-naming-policy.md`
  - `docs/architecture/architecture-guardrails-spec.md`
  - `docs/architecture/runtime-ownership-map.md`
  - `docs/architecture/workline-and-plugin-restructuring.md`
  - `docs/architecture/file_index.md`
- Current-doc scanning intentionally targets this cleanup's shorthand family (`C*`, `R-I3*`, `R-WLR`, `wlr`, `rule_*`) while the existing full process-stage patterns continue to guard active code, scripts, tests, and CI files.
- Historical line exceptions are narrow:
  - forbidden examples inside `process-naming-policy.md`
  - release-history rows in `file_index.md`
  - historical commit/reviewer labels in `workline-and-plugin-restructuring.md`
  - the `tests/README.md` policy line that names old shorthand examples
- Replaced remaining shorthand in active test/support comments and docstrings; ruff W505 comment wrapping required follow-up wording splits after stable names made some lines longer.
- Verification:
  - `uv run pytest tests/architecture/test_process_naming_guardrail.py -q` exited `0` with `8 passed`.
  - `uv run pytest tests/architecture -q` exited `0` with `220 passed, 1 skipped`.
  - `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q` exited `0` with `4 passed`.
  - `uv run pytest --collect-only -q -o addopts='' | tail -5` exited `0` with `1852 tests collected`.
  - `./scripts/git-quality-gate.sh --profile quality` exited `0`; ruff, bandit, runtime gates, process naming, import-linter, architecture guardrails, and topology all passed.
  - `git diff --check` exited `0` with no output.
- GitNexus detect-changes:
  - Initial `detect_changes(repo="wes_backend", scope="all")` returned no changes because the indexed repo name still resolved to the main repo path.
  - `npx gitnexus analyze` in this worktree exited `0` and indexed `/Users/kaizhou/codeDev/wes_backend-worktrees/codex-guardrail-shorthand-process-naming-cleanup`.
  - `detect_changes(repo="/Users/kaizhou/codeDev/wes_backend-worktrees/codex-guardrail-shorthand-process-naming-cleanup", scope="all")` failed with LadybugDB storage version mismatch (`database version 42`, MCP current build storage version `40`).
  - Final fallback `detect_changes(repo="wes_backend", scope="all")` again returned no changes, so runtime behavior risk could not be reliably inferred by MCP for this worktree. Manual expected scope remained guardrail scripts/tests, test/support comments, production comments, and current docs; no database migrations or API response model changes were made.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | Not run | Not required for backend guardrail naming debt cleanup |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | Not run | Outside voice skipped |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 5 issues, 0 critical gaps; D2-D6 all folded into the plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | Not applicable | No UI or frontend scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | Not run | No new developer workflow beyond existing quality gates |

- **VERDICT:** ENG CLEARED - ready to implement.

NO UNRESOLVED DECISIONS
