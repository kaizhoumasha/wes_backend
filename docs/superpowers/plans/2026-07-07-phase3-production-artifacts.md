# Phase3 + Phase4 Production Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 gate-valid 的 `phase3-p0-e2e-artifact`、`phase3-benchmark-artifact` 与 Phase4 production evidence hashes，让 Phase5 business lane 不再阻塞于 `MISSING_PHASE3_PRODUCTION_CLOSURE` 或 `MISSING_PHASE4_PRODUCTION_EVIDENCE`。

**Architecture:** 以现有 evidence 文件为输入，以现有 composer 脚本生成 artifact；不要手写 artifact JSON。`reports/phase3/evidence/**` 与 `reports/phase4/evidence/phase4-runtime/**` 是来源证据，`reports/phase3/*.json` 与 `reports/phase4/runtime-evidence-production.json` 是派生产物，`RuntimePhase3ClosureGate` 与 Phase4 runtime readiness gate 是唯一验收口径。

**Tech Stack:** Python 3.13, `uv`, JSON evidence, `scripts/compose_phase3_p0_e2e_artifact.py`, `scripts/compose_phase3_runtime_benchmark_artifact.py`, `scripts/compose_phase4_runtime_evidence_artifact.py`, `scripts/check_phase3_closure_gate.py`, `scripts/check_phase4_runtime_readiness_gate.py`, pytest, gstack Phase5 readiness gate。

---

## Scope Check

本计划关闭 production evidence 的三个 artifact 缺口：

1. `phase3-p0-e2e-artifact`
2. `phase3-benchmark-artifact`
3. `phase4-runtime-evidence-artifact` 的 production evidence manifest hashes

不在本计划内：

- 不解除 `phase5_business_lane_status: blocked-until-production-evidence`；补完本计划后，Phase5 business lane 的当前预期 blocker 是 `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN`。
- 不把 `reports/` 默认纳入 Git；`.gitignore` 当前忽略 `/reports/*` 与 `reports/`，artifact 默认作为本地/CI/现场证据产物使用。
- 不伪造现场结果；若 evidence 文件不是来自真实 dry-run / benchmark，执行者必须先替换 evidence，再运行本计划命令。

## Evidence Flow

```text
reports/phase3/evidence/*
  -> compose_phase3_p0_e2e_artifact.py
  -> compose_phase3_runtime_benchmark_artifact.py
  -> reports/phase3/*.json
  -> check_phase3_closure_gate.py

reports/phase4/evidence/phase4-runtime/*
  -> compose_phase4_runtime_evidence_artifact.py
  -> reports/phase4/runtime-evidence-production.json
  -> check_phase4_runtime_readiness_gate.py

Phase3 artifacts + Phase4 artifact
  -> check_phase5_readiness_gate.py --lane business
  -> expected next blocker: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN
```

## Current Diagnosis

当前仓库已有以下文件，但旧派生 artifact 缺 provenance/hash 字段或未通过 production evidence profile：

- `reports/phase3/phase3-p0-e2e.json`
- `reports/phase3/phase3-production-benchmark.json`
- `reports/phase3/evidence/p0-e2e/source.json`
- `reports/phase3/evidence/p0-e2e/callback_out_of_order.json`
- `reports/phase3/evidence/p0-e2e/ecs_timeout.json`
- `reports/phase3/evidence/p0-e2e/wms_reject.json`
- `reports/phase3/evidence/benchmark/runtime_inbox_claim.json`
- `reports/phase3/evidence/benchmark/conveyor_queue_writer.json`
- `reports/phase3/evidence/benchmark/ecs_status_command.json`
- `reports/phase3/evidence/benchmark/plane_snapshot.json`
- `reports/phase4/runtime-evidence-production.json`
- `reports/phase4/evidence/phase4-runtime/provider-contracts/sorter-inbound.json`
- `reports/phase4/evidence/phase4-runtime/provider-contracts/smt-ng-wms-reconciliation.json`
- `reports/phase4/evidence/phase4-runtime/traces/effect-dispatch.json`
- `reports/phase4/evidence/phase4-runtime/traces/runtime-inbox-worker.json`
- `reports/phase4/evidence/phase4-runtime/traces/runtime-hold-reconciliation.json`
- `reports/phase4/evidence/phase4-runtime/benchmarks/phase4-runtime.json`

已复现失败：

```bash
uv run python scripts/check_phase3_closure_gate.py \
  --closure-profile production \
  --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --benchmark-artifact reports/phase3/phase3-production-benchmark.json
```

Expected current failure:

```text
Phase 3 closure evidence failed validation: INVALID_PHASE3_CLOSURE_ARTIFACTS
invalid_artifacts=p0_e2e:MISSING_SOURCE_PROVENANCE,benchmark:MISSING_SCENARIO_PROVENANCE
```

Root cause:

- P0 E2E artifact 缺 `source.environment` 与 `source.evidence_sha256`。
- P0 E2E exception paths 缺各自 `evidence_sha256`。
- Benchmark scenario `source` 缺 `evidence` 与 `evidence_sha256` 的 composer 归一化结果。
- Phase4 production evidence artifact 缺 `evidence_manifest` 中每个引用文件的 `evidence_sha256`。
- 现有 composer 已能从 evidence 文件生成正确字段；无需新增平行 schema。

## File Structure

### Source Evidence

- Read/replace as needed: `reports/phase3/evidence/p0-e2e/source.json`
  - 必须是 `TraceQueryResult` 或等价脱敏录制结果。
  - `events[]` 至少覆盖 `workline_manifest`、`execution_session`、`runtime_inbox`、`runtime_intent`、`device_command`、`wms_fulfillment`、`plane_snapshot`。
  - `runtime_intent` 或 `device_command` event payload 必须包含 `effect_key` 前缀 `device-command:`。
  - `wms_fulfillment` event payload 必须包含 `effect_key` 前缀 `wms-fulfillment:`.
- Read/replace as needed: `reports/phase3/evidence/p0-e2e/callback_out_of_order.json`
  - JSON 形状：`{"case":"callback_out_of_order","result":"RECONCILING"}`。
- Read/replace as needed: `reports/phase3/evidence/p0-e2e/ecs_timeout.json`
  - JSON 形状：`{"case":"ecs_timeout","result":"RECONCILING"}`。
- Read/replace as needed: `reports/phase3/evidence/p0-e2e/wms_reject.json`
  - JSON 形状：`{"case":"wms_reject","result":"RECONCILING"}`。
- Read/replace as needed: `reports/phase3/evidence/benchmark/runtime_inbox_claim.json`
  - 必须包含 `source.kind = "postgresql"`、`workload.pending_inbox_count >= 1000`、`workload.worker_concurrency >= 4`。
- Read/replace as needed: `reports/phase3/evidence/benchmark/conveyor_queue_writer.json`
  - 必须包含 `source.kind = "postgresql"`、`workload.active_membership_count >= 200`、`workload.concurrent_identity_collision = true`。
- Read/replace as needed: `reports/phase3/evidence/benchmark/ecs_status_command.json`
  - 必须包含 `source.kind = "ecs-http"`、`workload.status_get_count >= 1`、`workload.command_post_count >= 1`。
- Read/replace as needed: `reports/phase3/evidence/benchmark/plane_snapshot.json`
  - 必须包含 `source.kind = "api-http"`、`workload.workline_count >= 1`、`queue_count >= 10`、`device_count >= 50`、`active_session_count >= 100`、`active_object_count >= 200`。
- Read/replace as needed: `reports/phase4/evidence/phase4-runtime/provider-contracts/*.json`
  - 必须覆盖 sorter inbound 与 SMT/NG/WMS reconciliation provider contract evidence。
- Read/replace as needed: `reports/phase4/evidence/phase4-runtime/traces/*.json`
  - 必须覆盖 effect dispatch、RuntimeInbox worker 与 RuntimeHold/Reconciliation trace。
- Read/replace as needed: `reports/phase4/evidence/phase4-runtime/benchmarks/phase4-runtime.json`
  - 必须是 production profile 可接受的 Phase4 runtime benchmark evidence。

### Derived Artifacts

- Generate: `reports/phase3/phase3-p0-e2e.json`
  - 只能由 `scripts/compose_phase3_p0_e2e_artifact.py` 生成。
- Generate: `reports/phase3/phase3-production-benchmark.json`
  - 只能由 `scripts/compose_phase3_runtime_benchmark_artifact.py` 生成。
- Generate: `reports/phase4/runtime-evidence-production.json`
  - 只能由 `scripts/compose_phase4_runtime_evidence_artifact.py` 生成。

### Documentation

- Create: `docs/architecture/phase3-phase4-production-evidence-bundle.md`
  - 记录生成命令、artifact 路径、evidence root、SHA256、校验结果和剩余 Phase5 business blocker。
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
  - 将 production evidence blocker 从“缺 Phase3 provenance”更新为“Phase3 + Phase4 production evidence 可由 reports evidence 生成并通过 gate；Phase5 business 仍等待 legacy matrix business close”。
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
  - 更新 §10.0.1 / §10.6 中 Phase5 business blocker 文案，避免继续声称 Phase3 或 Phase4 production evidence 缺失。

### Tests And Gates

- Run: `uv run python scripts/check_phase3_p0_e2e_gate.py reports/phase3/phase3-p0-e2e.json`
- Run: `uv run python scripts/check_phase3_closure_gate.py --closure-profile production --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- Run: `uv run pytest tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py tests/runtime/orchestration/test_phase3_closure_evidence_gate.py -q`
- Run: `uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- Run: `uv run pytest tests/contracts/test_phase4_runtime_evidence_artifact_composer.py tests/contracts/test_phase4_runtime_readiness_gate.py -q`
- Run: `uv run pytest tests/contracts/test_phase5_readiness_gate.py -q`
- Run: `uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json`

## Task 1: Baseline The Current Failure

**Files:**

- Read: `reports/phase3/phase3-p0-e2e.json`
- Read: `reports/phase3/phase3-production-benchmark.json`
- Read: `reports/phase3/evidence/p0-e2e/*.json`
- Read: `reports/phase3/evidence/benchmark/*.json`

- [ ] **Step 1: Confirm artifacts currently fail for the expected reason**

Run:

```bash
uv run python scripts/check_phase3_closure_gate.py \
  --closure-profile production \
  --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --benchmark-artifact reports/phase3/phase3-production-benchmark.json
```

Expected:

```text
Phase 3 closure evidence failed validation: INVALID_PHASE3_CLOSURE_ARTIFACTS
invalid_artifacts=p0_e2e:MISSING_SOURCE_PROVENANCE,benchmark:MISSING_SCENARIO_PROVENANCE
```

- [ ] **Step 2: Confirm required evidence files exist**

Run:

```bash
test -f reports/phase3/evidence/p0-e2e/source.json
test -f reports/phase3/evidence/p0-e2e/callback_out_of_order.json
test -f reports/phase3/evidence/p0-e2e/ecs_timeout.json
test -f reports/phase3/evidence/p0-e2e/wms_reject.json
test -f reports/phase3/evidence/benchmark/runtime_inbox_claim.json
test -f reports/phase3/evidence/benchmark/conveyor_queue_writer.json
test -f reports/phase3/evidence/benchmark/ecs_status_command.json
test -f reports/phase3/evidence/benchmark/plane_snapshot.json
```

Expected: command exits `0`.

- [ ] **Step 3: Inspect evidence source kinds and workload metadata**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

for path in sorted(Path("reports/phase3/evidence/benchmark").glob("*.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(path.name, payload["source"]["kind"], payload["workload"])
PY
```

Expected:

```text
conveyor_queue_writer.json postgresql {'active_membership_count': 200, 'concurrent_identity_collision': True}
ecs_status_command.json ecs-http {'command_post_count': 400, 'status_get_count': 400}
plane_snapshot.json api-http {'active_object_count': 200, 'active_session_count': 100, 'device_count': 50, 'queue_count': 10, 'workline_count': 1}
runtime_inbox_claim.json postgresql {'pending_inbox_count': 1000, 'worker_concurrency': 4}
```

## Task 2: Compose The P0 E2E Artifact

**Files:**

- Generate: `reports/phase3/phase3-p0-e2e.json`
- Read: `reports/phase3/evidence/p0-e2e/source.json`
- Read: `reports/phase3/evidence/p0-e2e/callback_out_of_order.json`
- Read: `reports/phase3/evidence/p0-e2e/ecs_timeout.json`
- Read: `reports/phase3/evidence/p0-e2e/wms_reject.json`
- Verify: `scripts/check_phase3_p0_e2e_gate.py`

- [ ] **Step 1: Verify the source recording has all required event groups**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

recording = json.loads(Path("reports/phase3/evidence/p0-e2e/source.json").read_text(encoding="utf-8"))
kinds = {event.get("kind") for event in recording.get("events", [])}
required = {
    "workline_manifest",
    "execution_session",
    "runtime_inbox",
    "runtime_intent",
    "device_command",
    "wms_fulfillment",
    "plane_snapshot",
}
missing = sorted(required - kinds)
print("missing_event_kinds=", missing)
PY
```

Expected:

```text
missing_event_kinds= []
```

- [ ] **Step 2: Verify exception evidence case names**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

expected = {
    "callback_out_of_order": Path("reports/phase3/evidence/p0-e2e/callback_out_of_order.json"),
    "ecs_timeout": Path("reports/phase3/evidence/p0-e2e/ecs_timeout.json"),
    "wms_reject": Path("reports/phase3/evidence/p0-e2e/wms_reject.json"),
}
for case, path in expected.items():
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(case, payload.get("case"), payload.get("result"))
PY
```

Expected:

```text
callback_out_of_order callback_out_of_order RECONCILING
ecs_timeout ecs_timeout RECONCILING
wms_reject wms_reject RECONCILING
```

- [ ] **Step 3: Generate the artifact through the composer**

Run:

```bash
uv run python scripts/compose_phase3_p0_e2e_artifact.py \
  --output reports/phase3/phase3-p0-e2e.json \
  --environment field-dry-run \
  --dependency-profile wms-ecs-http \
  --trace-recording reports/phase3/evidence/p0-e2e/source.json \
  --p95-seconds 18.7 \
  --exception-evidence callback_out_of_order=reports/phase3/evidence/p0-e2e/callback_out_of_order.json \
  --exception-evidence ecs_timeout=reports/phase3/evidence/p0-e2e/ecs_timeout.json \
  --exception-evidence wms_reject=reports/phase3/evidence/p0-e2e/wms_reject.json
```

Expected:

```text
Phase 3 P0 E2E artifact written: reports/phase3/phase3-p0-e2e.json
```

- [ ] **Step 4: Verify the generated provenance fields**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

artifact = json.loads(Path("reports/phase3/phase3-p0-e2e.json").read_text(encoding="utf-8"))
print("source_keys=", sorted(artifact["source"]))
print("source_environment=", artifact["source"]["environment"])
print("source_hash_len=", len(artifact["source"]["evidence_sha256"]))
for name, payload in sorted(artifact["exception_paths"].items()):
    print(name, payload["result"], len(payload["evidence_sha256"]))
PY
```

Expected:

```text
source_keys= ['environment', 'evidence', 'evidence_sha256', 'kind']
source_environment= field-dry-run
source_hash_len= 64
callback_out_of_order RECONCILING 64
ecs_timeout RECONCILING 64
wms_reject RECONCILING 64
```

- [ ] **Step 5: Run the P0 E2E artifact gate**

Run:

```bash
uv run python scripts/check_phase3_p0_e2e_gate.py reports/phase3/phase3-p0-e2e.json
```

Expected:

```text
Phase 3 P0 E2E artifact passed: reports/phase3/phase3-p0-e2e.json
```

## Task 3: Compose The Production Benchmark Artifact

**Files:**

- Generate: `reports/phase3/phase3-production-benchmark.json`
- Read: `reports/phase3/evidence/benchmark/runtime_inbox_claim.json`
- Read: `reports/phase3/evidence/benchmark/conveyor_queue_writer.json`
- Read: `reports/phase3/evidence/benchmark/ecs_status_command.json`
- Read: `reports/phase3/evidence/benchmark/plane_snapshot.json`
- Verify: `src/app/runtime/orchestration/benchmark_gate.py`

- [ ] **Step 1: Validate scenario names before composing**

Run:

```bash
uv run python - <<'PY'
from pathlib import Path

actual = {path.stem for path in Path("reports/phase3/evidence/benchmark").glob("*.json")}
expected = {"runtime_inbox_claim", "conveyor_queue_writer", "ecs_status_command", "plane_snapshot"}
print("missing=", sorted(expected - actual))
print("extra=", sorted(actual - expected))
PY
```

Expected:

```text
missing= []
extra= []
```

- [ ] **Step 2: Generate the artifact through the composer**

Run:

```bash
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run python scripts/compose_phase3_runtime_benchmark_artifact.py \
  --output reports/phase3/phase3-production-benchmark.json \
  --environment field-benchmark \
  --generated-at "$GENERATED_AT" \
  --dependency-profile postgresql-wms-ecs-http \
  --concurrency-level 64 \
  --duration-seconds 300 \
  --scenario-evidence runtime_inbox_claim=reports/phase3/evidence/benchmark/runtime_inbox_claim.json \
  --scenario-evidence conveyor_queue_writer=reports/phase3/evidence/benchmark/conveyor_queue_writer.json \
  --scenario-evidence ecs_status_command=reports/phase3/evidence/benchmark/ecs_status_command.json \
  --scenario-evidence plane_snapshot=reports/phase3/evidence/benchmark/plane_snapshot.json
```

Expected:

```text
Phase 3 production benchmark artifact written: reports/phase3/phase3-production-benchmark.json
```

- [ ] **Step 3: Verify scenario provenance fields**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

artifact = json.loads(Path("reports/phase3/phase3-production-benchmark.json").read_text(encoding="utf-8"))
print("profile_kind=", artifact["profile"]["kind"])
print("database_backend=", artifact["profile"]["database_backend"])
for name, scenario in sorted(artifact["scenarios"].items()):
    source = scenario["source"]
    print(name, source["kind"], Path(source["evidence"]).is_file(), len(source["evidence_sha256"]))
PY
```

Expected:

```text
profile_kind= production-scale
database_backend= postgresql
conveyor_queue_writer postgresql True 64
ecs_status_command ecs-http True 64
plane_snapshot api-http True 64
runtime_inbox_claim postgresql True 64
```

- [ ] **Step 4: Run the benchmark artifact gate in-process**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

artifact = json.loads(Path("reports/phase3/phase3-production-benchmark.json").read_text(encoding="utf-8"))
validation = RuntimeBenchmarkGate().validate_artifact(artifact)
print(validation.valid, validation.reason)
PY
```

Expected:

```text
True OK
```

## Task 4: Validate Phase3 Production Closure

**Files:**

- Verify: `reports/phase3/phase3-p0-e2e.json`
- Verify: `reports/phase3/phase3-production-benchmark.json`
- Verify: `reports/phase3/evidence/**`
- Test: `tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py`
- Test: `tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py`
- Test: `tests/runtime/orchestration/test_phase3_closure_evidence_gate.py`

- [ ] **Step 1: Run Phase3 production closure gate**

Run:

```bash
uv run python scripts/check_phase3_closure_gate.py \
  --closure-profile production \
  --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --benchmark-artifact reports/phase3/phase3-production-benchmark.json
```

Expected:

```text
Phase 3 closure evidence passed
```

- [ ] **Step 2: Run artifact composer and closure regression tests**

Run:

```bash
uv run pytest \
  tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py \
  tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py \
  tests/runtime/orchestration/test_phase3_closure_evidence_gate.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Confirm Phase5 business advances past Phase3 blocker**

Run:

```bash
uv run python scripts/check_phase5_readiness_gate.py \
  --lane business \
  --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json \
  --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
```

Expected current result:

```text
Phase 5 readiness failed: MISSING_PHASE4_PRODUCTION_EVIDENCE
```

Acceptance: this command must not fail with `MISSING_PHASE3_PRODUCTION_CLOSURE`. If it fails at Phase4, Phase3 artifacts are complete and the remaining blocker is outside this plan.

## Task 5: Compose And Validate The Phase4 Production Evidence Artifact

**Files:**

- Generate: `reports/phase4/runtime-evidence-production.json`
- Read: `reports/phase4/evidence/phase4-runtime/provider-contracts/*.json`
- Read: `reports/phase4/evidence/phase4-runtime/traces/*.json`
- Read: `reports/phase4/evidence/phase4-runtime/benchmarks/phase4-runtime.json`
- Verify: `scripts/check_phase4_runtime_readiness_gate.py`

- [ ] **Step 1: Confirm required Phase4 evidence files exist**

Run:

```bash
test -f reports/phase4/evidence/phase4-runtime/provider-contracts/sorter-inbound.json
test -f reports/phase4/evidence/phase4-runtime/provider-contracts/smt-ng-wms-reconciliation.json
test -f reports/phase4/evidence/phase4-runtime/traces/effect-dispatch.json
test -f reports/phase4/evidence/phase4-runtime/traces/runtime-inbox-worker.json
test -f reports/phase4/evidence/phase4-runtime/traces/runtime-hold-reconciliation.json
test -f reports/phase4/evidence/phase4-runtime/benchmarks/phase4-runtime.json
```

Expected: command exits `0`.

- [ ] **Step 2: Generate the Phase4 production artifact through the composer**

Run:

```bash
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run python scripts/compose_phase4_runtime_evidence_artifact.py \
  --output reports/phase4/runtime-evidence-production.json \
  --profile production \
  --environment field-production \
  --generated-at "$GENERATED_AT" \
  --evidence-dir evidence/phase4-runtime
```

Expected:

```text
Phase 4 runtime evidence artifact written: reports/phase4/runtime-evidence-production.json
```

- [ ] **Step 3: Run the Phase4 production readiness gate**

Run:

```bash
uv run python scripts/check_phase4_runtime_readiness_gate.py \
  --readiness-profile production \
  --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json \
  --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --benchmark-artifact reports/phase3/phase3-production-benchmark.json
```

Expected:

```text
Phase 4 runtime readiness evidence gate passed: reason=PHASE4_RUNTIME_EVIDENCE_READY evidence_profile=production
```

- [ ] **Step 4: Run Phase4 evidence contract tests**

Run:

```bash
uv run pytest \
  tests/contracts/test_phase4_runtime_evidence_artifact_composer.py \
  tests/contracts/test_phase4_runtime_readiness_gate.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Confirm Phase5 business advances past Phase3 and Phase4 evidence blockers**

Run:

```bash
uv run python scripts/check_phase5_readiness_gate.py \
  --lane business \
  --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json \
  --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
```

Expected current result:

```text
Phase 5 readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN
details=phase5_business_lane_status
```

Acceptance: this command must not fail with `MISSING_PHASE3_PRODUCTION_CLOSURE` or `MISSING_PHASE4_PRODUCTION_EVIDENCE`.

## Task 6: Write The Tracked Evidence Bundle Ledger

**Files:**

- Create: `docs/architecture/phase3-phase4-production-evidence-bundle.md`

- [ ] **Step 1: Create the ledger with commands and gate outputs**

Add this document structure:

````markdown
# Phase3 + Phase4 Production Evidence Bundle

## Status

- Phase3 P0 E2E artifact: ready
- Phase3 benchmark artifact: ready
- Phase3 production closure gate: passed
- Phase4 production evidence artifact: ready
- Phase4 production readiness gate: passed
- Phase5 business next blocker: legacy matrix business close

## Artifact Paths

| Artifact | Path | Generated By |
| --- | --- | --- |
| P0 E2E | `reports/phase3/phase3-p0-e2e.json` | `scripts/compose_phase3_p0_e2e_artifact.py` |
| Benchmark | `reports/phase3/phase3-production-benchmark.json` | `scripts/compose_phase3_runtime_benchmark_artifact.py` |
| Phase4 runtime evidence | `reports/phase4/runtime-evidence-production.json` | `scripts/compose_phase4_runtime_evidence_artifact.py` |

## Evidence Roots

| Evidence | Path |
| --- | --- |
| P0 E2E trace recording | `reports/phase3/evidence/p0-e2e/source.json` |
| P0 E2E exception evidence | `reports/phase3/evidence/p0-e2e/*.json` |
| Benchmark scenario evidence | `reports/phase3/evidence/benchmark/*.json` |
| Phase4 runtime evidence | `reports/phase4/evidence/phase4-runtime/**` |

## Validation

```text
uv run python scripts/check_phase3_closure_gate.py --closure-profile production --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json
Phase 3 closure evidence passed
uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json
Phase 4 runtime readiness evidence gate passed: reason=PHASE4_RUNTIME_EVIDENCE_READY evidence_profile=production
uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
Phase 5 readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN
```

## Boundary

`reports/` is ignored by Git. Raw artifacts are local/CI/field evidence outputs, not source code. This ledger records the reproducible commands, SHA256 values and gate results; do not commit raw evidence unless release governance explicitly requires an artifact snapshot.
````

- [ ] **Step 2: Verify the ledger has no stale blocker text**

Run:

```bash
rg -n "MISSING_PHASE3_PRODUCTION_CLOSURE|MISSING_PHASE4_PRODUCTION_EVIDENCE|缺 phase3-p0-e2e-artifact|缺 phase3-benchmark-artifact" docs/architecture/phase3-phase4-production-evidence-bundle.md || true
```

Expected: no output.

## Task 7: Update Phase5 Planning Documents

**Files:**

- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`

- [ ] **Step 1: Update legacy cleanup execution plan status**

Edit `docs/architecture/legacy-cleanup-execution-plan.md` so the Phase5 business blocker says:

```markdown
Phase3 production closure artifacts and Phase4 production evidence artifacts can now be generated from `reports/**/evidence/**` and pass their production gates.
Phase5 business lane remains blocked by legacy matrix business close, not by missing Phase3 provenance or Phase4 evidence hashes.
```

- [ ] **Step 2: Update the top-level architecture status**

Edit `docs/architecture/workline-and-plugin-restructuring.md` in §10.0.1 / §10.6 so the Phase5 status says:

```markdown
Phase5 business lane no longer uses `MISSING_PHASE3_PRODUCTION_CLOSURE` or `MISSING_PHASE4_PRODUCTION_EVIDENCE` as the current expected blocker after regenerated Phase3/Phase4 artifacts are supplied. The next expected blocker is `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN`.
```

- [ ] **Step 3: Check the docs point to the provenance ledger**

Run:

```bash
rg -n "phase3-phase4-production-evidence-bundle.md|reports/phase3/phase3-p0-e2e.json|reports/phase3/phase3-production-benchmark.json|reports/phase4/runtime-evidence-production.json" docs/architecture/legacy-cleanup-execution-plan.md docs/architecture/workline-and-plugin-restructuring.md
```

Expected: both architecture docs reference either the ledger or both artifact paths.

## Task 8: Final Verification And Commit

**Files:**

- Verify: `docs/architecture/phase3-phase4-production-evidence-bundle.md`
- Verify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Verify: `docs/architecture/workline-and-plugin-restructuring.md`
- Generated but normally untracked: `reports/phase3/phase3-p0-e2e.json`
- Generated but normally untracked: `reports/phase3/phase3-production-benchmark.json`
- Generated but normally untracked: `reports/phase4/runtime-evidence-production.json`

- [ ] **Step 1: Run focused verification**

Run:

```bash
uv run python scripts/check_phase3_closure_gate.py \
  --closure-profile production \
  --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --benchmark-artifact reports/phase3/phase3-production-benchmark.json

uv run pytest \
  tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py \
  tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py \
  tests/runtime/orchestration/test_phase3_closure_evidence_gate.py \
  -q

uv run python scripts/check_phase4_runtime_readiness_gate.py \
  --readiness-profile production \
  --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json \
  --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --benchmark-artifact reports/phase3/phase3-production-benchmark.json

uv run pytest \
  tests/contracts/test_phase4_runtime_evidence_artifact_composer.py \
  tests/contracts/test_phase4_runtime_readiness_gate.py \
  -q

uv run pytest tests/contracts/test_phase5_readiness_gate.py -q

./scripts/git-quality-gate.sh --profile quality
```

Expected:

- Phase3 closure gate prints `Phase 3 closure evidence passed`.
- Phase4 readiness gate prints `Phase 4 runtime readiness evidence gate passed`.
- Selected Phase3, Phase4 and Phase5 contract tests pass.
- Quality profile passes.

- [ ] **Step 2: Confirm Phase5 business blocker moved past Phase3**

Run:

```bash
uv run python scripts/check_phase5_readiness_gate.py \
  --lane business \
  --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json \
  --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json \
  --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
```

Expected current result:

```text
Phase 5 readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN
details=phase5_business_lane_status
```

Acceptance: if the reason is `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN`, this plan succeeded. If the reason is `MISSING_PHASE3_PRODUCTION_CLOSURE` or `MISSING_PHASE4_PRODUCTION_EVIDENCE`, return to Task 2, Task 3 or Task 5.

- [ ] **Step 3: Review Git status**

Run:

```bash
git status --short
```

Expected tracked changes:

```text
 M docs/architecture/legacy-cleanup-execution-plan.md
 M docs/architecture/workline-and-plugin-restructuring.md
?? docs/architecture/phase3-phase4-production-evidence-bundle.md
```

`reports/phase3/**` and `reports/phase4/**` should not appear unless the team intentionally changes artifact retention policy.

- [ ] **Step 4: Commit tracked documentation**

Before commit, if any Python function, class, or method was changed while executing this plan, run GitNexus impact analysis and `gitnexus_detect_changes()` per AGENTS.md. If only reports and docs changed, record that no code symbols were modified.

Run:

```bash
git add \
  docs/architecture/phase3-phase4-production-evidence-bundle.md \
  docs/architecture/legacy-cleanup-execution-plan.md \
  docs/architecture/workline-and-plugin-restructuring.md

git commit -m "docs(runtime): 补齐 Phase3/Phase4 production evidence 账本"
```

Expected: commit succeeds with only tracked docs staged.

## Self-Review

Spec coverage:

- `phase3-p0-e2e-artifact`：Task 2 generates and validates it with existing composer and P0 E2E gate.
- `phase3-benchmark-artifact`：Task 3 generates and validates it with existing composer and benchmark gate.
- Phase3 production closure：Task 4 validates both artifacts together with `RuntimePhase3ClosureGate`.
- Phase4 production evidence：Task 5 generates and validates `reports/phase4/runtime-evidence-production.json` with production evidence hashes.
- Phase5 business handoff：Task 5 and Task 8 confirm the blocker moves beyond `MISSING_PHASE3_PRODUCTION_CLOSURE` and `MISSING_PHASE4_PRODUCTION_EVIDENCE`.
- Provenance documentation：Task 6 and Task 7 record reproducible commands, SHA256 values and remaining blockers without committing raw `reports/` data.

Placeholder scan:

- No placeholder sections remain.
- Every command has an expected result.
- Every file path is explicit.
- No task depends on an unnamed future implementation.

Type and naming consistency:

- Artifact argument names match `scripts/check_phase5_readiness_gate.py`: `--phase3-p0-e2e-artifact`, `--phase3-benchmark-artifact`.
- Phase3 closure argument names match `scripts/check_phase3_closure_gate.py`: `--p0-e2e-artifact`, `--benchmark-artifact`.
- Phase4 readiness argument names match `scripts/check_phase4_runtime_readiness_gate.py`: `--phase4-runtime-evidence-artifact`, `--p0-e2e-artifact`, `--benchmark-artifact`.
- Required benchmark scenario names match `RuntimeBenchmarkGate`: `runtime_inbox_claim`, `conveyor_queue_writer`, `ecs_status_command`, `plane_snapshot`.
- Required P0 exception path names match `RuntimeP0E2EArtifactComposer`: `callback_out_of_order`, `ecs_timeout`, `wms_reject`.
