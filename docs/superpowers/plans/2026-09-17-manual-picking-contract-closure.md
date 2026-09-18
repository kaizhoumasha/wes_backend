# Manual-Picking Contract Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to run the
> single remaining task below. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the approved/manual-picking contract boundary without implementing the
unapproved direct-pick operation, and prevent unsupported `added_direct_picks` plans from
entering an unfinishable execution state.

**Status as of 2026-09-18:** Tasks 1-5 of the original plan are already implemented and
merged on `develop` via commit `f65ca376` ("人工分拣新增 Plan Admission Policy 校验") plus
same-day direct edits to the contract doc and the manual-picking README. See "What already
exists" below for line-by-line evidence. Task 6's PostgreSQL transaction suite has now also
run clean (23 passed) against an isolated Postgres on the remote joint-debug host — see
Task 6 Step 2. **Remaining before final closure: Task 6 Steps 1 and 3 (fresh FAST re-run +
quality gate + HEAVY selection on the final tree) and Step 4/5 (residual scan + commit
hygiene + status record).** Do not re-run Tasks 1-5's RED/GREEN steps — the code and tests
they describe already exist and are already GREEN; treating them as pending work risks an
executor mistaking a passing suite for a broken environment, or colliding with existing
test names while trying to "add" tests that already exist.

**Architecture:** Shared host owns WMS plan-delta persistence, Evidence, idempotency, and
transaction ownership. A typed, pure plugin admission policy runs before `apply_plan()` in
the existing locked transaction; `manual-picking` rejects unsupported direct-pick members
and the host persists the rejection via existing Evidence semantics. Direct-pick execution
remains outside this plan until §5.5 is separately approved — it still carries a
`DRAFT / NOT AUTHORIZED` gate in the contract doc and is not registered anywhere in code.

**Tech Stack:** Python 3.13, SQLModel/SQLAlchemy, Pydantic strict wire models,
pytest/pytest-asyncio, existing WES plugin SDK and static deployment composition.

**Spec:** `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §6 and
the accepted D1-D6 review decisions; supporting constraints in
`docs/superpowers/plans/2026-09-16-wms-picking-task-cancellation.md` §5.5.4 and §17.

## Global Constraints

- API → Service → Repository → Database; the shared host must not import `manual-picking`
  business rules. (Verified: `picking_task_plan_delta.py` only imports SDK types.)
- The plugin owns business admission; the host owns Evidence, idempotency, transaction
  boundaries, retry, and response mapping.
- Rejection happens before `apply_plan()` persists plan members, in the same transaction as
  Evidence acceptance. (Verified: `_admission_reason()` runs before `apply_plan()` in
  `record()`.)
- Do not register or implement `outbound.manual_rack.direct_pick_completed@v1` before
  contract §5.5 approval. (Verified: zero hits for `direct_pick_completed` under
  `workline_plugins/manual-picking/src`, `src/app/wms_adapter/outbound_picking`,
  `deployment`, `tests/contracts/wms_adapter/outbound_picking`.)
- No compatibility wrappers, dynamic registries, a second Evidence/Confirmation path, or a
  new direct-pick execution model exist or are to be added.
- Existing approved Bin/SCAN/RETURN_BUFFER behavior is unchanged (covered by existing
  plugin FAST suites, untouched by this closure).
- All commands use `uv run`; plugin tests stay under `workline_plugins/manual-picking/tests/`,
  shared contract tests stay under `tests/contracts/wms_adapter/` or
  `tests/integration/wms_adapter/`.
- No migration is expected for this closure; none was introduced by the already-merged work.

## What already exists

| Item the original plan asked for | Where it already lives | Evidence |
| --- | --- | --- |
| Task 1: §6 status matrix (4-state: contract/code/automated/field), §1.1/§3.5/§5.5 `DRAFT / NOT AUTHORIZED` gates, §5.5 field definitions, §8 test-owner reconciliation | `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §1.1 (line 24, 40), §3.5 (line 254), §5.5 (line 425-473), §6 (line 579-598), §8 (line 600-659) | Read directly; all gates and fields present, §8 lists the exact 9 test names Task 3 asked for |
| Task 1: manual-picking README standard-component matrix | `workline_plugins/manual-picking/README.md` "双向合同验证（2026-09-17）" section | Bidirectional matrix present, `direct_pick_completed` marked `OUT` per §6 |
| Task 2: `PickingTaskPlanAdmissionFact` / `Decision` / `DecisionKind` / `Policy` (Protocol) | `src/wes_plugin_sdk/src/wes_plugin_sdk/picking_task_plan.py:117-170` | Read directly; matches plan's Task 2 interface spec field-for-field |
| Task 2: SDK contract tests | `src/wes_plugin_sdk/tests/test_picking_task_plan.py` | Ran: 5 passed |
| Task 3: pre-apply admission wired into `PickingTaskPlanDeltaService`, `composition.py`, `register.py`, `InstalledWorkLinePlugin` | `src/app/wms_integration/outbound_picking/services/picking_task_plan_delta.py:113-116,236-268`, `composition.py:22,42,56`, `src/register.py:106-117`, `src/app/workline/installed_plugin.py:14,40` | Read directly; admission call sits between `validate_plan` and `apply_plan`, static mapping keyed by `(plugin_key, plugin_version)` |
| Task 3: shared contract + PostgreSQL tests | `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py`, `test_plan_delta_event_handler.py`; `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py` | Ran FAST suite: 59 passed. PostgreSQL suite exists with the exact named tests (`test_custom_admission_reason_replays_and_drift_stays_idempotency_conflict`, `test_payload_drift_before_admission_rejection_does_not_hide_plugin_reason`, `test_admission_policy_exception_rolls_back_evidence_task_and_members`); **not yet run this session — needs an isolated PostgreSQL environment (see Task 6 Step 2)** |
| Task 4: `ManualPickingPlanAdmissionPolicy`, static assembly in `build_plugin()` | `workline_plugins/manual-picking/src/manual_picking/application/plan_admission.py`, wired via `plugin.py` | Read directly; rejects `has_direct_picks` with `MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED`, matches plan spec exactly |
| Task 4: plugin policy + declaration/allowlist tests | `workline_plugins/manual-picking/tests/test_plan_admission.py`, `test_declaration.py` | Ran: 7 passed |
| Task 5: contract §8 test-ownership table, C1-C7 owner map | `docs/contracts/...md` §8.1-8.4, §11.1 | Already reconciled to real files, marked `EXISTS` where applicable, explicitly caveats PostgreSQL/E2E/field acceptance as separate |
| Topology/collection guardrails | `tests/architecture/test_suite_topology_guardrail.py`, `test_core_plugin_test_ownership_guardrail.py` | Ran: 15 passed |
| Forbidden-path residual scan | `direct_pick_completed`/`MANUAL_RACK`/`DirectPickExecution` scoped to plugin src + adapter + deployment + contract tests | Ran: zero forbidden hits; `DirectPickExecution` appears only as plan-delta model/guard usage, as expected |

The plan reuses all of the above; nothing here needs to be rebuilt.

## NOT in scope

- Implementing `outbound.manual_rack.direct_pick_completed@v1` — requires separate contract
  approval and a separate plan; still gated `DRAFT / NOT AUTHORIZED` in the contract doc.
- Persisting a direct-pick completion model, `completed_at` application state, or rack-face
  departure application.
- `WORKLINE_STOPPING` RETURN_BUFFER drain — owned by `WorkLineConfigurationService._trigger_plugin_drain`,
  untouched by this closure. Plugin switching is no longer a planned trigger for this project.
- PDA integration, Cell-level business data, supplier ECS/PLC protocols, and field
  acceptance.
- New migration, new outbox/confirmation type, dynamic plugin registry, compatibility path,
  or direct-pick transport template — none exist and none are introduced here.
- Restating a full PASS/FAIL status table inside this plan file: §6/§8 of the contract doc
  and the README's bidirectional matrix remain the single status source (plan-eng-review
  D2, 2026-09-17) — duplicating it here would create a third drift-prone copy.

## Failure modes

| Failure | Test | Handling | User-visible result |
| --- | --- | --- | --- |
| Policy rejects after member write | PostgreSQL atomicity test (`test_admission_policy_exception_rolls_back_evidence_task_and_members`) | Pre-apply call inside the same transaction as member persistence | WMS receives deterministic rejection; no orphan member — **verified 2026-09-18, PASSED against isolated remote PostgreSQL** |
| Same rejected identity is retried | `test_plan_admission_replay_returns_first_reason_without_reinvoking_policy` (FAST, verified GREEN) | Replay original reason/evidence; payload drift is conflict | WMS can retry without creating a second plan |
| Plugin identity has no admission policy | `test_direct_pick_remains_shared_behavior_without_a_policy` (FAST, verified GREEN) | Host preserves current generic behavior | Other plugins are not blocked by manual-picking policy |
| Direct-pick operation accidentally registered | Declaration/allowlist test + residual `rg` scan (both verified this session: GREEN / zero hits) | Static operation tuple excludes it | Event is not routed to an unapproved consumer |
| Closure commit accidentally sweeps in unrelated in-progress files | N/A — process control, not a test | Task 6 Step 4 requires explicit `git add <path>` per file, never `git add -A`/`git add .` (plan-eng-review D-Issue1, 2026-09-17) | Unrelated `inbound_evidence.py` / `inbound_evidence_service.py` / `confirmation_support.py` diffs stay out of the closure commit |

## Task 6: Final Verification and Contract Closure

**Files:** none modified except this plan file (record of results) and, only if a genuinely
new runtime/integration asset is found, `docs/architecture/heavy-test-impact.toml`.

- [x] **Step 1: Run the FAST focused suites on the current tree.**

  **Done 2026-09-18**, re-run on the final tree (not reusing the 2026-09-17 review-session
  numbers).

  ```bash
  uv run pytest tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py \
    tests/contracts/wms_adapter/outbound_picking/test_plan_delta_event_handler.py -q -o addopts=''
  uv run --extra manual-picking pytest workline_plugins/manual-picking/tests -m "not integration" -q -o addopts=''
  uv run pytest src/wes_plugin_sdk/tests/test_picking_task_plan.py -q -o addopts=''
  ```
  Result: **59 passed** / **332 passed, 15 deselected** (full plugin FAST set, not just the
  declaration+admission subset) / **5 passed**.

- [x] **Step 2: Run the PostgreSQL transaction tests.**

  **Done 2026-09-18.** Ran against an isolated, tmpfs, throwaway Postgres/Redis
  (`docker-compose.ci-heavy.yml` + `.ci-heavy.local.yml`) started on the remote docker
  engine at `CANTAISYS@100.94.216.118` via `DOCKER_HOST=ssh://...` (the shared
  `wes_integration` compose project on that host was never touched — separate compose
  project, separate network, torn down with `down --volumes` after the run), reached from
  this machine through an SSH `-L` port-forward tunnel. No mocks were needed beyond what
  the test file already uses internally (`unittest.mock.Mock`) — this suite has no external
  WMS/ECS dependency.

  ```bash
  uv run pytest tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py -q -o addopts=''
  ```
  Result: **23 passed** on a clean `develop` tree (all admission-related tests green,
  including `test_custom_admission_reason_replays_and_drift_stays_idempotency_conflict`,
  `test_payload_drift_before_admission_rejection_does_not_hide_plugin_reason`,
  `test_admission_policy_exception_rolls_back_evidence_task_and_members`).

  **Side finding (out of scope for this closure, flagged for the owner of that WIP):** the
  first run of this suite, taken against the working tree as it stood mid-review (with the
  3 unrelated uncommitted edits noted in Failure modes below still present), showed 8
  failures — all `CONFLICT` where `DUPLICATE` was expected. Root cause isolated by stashing
  those 3 files and re-running: the WIP change to
  `src/app/execution/services/inbound_evidence_service.py` adds a new
  `picking_task_id: int | None = None` parameter to `InboundEvidenceService.accept()` and
  folds `existing.picking_task_id` into the replay-equality tuple. `PickingTaskPlanDeltaService.record()`
  backfills `evidence.picking_task_id = task.id` on the ORM object *after* `accept()` returns,
  never through the `accept()` parameter — so on any replay of the same `operation_id`,
  `existing.picking_task_id` (already backfilled from the first call) never matches the
  incoming call's `picking_task_id=None`, and every idempotent replay is misclassified as a
  content conflict. This is a real regression risk in that WIP, not in anything this
  closure plan touches — whoever finishes that change needs to either thread
  `picking_task_id` through the callers that backfill it, or exclude it from the
  replay-equality tuple.

- [x] **Step 3: Run topology, collection guards, quality, and HEAVY verification.**

  ```bash
  uv run pytest tests/architecture/test_suite_topology_guardrail.py \
    tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
  uv run pytest --collect-only -q -o addopts='' | tail -5
  ./scripts/git-quality-gate.sh --profile quality
  uv run scripts/select_heavy_tests.py --scope unstaged
  ./scripts/run_selected_heavy_local.sh --scope unstaged
  ```
  **Done 2026-09-18.** Topology/ownership guardrails: **15 passed**. Full collection:
  **4103 tests collected**, no collection errors. Quality gate (`--profile quality`):
  **4098 passed, 5 skipped**, FAST speed budget passed, `[quality] Profile "quality"
  passed.`. HEAVY selection: run with the 3 unrelated WIP files (`inbound_evidence.py`,
  `inbound_evidence_service.py`, `confirmation_support.py`) temporarily stashed, since
  `--scope unstaged` otherwise mixes that unrelated diff into the selection — with only
  this plan's own change (a docs file) in the working tree, the selector returned **zero**
  HEAVY tests (exit 0, empty output), so `run_selected_heavy_local.sh` had nothing to run.
  Correct result: this closure changes no runtime/integration asset. Stash popped back
  immediately after; no `heavy-test-impact.toml` mapping was needed.

- [x] **Step 4: Scan for forbidden direct-pick activation, then commit by explicit path only.**

  ```bash
  rg -n "direct_pick_completed|MANUAL_RACK|DirectPickExecution" \
    workline_plugins/manual-picking/src src/app/wms_adapter/outbound_picking \
    deployment tests/contracts/wms_adapter/outbound_picking
  git status --short
  ```
  **Done 2026-09-18.** `DirectPickExecution` appears only in plan-delta data/guard usage
  (`completion_repository.py`, `test_plan_delta_service.py`); zero `direct_pick_completed`
  or `MANUAL_RACK` hits. `git status` still shows the 3 unrelated in-progress files —
  staged and committed **only** `docs/superpowers/plans/2026-09-17-manual-picking-contract-closure.md`
  by explicit path; never ran `git add -A`/`git add .` (plan-eng-review D-Issue1,
  2026-09-17).

- [x] **Step 5: Record closure status.**

  Contract closure is complete as of **2026-09-18**:
  - §6's C1-C7 four-state matrix and the README's bidirectional matrix (both current as of
    2026-09-17, re-checked 2026-09-18) have no contradictory `APPROVED`/`ReviewRequired`
    claims — read directly, not re-derived here (plan-eng-review D-Issue2, 2026-09-17).
  - Direct-pick draft sections (§1.1, §3.5, §5.5) remain `DRAFT / NOT AUTHORIZED` on the
    final tree.
  - Unsupported direct-pick plan members are rejected before persistence in an atomic
    transaction — confirmed by Step 2's PostgreSQL run (23 passed, including the atomicity
    test).
  - Steps 1-4 above passed on the final tree with fresh, this-session evidence (FAST: 59 +
    332 + 5 passed; PostgreSQL: 23 passed; guardrails: 15 passed; full collection: 4103
    tests, 0 errors; quality gate: 4098 passed/5 skipped; HEAVY: 0 tests selected, correctly,
    for a docs-only diff).
  - The approved Bin path remains green (332 plugin FAST tests passing) and no direct-pick
    operation is registered (Step 4 scan).

  One follow-up surfaced during closure, tracked separately, not blocking: the unrelated WIP
  change to `InboundEvidenceService.accept()`'s `picking_task_id` parameter has a replay
  regression (Task 6 Step 2's "Side finding") — that WIP's own owner needs to fix it before
  it is committed; it was never part of this plan's scope and was excluded from the closure
  commit.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 2 issues, 0 critical gaps, 0 unresolved |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | backend-only, not applicable |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run |
| Outside Voice | codex/claude second opinion | Independent read | 0 | — | not run (single-task closure plan; skipped by reviewer judgment given the dominant finding — plan staleness — was independently confirmed by reading source, tests, and git history directly, not by model opinion) |

### Completion Summary

- Step 0: Scope Challenge — scope reduced: the plan's Tasks 1-5 (15+ files, 4 new SDK
  types) were found already implemented and merged (commit `f65ca376` plus same-day doc/
  README edits); user chose to rewrite the plan into a Task-6-only closure checklist rather
  than re-execute or discard it.
- Architecture Review: 0 new issues — the already-shipped design (typed pre-apply admission
  port, static composition, no dynamic registry) matches the plan's stated constraints;
  verified directly by reading `picking_task_plan_delta.py`, `composition.py`,
  `register.py`, `installed_plugin.py`, and the plugin's `plan_admission.py`.
- Code Quality Review: 2 issues found — (1) closure commit could accidentally sweep in 3
  unrelated uncommitted files if staged with `git add -A`; (2) restating a full status
  table in the plan would create a third drift-prone source alongside the contract doc's
  §6/§8 and the README's bidirectional matrix. Both resolved: explicit-path staging only,
  and the plan now links to the contract doc/README instead of duplicating their tables.
- Test Review: all 9 test names the original plan's Task 3 asked for already exist and are
  named exactly as specified; FAST subset verified GREEN this session (59 + 7 + 5 = 71
  passed). PostgreSQL suite exists with the 3 named integration tests but was not run this
  session (needs isolated PostgreSQL env) — carried into Task 6 Step 2 as the one concrete
  remaining gap.
- Performance Review: 0 issues — admission policy is a pure, synchronous, O(1) dict lookup
  with no I/O, matching the plan's own constraint that policies must not touch the database.
- NOT in scope: written.
- What already exists: written, with file:line evidence for every item the original plan's
  Tasks 1-5 asked for.
- TODOS.md updates: 0 — nothing new surfaced; the plan's own `NOT in scope` items already
  map to the existing RETURN_BUFFER TODO.
- Failure modes: 5 paths listed, 0 critical gaps (one path — PostgreSQL atomicity — has a
  test but no verified-this-session GREEN result; that's a known, already-scheduled gap,
  not a silent one).
- Outside voice: skipped (see table above).
- Parallelization: none — single remaining task, sequential by construction.
- Lake Score: N/A — no build-vs-shortcut tradeoff was on the table this round; the only
  decisions were process-hygiene ones (commit scope, single source of truth).
- Unresolved decisions: 0.

NO UNRESOLVED DECISIONS
