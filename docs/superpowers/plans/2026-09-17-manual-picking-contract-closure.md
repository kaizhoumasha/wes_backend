# Manual-Picking Contract Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the approved/manual-picking contract boundary without implementing the unapproved direct-pick operation, and prevent unsupported `added_direct_picks` plans from entering an unfinishable execution state.

**Architecture:** Keep WMS plan-delta persistence, Evidence, idempotency, and transaction ownership in the shared host. Add one typed, pure plugin admission policy that the host invokes before `apply_plan()` in the existing locked transaction. `manual-picking` rejects unsupported direct-pick members; the host persists the rejection and response using existing Evidence semantics. Direct-pick execution remains outside this plan until §5.5 is separately approved.

**Tech Stack:** Python 3.13, SQLModel/SQLAlchemy, Pydantic strict wire models, pytest/pytest-asyncio, existing WES plugin SDK and static deployment composition.

**Spec:** `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §6 and the accepted D1-D6 review decisions; supporting constraints in `docs/superpowers/plans/2026-09-16-wms-picking-task-cancellation.md` §5.5.4 and §17.

## Global Constraints

- API → Service → Repository → Database; the shared host must not import `manual-picking` business rules.
- The plugin owns business admission; the host owns Evidence, idempotency, transaction boundaries, retry, and response mapping.
- Rejection must happen before `apply_plan()` persists plan members, in the same transaction as Evidence acceptance.
- Do not register or implement `outbound.manual_rack.direct_pick_completed@v1` before contract §5.5 approval.
- Do not add compatibility wrappers, dynamic registries, a second Evidence/Confirmation path, or a new direct-pick execution model.
- Existing approved Bin/SCAN/RETURN_BUFFER behavior must remain unchanged.
- All commands use `uv run`; plugin tests stay under `workline_plugins/manual-picking/tests/`, shared contract tests stay under `tests/contracts/wms_adapter/` or `tests/integration/wms_adapter/`.
- No migration is expected. If implementation proves a schema change necessary, stop and update the plan before editing models or migrations.

## Current Boundaries

```text
WMS plan_delta event
        |
        v
shared Evidence accept + task lock
        |
        +-- typed plugin admission policy (new, before apply_plan)
        |       +-- reject unsupported direct picks -> conflict Evidence/response
        |       +-- accept Bin-only plan -> existing apply_plan()
        |
        v
shared plan member persistence + post-commit plugin activation
```

The current implementation calls the plugin only after plan persistence. The plan changes that ordering through a narrow admission port; it does not move transport creation, WMS parsing, or business sequencing into the shared service.

## File Map

| Responsibility | Files |
| --- | --- |
| Approval/status source of truth | `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` |
| Public typed admission contract | `src/wes_plugin_sdk/src/wes_plugin_sdk/picking_task_plan.py`, `src/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py` |
| Shared transaction and composition | `src/app/wms_integration/outbound_picking/services/picking_task_plan_delta.py`, `src/app/wms_integration/outbound_picking/composition.py`, `src/app/workline/installed_plugin.py`, `src/register.py` |
| Manual-picking admission policy | `workline_plugins/manual-picking/src/manual_picking/application/plan_admission.py`, `workline_plugins/manual-picking/src/manual_picking/application/plugin.py` |
| Shared contract tests | `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py`, `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py` |
| Plugin tests | `workline_plugins/manual-picking/tests/test_plan_admission.py`, `test_declaration.py`, `test_plan_applied_handler.py` |
| Test topology/HEAVY references | `docs/architecture/heavy-test-impact.toml` only if a runtime or integration asset changes its HEAVY surface |

## Task 1: Freeze the §6 Contract Status Matrix

**Files:**
- Modify: `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §1.1, §3.5, §5.5, §6, §8, §11 and final review report if present
- Modify: `workline_plugins/manual-picking/README.md` standard-component matrix

**Interfaces:**
- Produces the authoritative mapping for C1-C7, with separate columns for contract approval, code implementation, automated acceptance, and field acceptance.
- Marks §3.5/§5.5 and the direct-pick branch in §1.1 as `DRAFT / NOT AUTHORIZED` until a later approval.

- [ ] **Step 1: Inventory every status claim and C1-C7 reference.**

  Run:
  ```bash
  rg -n "C[1-7]|APPROVED|ReviewRequired|direct_pick_completed|联合评审|代码实施授权" \
    docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  ```
  Record each occurrence before editing; do not silently delete historical review evidence.

- [ ] **Step 2: Write the status matrix.**

  Define C1-C7 by concrete operation/behavior, not historical shorthand. At minimum map point2 admission, point2 completion ingress, local completion application, SCAN/NG routing, FIFO enqueue, task completion/drain, and static composition/worker acceptance. Mark each status independently and keep direct-pick outside C1-C7 until approved.

- [ ] **Step 3: Gate the draft sections.**

  Add a visible status note directly below the §1.1 direct-pick branch, §3.5 heading, and §5.5 heading. The note must state that the wire text is draft material, cannot be implemented, and is excluded from current E2E acceptance.

- [ ] **Step 4: Repair §5.5 field definitions.**

  Replace the malformed rows with explicit definitions for `data.task_id`, `data.rack_id`, `data.rack_face`, `data.completed_at`, and the envelope `timestamp`; state identifier constraints, `completed_at <= timestamp`, strict-object/extra-field behavior, and the future `task_id + rack_id + rack_face` terminal identity. Do not register the operation in code as part of this task.

- [ ] **Step 5: Reconcile the test-owner table.**

  Replace nonexistent paths with current owners or mark a future owner as `PLANNED`, never as an executed acceptance result. Add the new direct-pick admission/allowlist tests from Tasks 2-4 only after those tests exist.

- [ ] **Step 6: Verify the document-only change.**

  Run:
  ```bash
  git diff --check
  ./scripts/git-quality-gate.sh --check release-metadata
  ```
  Expected: no whitespace errors; release metadata remains unchanged.

## Task 2: Add the Typed Pre-Apply Admission Contract

**Files:**
- Modify: `src/wes_plugin_sdk/src/wes_plugin_sdk/picking_task_plan.py`
- Modify: `src/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py`
- Modify: `src/app/workline/installed_plugin.py`
- Test: `src/wes_plugin_sdk/tests/test_picking_task_plan.py` (create if absent)

**Interfaces:**
- Add an immutable `PickingTaskPlanAdmissionFact` containing only the already validated plan identity needed for business admission: `task_id`, `plan_revision`, `has_direct_picks`, and `plugin_key/plugin_version` only if the existing SDK fact convention requires identity.
- Add a closed `PickingTaskPlanAdmissionDecision` with `ACCEPT` and `REJECT` plus a required stable `reason_code` for rejection; do not expose raw WMS payloads or database objects.
- Add `PickingTaskPlanAdmissionPolicy(Protocol)` with `__call__(fact) -> PickingTaskPlanAdmissionDecision`.
- Extend `InstalledWorkLinePlugin` with an optional admission policy, preserving `None` for plugins that do not need one.
- Extend `build_outbound_picking_runtime(..., plan_admission_policies: Mapping[tuple[str, str], PickingTaskPlanAdmissionPolicy] | None = None)` and pass the mapping to `PickingTaskPlanDeltaService`.

- [ ] **Step 1: Write SDK contract tests first.**

  Cover immutable construction, accepted Bin-only facts, direct-pick rejection decisions, invalid empty reason codes, and rejection reason stability. Assert the SDK types contain no ORM, HTTP, Celery, or plugin-specific imports.

- [ ] **Step 2: Run the SDK tests and confirm RED.**

  Run:
  ```bash
  uv run pytest src/wes_plugin_sdk/tests/test_picking_task_plan.py -q -o addopts=''
  ```
  Expected: FAIL because the new types and exports do not exist.

- [ ] **Step 3: Implement the smallest typed contract.**

  Keep the policy pure and synchronous. The policy must not inspect the database, query current WorkLine state, perform I/O, or decide transport behavior.

- [ ] **Step 4: Run the SDK tests and package import checks.**

  Run:
  ```bash
  uv run pytest src/wes_plugin_sdk/tests/test_picking_task_plan.py src/wes_plugin_sdk/tests/test_plugin_definition.py -q -o addopts=''
  ```
  Expected: PASS.

## Task 3: Invoke Admission Before `apply_plan()`

**Files:**
- Modify: `src/app/wms_integration/outbound_picking/services/picking_task_plan_delta.py`
- Modify: `src/app/wms_integration/outbound_picking/composition.py`
- Modify: `src/register.py` to pass the static plugin admission policies into the outbound-picking composition
- Test: `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py`
- Test: `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`

**Interfaces:**
- `PickingTaskPlanDeltaService(..., plan_admission_policies: Mapping[tuple[str, str], PickingTaskPlanAdmissionPolicy] | None = None)` receives a static mapping of plugin identity to policy. It must not import `manual_picking`.
- The service constructs the admission fact after strict DTO validation and task locking, before `apply_plan()`.
- On `REJECT`, the existing rejection path records the first Evidence/reason, keeps the operation identity for replay, does not call `apply_plan()`, does not call `add_members()`, and returns the existing deterministic conflict/rejection response.
- On `ACCEPT`, behavior remains byte-for-byte equivalent to the current path.

- [ ] **Step 1: Add the failing shared-service tests.**

  Add tests for: policy rejection before member persistence; same identity replay returning the original reason; same identity payload drift returning idempotency conflict; Bin-only acceptance still applying; plugin identity with no policy preserving current shared behavior; and policy exceptions rolling back Evidence/task/member writes.

- [ ] **Step 2: Run focused shared tests and confirm RED.**

  Run:
  ```bash
  uv run pytest tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py -q -o addopts=''
  ```
  Expected: new admission tests fail while existing plan-delta tests remain the baseline.

- [ ] **Step 3: Wire the policy lookup through static composition.**

  In `src/register.py`, build `{(plugin.definition.plugin_key, plugin.definition.plugin_version): plugin.picking_task_plan_admission_policy}` for non-`None` policies and pass it to `build_outbound_picking_runtime`. Do not create a dynamic registry or scan installed modules. The policy lookup must be keyed by the frozen WorkLine plugin identity, not the current default plugin.

- [ ] **Step 4: Implement the pre-apply call and reuse existing rejection semantics.**

  Place the call between `validate_plan(...)` and `apply_plan(...)`. Ensure a rejection never sets `last_applied_plan_revision`, `last_plan_evidence_id`, `ApplyStatus.APPLIED`, or `DirectPickExecution` rows.

- [ ] **Step 5: Run shared FAST tests.**

  Run:
  ```bash
  uv run pytest tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py \
    tests/contracts/wms_adapter/outbound_picking/test_plan_delta_event_handler.py -q -o addopts=''
  ```
  Expected: PASS.

- [ ] **Step 6: Run the PostgreSQL transaction tests.**

  Run only when the required isolated PostgreSQL environment is ready:
  ```bash
  uv run pytest tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py -q -o addopts=''
  ```
  Required evidence: rejection and member persistence roll back atomically; duplicate and conflict identities remain deterministic.

## Task 4: Implement the Manual-Picking Policy and Static Operation Guard

**Files:**
- Create: `workline_plugins/manual-picking/src/manual_picking/application/plan_admission.py`
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/plugin.py`
- Modify: `workline_plugins/manual-picking/tests/test_declaration.py`
- Create: `workline_plugins/manual-picking/tests/test_plan_admission.py`
- Modify: `workline_plugins/manual-picking/tests/test_plan_applied_handler.py` only for unchanged Bin-only behavior

**Interfaces:**
- `ManualPickingPlanAdmissionPolicy.__call__(fact) -> PickingTaskPlanAdmissionDecision` returns `REJECT/MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED` when `fact.has_direct_picks` is true, otherwise `ACCEPT`.
- `business_wms_operations` remains unchanged and must not contain `outbound.manual_rack.direct_pick_completed@v1`.

- [ ] **Step 1: Write plugin policy tests first.**

  Cover direct-pick-only rejection, mixed Bin/direct rejection, empty direct-pick acceptance, stable reason code, and policy purity using only SDK facts.

- [ ] **Step 2: Run plugin admission tests and confirm RED.**

  Run:
  ```bash
  uv run --extra manual-picking pytest workline_plugins/manual-picking/tests/test_plan_admission.py -q -o addopts=''
  ```
  Expected: FAIL before the policy exists.

- [ ] **Step 3: Implement and statically assemble the policy.**

  Instantiate it in `build_plugin()` and expose it through `InstalledWorkLinePlugin`; do not add an Event handler or direct-pick operation constant.

- [ ] **Step 4: Add declaration and allowlist assertions.**

  Assert the manual plugin has the policy, retains the approved operation set, and excludes `direct_pick_completed`.

- [ ] **Step 5: Run plugin FAST tests.**

  Run:
  ```bash
  uv run --extra manual-picking pytest workline_plugins/manual-picking/tests -m "not integration" -q -o addopts=''
  ```
  Expected: existing approved-path tests and new policy/allowlist tests pass.

## Task 5: Rebuild Contract Ownership and Integration Coverage

**Files:**
- Modify: `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §8 and §11
- Modify: `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py`
- Modify: `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`
- Modify: `workline_plugins/manual-picking/tests/test_scan_flow.py`, `test_completion_repository.py`, `test_declaration.py` where existing owners already cover the behavior
- Create: `workline_plugins/manual-picking/tests/test_plan_admission.py` from Task 4

**Interfaces:**
- The contract names only existing test owners or explicitly marked future owners.
- The approved Bin path remains owned by existing scan/completion/batch tests; no duplicate full matrix is added to plugin and core tests.

- [ ] **Step 1: Replace stale owner paths with actual owners.**

  Map each §8 row to a real test file and named test behavior. Remove claims that missing tests already prove acceptance.

- [ ] **Step 2: Add transaction and allowlist coverage.**

  Ensure the matrix explicitly covers: direct-pick-only rejection, mixed-source rejection, duplicate identity replay, payload conflict, no `DirectPickExecution` persistence on rejection, plugin-disabled/shared-ingress behavior, and absence of `direct_pick_completed` from manual-picking registration.

- [ ] **Step 3: Add the approved-path coverage map.**

  Map C1-C7 to existing scan, completion, batch, drain, declaration, and worker tests without creating duplicate tests for shared Evidence/Confirmation mechanics.

- [ ] **Step 4: Run focused contract and plugin tests.**

  Run:
  ```bash
  uv run pytest tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py \
    tests/contracts/wms_adapter/outbound_picking/test_plan_delta_event_handler.py -q -o addopts=''
  uv run --extra manual-picking pytest workline_plugins/manual-picking/tests/test_declaration.py \
    workline_plugins/manual-picking/tests/test_plan_admission.py \
    workline_plugins/manual-picking/tests/test_scan_flow.py \
    workline_plugins/manual-picking/tests/test_completion_repository.py -q -o addopts=''
  ```

## Task 6: Final Verification and Contract Closure

**Files:**
- Verify all files from Tasks 1-5
- Modify: `docs/architecture/heavy-test-impact.toml` only if the changed runtime/integration assets require a new precise mapping

- [ ] **Step 1: Run plugin and shared focused suites on the final code snapshot.**

  Re-run the commands from Tasks 3-5 after the last production/test change. Do not reuse pre-change green evidence.

- [ ] **Step 2: Run topology and collection guards.**

  ```bash
  uv run pytest tests/architecture/test_suite_topology_guardrail.py \
    tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
  uv run pytest --collect-only -q -o addopts='' | tail -5
  ```

- [ ] **Step 3: Run quality and selected HEAVY verification.**

  ```bash
  ./scripts/git-quality-gate.sh --profile quality
  uv run scripts/select_heavy_tests.py --scope unstaged
  ./scripts/run_selected_heavy_local.sh --scope unstaged
  ```
  If the selector reports a new runtime or integration asset, add the exact mapping before treating the result as final evidence.

- [ ] **Step 4: Scan for forbidden direct-pick activation.**

  ```bash
  rg -n "direct_pick_completed|MANUAL_RACK|DirectPickExecution" \
    workline_plugins/manual-picking/src src/app/wms_adapter/outbound_picking \
    deployment tests/contracts/wms_adapter/outbound_picking
  ```
  Expected: `DirectPickExecution` remains plan-delta data/guard coverage only; no `direct_pick_completed` adapter, handler, route registration, or DeviceCommand path exists.

- [ ] **Step 5: Record closure status.**

  Contract closure is complete only when:
  - §6 C1-C7 has four-state evidence and no contradictory `APPROVED`/`ReviewRequired` claims.
  - Direct-pick draft sections are visibly not authorized.
  - Unsupported direct-pick plan members are rejected before persistence in an atomic transaction.
  - Real test owners and commands in §8 exist and pass.
  - The approved Bin path remains green and no direct-pick operation is registered.

## NOT in scope

- Implementing `outbound.manual_rack.direct_pick_completed@v1`; this requires a separate contract approval and a separate plan.
- Persisting a direct-pick completion model, `completed_at` application state, or rack-face departure application.
- `WORKLINE_STOPPING` / `PLUGIN_SWITCHING` RETURN_BUFFER drain; the existing `TODOS.md` item remains the owner.
- PDA integration, Cell-level business data, supplier ECS/PLC protocols, and field acceptance.
- New migration, new outbox/confirmation type, dynamic plugin registry, compatibility path, or direct-pick transport template.

## What already exists

- Shared strict `plan_delta` parsing, Evidence acceptance, task locking, idempotency conflict handling, and member persistence.
- Static plugin composition and `InstalledWorkLinePlugin` runtime binding.
- `PickingTaskPlanAppliedHandler` for approved target/source rack transport intents.
- Manual-picking four-point scan flow, WMS admission/completion, FIFO, rack cycle, task completion, and drain behavior.
- Existing plugin FAST tests and shared plan-delta PostgreSQL tests; the plan repairs their ownership map instead of replacing them.

## Failure Modes

| Failure | Test | Handling | User-visible result |
| --- | --- | --- | --- |
| Policy rejects after member write | PostgreSQL atomicity test | Prevented by pre-apply call in the same transaction | WMS receives deterministic rejection; no orphan member |
| Same rejected identity is retried | Shared plan-delta contract test | Replay original reason/evidence; payload drift is conflict | WMS can retry without creating a second plan |
| Plugin identity has no admission policy | Shared composition test | Host preserves current generic behavior; no manual rule is applied | Other plugins are not blocked by manual-picking policy |
| Direct-pick operation accidentally registered | Declaration/allowlist test and residual scan | Static operation tuple excludes it | Event is not routed to an unapproved consumer |
| Approved Bin path regresses | Existing scan/batch/completion/plugin FAST suites | Focused and final QUALITY verification | Existing manual Bin workflow remains unchanged |

## Execution Order and Parallelization

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Task 1: contract status/doc repair | `docs/contracts/`, plugin README | — |
| Task 2: SDK admission types | `src/wes_plugin_sdk/` | — |
| Task 3: host pre-apply wiring | `src/app/wms_integration/outbound_picking/`, `deployment/` | Task 2 |
| Task 4: manual policy/allowlist | `workline_plugins/manual-picking/` | Task 2, Task 3 interface |
| Task 5: test ownership and integration coverage | `tests/`, plugin tests, contract docs | Tasks 1-4 |
| Task 6: final gates | scripts, HEAVY mapping, all affected modules | Task 5 |

Lane A: Task 1 (documentation only).

Lane B: Task 2 → Task 3 → Task 4 (shared typed interface and composition path).

Lane C: No independent implementation lane; Task 5 must wait for the final admission interface so tests assert the actual transaction boundary.

Execution order: start Task 1 and Task 2 in parallel worktrees only if the SDK and contract owners are separate; merge both, then execute Tasks 3-4 sequentially, then Tasks 5-6. Task 1 and Task 5 both edit the contract document, so they must not run concurrently.

## Review Checkpoints

- After Task 1: contract-only review confirms C1-C7/status wording and direct-pick draft gates.
- After Task 3: architecture review confirms the shared host still has no plugin import and rejection is atomic.
- After Task 5: test-owner review confirms every §8 claim maps to a collected test.
- Before completion: run the single final review and verification snapshot; do not claim contract closure from focused tests alone.
