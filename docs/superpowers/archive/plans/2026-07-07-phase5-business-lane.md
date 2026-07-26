<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260707-115825.md -->
# Phase5 Business Lane Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Completion status (2026-07-07):** DONE and LANDED. 本计划的 readiness 部分已随 PR #79 合并到 `develop`（`v0.14.0.0`，merge SHA `8c833610c08005005406b3a774c92519f69b7886`）。Phase5 business readiness gate 已通过；后续 destructive cleanup 由 companion plan `2026-07-07-phase5-business-legacy-destructive-cleanup.md` 执行并同 PR 落地。

**Original goal:** 将 Phase5 business lane 从 `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN` 推进到 `PHASE5_BUSINESS_READY`，同时不删除业务承载 legacy 数据或流程语义。

**Architecture:** 复用现有 Phase3 production closure gate、Phase4 runtime evidence gate、Phase4 business contract tests 和 Phase5 readiness gate。执行顺序固定为 artifact preflight -> evidence/contract gates -> legacy matrix closure guardrail -> business gate wiring -> docs 状态同步 -> final quality gate。

**Tech Stack:** Python 3.13, `uv`, pytest, GStack quality gate, tracked docs ledger + ignored `reports/` artifacts.

---

## Execution Baseline (Historical)

执行前真实 blocker 已由工程评审确认：

```text
reports/phase3/*.json + reports/phase4/runtime-evidence-production.json
  -> check_phase3_closure_gate.py
  -> check_phase4_runtime_readiness_gate.py
  -> Phase4 business contract tests
  -> check_phase5_readiness_gate.py --lane business
  -> LEGACY_MATRIX_BUSINESS_ITEMS_OPEN / phase5_business_lane_status
```

重要边界：

- `phase5-tech` 已完成，不重开旧 plugin runtime/import 框架清理。
- `reports/` 被 Git 忽略；本计划不得假设干净 worktree 天然存在 raw artifacts。
- `legacy-cleanup-matrix.csv` 当前没有 `drop_phase == phase5-business` 条目；104 个 `phase4_carrier=True` 条目归属 `phase4` rebuild。
- 本计划只推进 readiness 状态；destructive business cleanup 在 companion plan 中独立执行，并已随 PR #79 同步落地。

## File Structure

- Create: `tests/contracts/test_phase5_business_lane_matrix_closure.py`
  - 负责锁定 business lane matrix closure 的可审计前提，避免只改 ready token。
- Modify: `scripts/check_phase5_readiness_gate.py`
  - 将 matrix closure guardrail 纳入 `BUSINESS_LANE_CONTRACT_TESTS`，避免单跑 `--lane business` 时 ready token 假阳性。
- Modify: `tests/contracts/test_phase5_readiness_gate.py`
  - 锁定 business readiness gate 会执行 matrix closure guardrail。
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
  - 更新 `phase5_business_lane_status` 和 stale evidence blocker 文案。
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
  - 同步 Phase5 business lane 从 blocked 到 ready 的状态与边界。
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
  - 记录 business gate 通过后的执行状态。
- Modify: `docs/architecture/phase3-phase4-production-evidence-bundle.md`
  - 将 validation 中 Phase5 business 结果从 expected blocker 更新为 passed。
- Modify: `docs/superpowers/plans/2026-07-07-phase5-business-lane.md`
  - 执行过程中逐步勾选状态。

## Task 1: Artifact Preflight

**Files:**
- Read: `docs/architecture/phase3-phase4-production-evidence-bundle.md`
- Produce if missing: `reports/phase3/phase3-p0-e2e.json`
- Produce if missing: `reports/phase3/phase3-production-benchmark.json`
- Produce if missing: `reports/phase4/runtime-evidence-production.json`

- [x] **Step 1: Confirm raw artifact availability**

Run:

```bash
test -f reports/phase3/phase3-p0-e2e.json \
  && test -f reports/phase3/phase3-production-benchmark.json \
  && test -f reports/phase4/runtime-evidence-production.json \
  && echo "phase5 artifacts present"
```

Expected if local artifacts already exist:

```text
phase5 artifacts present
```

If the command prints nothing or exits non-zero, continue to Step 2.

- [x] **Step 2: Regenerate missing artifacts from the tracked ledger**

Before regenerating, confirm the raw evidence roots exist:

```bash
test -d reports/phase3/evidence/p0-e2e \
  && test -d reports/phase3/evidence/benchmark \
  && test -d reports/phase4/evidence/phase4-runtime \
  && echo "phase5 raw evidence roots present"
```

Expected:

```text
phase5 raw evidence roots present
```

If a raw evidence root is missing, stop with `MISSING_RAW_EVIDENCE_ROOTS`. The tracked ledger records hashes and
provenance, but it does not yet contain a concrete CI artifact locator. Do not replace production evidence with
synthetic local files; restore the archived field/CI `reports/` bundle first, then rerun this task.

Use the frozen timestamp from `docs/architecture/phase3-phase4-production-evidence-bundle.md`:

```bash
SNAPSHOT_GENERATED_AT="2026-07-07T02:46:16Z"

uv run python scripts/compose_phase3_p0_e2e_artifact.py \
  --output reports/phase3/phase3-p0-e2e.json \
  --environment field-dry-run \
  --dependency-profile wms-ecs-http \
  --trace-recording reports/phase3/evidence/p0-e2e/source.json \
  --p95-seconds 18.7 \
  --exception-evidence callback_out_of_order=reports/phase3/evidence/p0-e2e/callback_out_of_order.json \
  --exception-evidence ecs_timeout=reports/phase3/evidence/p0-e2e/ecs_timeout.json \
  --exception-evidence wms_reject=reports/phase3/evidence/p0-e2e/wms_reject.json

uv run python scripts/compose_phase3_runtime_benchmark_artifact.py \
  --output reports/phase3/phase3-production-benchmark.json \
  --environment field-benchmark \
  --generated-at "$SNAPSHOT_GENERATED_AT" \
  --dependency-profile postgresql-wms-ecs-http \
  --concurrency-level 64 \
  --duration-seconds 300 \
  --scenario-evidence runtime_inbox_claim=reports/phase3/evidence/benchmark/runtime_inbox_claim.json \
  --scenario-evidence conveyor_queue_writer=reports/phase3/evidence/benchmark/conveyor_queue_writer.json \
  --scenario-evidence ecs_status_command=reports/phase3/evidence/benchmark/ecs_status_command.json \
  --scenario-evidence plane_snapshot=reports/phase3/evidence/benchmark/plane_snapshot.json

uv run python scripts/compose_phase4_runtime_evidence_artifact.py \
  --output reports/phase4/runtime-evidence-production.json \
  --profile production \
  --environment field-production \
  --generated-at "$SNAPSHOT_GENERATED_AT" \
  --evidence-dir evidence/phase4-runtime
```

Expected: all three output files are created under ignored `reports/`.

- [x] **Step 3: Verify artifact hashes match the ledger**

Run:

```bash
shasum -a 256 \
  reports/phase3/phase3-p0-e2e.json \
  reports/phase3/phase3-production-benchmark.json \
  reports/phase4/runtime-evidence-production.json
```

Expected hashes:

```text
0840947996b7e15e5847a57b16156373e174fbafc491350592e097aa9c4a60ed  reports/phase3/phase3-p0-e2e.json
6d039f4210128b337ff10228228d510a45ea0caab01f331f7fdfc592bbcd71b1  reports/phase3/phase3-production-benchmark.json
29b6d3990296efa875b35c90ba90a4fe4ea70b150b376c97f5243706f1cb7fec  reports/phase4/runtime-evidence-production.json
```

- [x] **Step 4: Run baseline business gate**

Run:

```bash
uv run python scripts/check_phase5_readiness_gate.py --lane business \
  --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json \
  --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
```

Expected before matrix closure during implementation:

```text
Phase 5 readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN
details=phase5_business_lane_status
```

If the failure is `MISSING_PHASE3_PRODUCTION_CLOSURE`, `MISSING_PHASE4_PRODUCTION_EVIDENCE`, or `PHASE5_BUSINESS_CONTRACTS_OPEN`, stop and fix that blocker before continuing.

## Task 2: Matrix Closure Guardrail And Gate Wiring

**Files:**
- Create: `tests/contracts/test_phase5_business_lane_matrix_closure.py`
- Modify: `scripts/check_phase5_readiness_gate.py`
- Modify: `tests/contracts/test_phase5_readiness_gate.py`
- Read: `docs/architecture/legacy-cleanup-matrix.csv`
- Read: `docs/architecture/legacy-cleanup-matrix.md`

- [x] **Step 1: Add the closure guardrail test**

Create a pytest contract test that asserts these exact conditions:

```text
1. legacy-cleanup-matrix.csv has zero rows where drop_phase == "phase5-business".
2. Every phase4_carrier=True row has:
   - drop_phase == "phase4"
   - non-empty target_path
   - non-empty target_capability
   - non-empty blocking_tests
3. legacy-cleanup-matrix.md contains:
   phase5_business_lane_status: ready-for-business-cleanup
4. Current-state architecture docs have zero matches for stale blocker tokens:
   MISSING_PHASE3_PRODUCTION_CLOSURE / MISSING_PHASE4_PRODUCTION_EVIDENCE /
   LEGACY_MATRIX_BUSINESS_ITEMS_OPEN / blocked-until-production-evidence /
   继续阻塞 / 当前失败
```

Keep the test small and data-driven. Do not duplicate the matrix generator rules in the test. Do not keep historical
blocker notes in current-state architecture docs; move historical context to git history or a clearly separate archive
doc if it must be preserved.

- [x] **Step 2: Wire the guardrail into business readiness**

Before changing `scripts/check_phase5_readiness_gate.py`, run GitNexus impact analysis on
`BUSINESS_LANE_CONTRACT_TESTS` and `_business_contracts_result`. If the risk is HIGH or CRITICAL, stop and report the
blast radius before editing.

Then add `tests/contracts/test_phase5_business_lane_matrix_closure.py` to `BUSINESS_LANE_CONTRACT_TESTS`.

Update `tests/contracts/test_phase5_readiness_gate.py` so:

```text
1. Its tmp repo contract-test fixtures derive from the script's BUSINESS_LANE_CONTRACT_TESTS source of truth,
   or have an explicit parity assertion if direct import is not practical.
2. _write_business_lane_contract_tests creates a passing stub for every path in that source list, including
   tests/contracts/test_phase5_business_lane_matrix_closure.py.
3. A focused assertion fails if BUSINESS_LANE_CONTRACT_TESTS drops the matrix closure guardrail.
```

Expected: `check_phase5_readiness_gate.py --lane business` cannot pass by only changing
`phase5_business_lane_status`; the CSV/docs closure guardrail is part of the gate-owned contract suite, and the
readiness-gate unit tests keep passing because their tmp repo stubs stay in sync with the gate-owned list.

- [x] **Step 3: Run the new guardrail and verify it fails**

Run:

```bash
uv run pytest tests/contracts/test_phase5_business_lane_matrix_closure.py -q
```

Expected before docs update:

```text
FAILED ... phase5_business_lane_status
```

- [x] **Step 4: Confirm matrix row facts independently**

Run:

```bash
uv run python - <<'PY'
import csv
from collections import Counter

with open("docs/architecture/legacy-cleanup-matrix.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

phase5_business = [row for row in rows if row["drop_phase"] == "phase5-business"]
phase4_carriers = [row for row in rows if row["phase4_carrier"].lower() == "true"]
missing = [
    row
    for row in phase4_carriers
    if row["drop_phase"] != "phase4"
    or not row["target_path"]
    or not row["target_capability"]
    or not row["blocking_tests"]
]

print("drop_phase", Counter(row["drop_phase"] for row in rows))
print("phase5_business", len(phase5_business))
print("phase4_carrier", len(phase4_carriers), "invalid", len(missing))
PY
```

Expected:

```text
phase5_business 0
phase4_carrier 104 invalid 0
```

## Task 3: Docs State Closure

**Files:**
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Modify: `docs/architecture/phase3-phase4-production-evidence-bundle.md`

- [x] **Step 1: Update legacy cleanup matrix status**

In `docs/architecture/legacy-cleanup-matrix.md`:

```text
phase5_business_lane_status: blocked-until-production-evidence
```

Change to:

```text
phase5_business_lane_status: ready-for-business-cleanup
```

Also replace stale text that says business lane fails at `MISSING_PHASE3_PRODUCTION_CLOSURE`. The updated wording must say:

```text
携带 regenerated Phase3/Phase4 artifacts 后，business lane 已通过 Phase3 production closure、
Phase4 production evidence 与 Phase4 business contract tests；legacy matrix 中无
phase5-business 删除项，Phase5 business readiness gate 可进入 ready 状态。
```

- [x] **Step 2: Update the main restructuring doc**

In `docs/architecture/workline-and-plugin-restructuring.md`, update Phase5 status to say:

```text
phase5-business 已通过 readiness gate。当前仅表示业务承载 legacy 删除前置已清账；
本轮不删除业务承载 legacy 数据、schema 或流程语义。后续 destructive cleanup 必须另开计划。
```

Keep existing technical lane completion notes intact.

- [x] **Step 3: Update execution plan and evidence ledger validation**

In `docs/architecture/legacy-cleanup-execution-plan.md` and `docs/architecture/phase3-phase4-production-evidence-bundle.md`, replace the old expected failure block:

```text
Phase 5 readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN
details=phase5_business_lane_status
```

With the final expected result:

```text
Phase 5 readiness passed: lane=business
```

Mention that raw `reports/` artifacts remain ignored and must be regenerated from restored field/CI evidence before validation.

- [x] **Step 4: Re-run the closure guardrail**

Run:

```bash
uv run pytest tests/contracts/test_phase5_business_lane_matrix_closure.py -q
```

Expected:

```text
1 passed
```

## Task 4: Gate Verification

**Files:**
- No additional code edits expected after Task 2.

- [x] **Step 1: Run Phase5 readiness contract tests**

Run:

```bash
uv run pytest tests/contracts/test_phase5_readiness_gate.py tests/contracts/test_phase5_business_lane_matrix_closure.py -q
```

Expected:

```text
all selected Phase5 readiness tests passed
```

- [x] **Step 2: Run technical lane gate**

Run:

```bash
uv run python scripts/check_phase5_readiness_gate.py --lane technical
```

Expected:

```text
Phase 5 readiness passed: lane=technical
```

- [x] **Step 3: Run business lane gate**

Run:

```bash
uv run python scripts/check_phase5_readiness_gate.py --lane business \
  --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json \
  --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
```

Expected:

```text
Phase 5 readiness passed: lane=business
```

- [x] **Step 4: Run test topology guardrail**

Run:

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
```

Expected: pass.

- [x] **Step 5: Run collect-only smoke**

Run:

```bash
uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected: collection completes without errors.

- [x] **Step 6: Run quality gate**

Run:

```bash
./scripts/git-quality-gate.sh --profile quality
```

Expected: quality gate passes.

- [x] **Step 7: Confirm stale blocker text is mechanically closed**

Run:

```bash
! rg -n "MISSING_PHASE3_PRODUCTION_CLOSURE|MISSING_PHASE4_PRODUCTION_EVIDENCE|LEGACY_MATRIX_BUSINESS_ITEMS_OPEN|blocked-until-production-evidence|继续阻塞|当前失败" \
  docs/architecture/legacy-cleanup-matrix.md \
  docs/architecture/workline-and-plugin-restructuring.md \
  docs/architecture/legacy-cleanup-execution-plan.md \
  docs/architecture/phase3-phase4-production-evidence-bundle.md
```

Expected: command exits 0 because `rg` finds no stale blocker token in current-state architecture docs. Historical dated
notes are not allowlisted in these files for this closure PR.

## Task 5: Commit And Handoff

**Files:**
- Commit all modified docs, the readiness gate wiring, and the new contract test.

- [x] **Step 1: Inspect final diff**

Run:

```bash
git status --short
git diff -- docs/architecture docs/superpowers/plans scripts/check_phase5_readiness_gate.py tests/contracts/test_phase5_readiness_gate.py
git diff --no-index /dev/null tests/contracts/test_phase5_business_lane_matrix_closure.py || true
```

Expected:

```text
Only Phase5 business readiness docs, readiness gate wiring, and the matrix closure guardrail changed.
The new test file is visible before staging.
```

- [x] **Step 2: Run GitNexus change detection**

Run:

```bash
gitnexus detect-changes
```

Expected: changed scope matches docs + readiness gate wiring + tests only.

If the local GitNexus command name differs, use the configured GitNexus detect-changes entrypoint for this repo.

- [x] **Step 3: Commit**

Run:

```bash
git add \
  docs/architecture/legacy-cleanup-matrix.md \
  docs/architecture/workline-and-plugin-restructuring.md \
  docs/architecture/legacy-cleanup-execution-plan.md \
  docs/architecture/phase3-phase4-production-evidence-bundle.md \
  docs/superpowers/plans/2026-07-07-phase5-business-lane.md \
  scripts/check_phase5_readiness_gate.py \
  tests/contracts/test_phase5_readiness_gate.py \
  tests/contracts/test_phase5_business_lane_matrix_closure.py

git commit -m "docs(runtime): 推进 Phase5 business lane ready 状态"
```

Expected: commit succeeds.

## NOT In Scope

- 不删除业务承载 legacy 数据、schema、迁移历史或流程语义。
- 不提交 raw `reports/` artifacts；只提交 docs、ledger 状态和 guardrail test。
- 不重开 Phase5 technical lane，不恢复旧 plugin runtime/import 路径。
- 不建设统一运营看板、告警平台或供应商联调手册。
- 不做 UI/frontend 改造。

## Acceptance Criteria

- `uv run python scripts/check_phase5_readiness_gate.py --lane business ...` passes.
- `uv run python scripts/check_phase5_readiness_gate.py --lane technical` still passes.
- `BUSINESS_LANE_CONTRACT_TESTS` includes `tests/contracts/test_phase5_business_lane_matrix_closure.py`, so business gate cannot pass by ready-token-only drift.
- `tests/contracts/test_phase5_readiness_gate.py` derives or parity-checks the business contract list and creates tmp repo stubs for every listed contract test.
- `tests/contracts/test_phase5_business_lane_matrix_closure.py` prevents ready token drift without matrix/docs evidence.
- Docs no longer say Phase5 business is blocked by Phase3/Phase4 production evidence.
- Current-state architecture docs no longer say Phase5 business is blocked by `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN`.
- Final diff contains no destructive business cleanup.

## Self-Review

- Spec coverage: covers the ENG review finding by making artifact preflight the first formal task.
- Placeholder scan: no placeholder tokens, no unspecified test command, no deferred validation.
- Scope check: single subsystem, docs + readiness gate wiring + contract guardrail only; destructive cleanup explicitly out of scope.
- Test strategy: adds one narrow matrix closure contract test and wires it into the existing Phase5 business readiness gate.

## Decision Audit Trail

| Decision | Type | Principle | Resolution |
| --- | --- | --- | --- |
| Skip Design Review | mechanical | Scope accuracy | 本计划无 UI/frontend surface，Design Review 跳过。 |
| Run DX Review | mechanical | Developer-facing clarity | 计划包含 agentic worker instructions、命令、gate 和 handoff，保留 DX 视角。 |
| Treat raw evidence roots as hard precondition | auto-decided | Explicit over clever | Ledger 记录 hash/provenance，但无具体 CI artifact locator；缺 raw evidence 时停止，不合成证据。 |
| Wire matrix closure into business gate | auto-decided | Completeness | 新 guardrail 必须进入 `BUSINESS_LANE_CONTRACT_TESTS`，避免 `--lane business` 只靠 ready token 误判。 |
| Add stale blocker search | auto-decided | DRY / evidence integrity | 用 `rg` 收口 current-state blocker 文案，避免 docs ledger 互相矛盾。 |
| Keep destructive cleanup out of scope | mechanical | Focus | 本轮只推进 readiness，不删除业务承载 legacy 数据、schema 或流程语义。 |

## Autoplan Verification Notes

- Artifact presence: `phase5 artifacts present`.
- Raw evidence roots: `phase5 raw evidence roots present`.
- Artifact hashes: matched the tracked ledger for Phase3 P0 E2E, Phase3 benchmark, and Phase4 runtime evidence.
- Matrix audit: `phase5_business 0`; `phase4_carrier 104 invalid 0`.
- Existing readiness tests: `uv run pytest tests/contracts/test_phase5_readiness_gate.py -q` -> `12 passed`.
- Pre-implementation baseline gate: `uv run python scripts/check_phase5_readiness_gate.py --lane business ...` failed at `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN / phase5_business_lane_status`, as expected before executing this plan.
- Outside Codex CLI review: completed read-only and produced 3 findings; all were folded into the plan.

## Implementation Completion Status

- **Status:** DONE / LANDED.
- **Landing:** PR #79 merged into `develop` on 2026-07-07 as `v0.14.0.0`; merge SHA `8c833610c08005005406b3a774c92519f69b7886`.
- **Delivered:** `phase5_business_lane_status` advanced to ready, matrix closure guardrail was wired into the business readiness gate, and current-state architecture docs no longer report Phase5 business blocked by Phase3/Phase4 evidence or legacy matrix readiness.
- **Verification at ship:** `uv run pytest tests/ -q` passed with `1820 passed, 5 skipped`; `./scripts/git-quality-gate.sh --profile quality` passed; `check_phase5_readiness_gate.py --lane business` passed with regenerated Phase3/Phase4 artifacts.
- **Deploy status:** PR merged; no GitHub deploy workflow or production URL was configured, so post-merge canary was skipped by user choice and recorded as `DEPLOYED (UNVERIFIED)`.
- **Follow-up:** business destructive cleanup is no longer pending; it landed in the companion plan/PR #79. `WorkLine.runtime_status` physical schema/data deletion remains a separate cleanup plan.

## GSTACK REVIEW REPORT

以下保留实施前评审记录；当前完成状态以上方 `Implementation Completion Status` 为准。

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/autoplan` | Scope & strategy | 1 | clean | Scope remains narrow: move business lane readiness only; destructive cleanup stays out of scope. |
| Codex Review | `codex exec` | Independent 2nd opinion | 1 | clean-after-folding | Initial 3 findings folded before this eng terminal review. |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | clean | Implementation-final review found 0 new issues after gate fixture parity and mechanical stale-token check were folded. |
| Design Review | `/autoplan` | UI/UX gaps | 1 | skipped | Backend/docs/test-only plan; no UI surface. |
| DX Review | `/autoplan` | Developer experience gaps | 1 | clean-after-folding | Execution is explicit for agentic workers: preflight, gate wiring, docs closure, verification, handoff. |

- **VERDICT:** ENG CLEARED — ready for implementation with `superpowers:executing-plans` or `superpowers:subagent-driven-development`.
NO UNRESOLVED DECISIONS
