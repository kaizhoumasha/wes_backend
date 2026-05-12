<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/wes_workspace/workline-sandbox-runtime-flow-autoplan-restore-20260508-210210.md -->

> Legacy notes: 本计划已被 2026-05-12 `RuntimeIntent` 旧链路清理方案取代；其中状态字段和状态机设计仅作历史记录。

# WES 插件状态架构大重构计划

## Summary

将插件状态收敛为清晰的四层事实源：`Device.status` 只表示设备健康，`DeviceCommand.status` 只表示命令生命周期，`WorklineSession.status` 只表示 Session 生命周期，`WorklineSession.plugin_state` 只表示业务进度阶段。Payload 里的 `status/result` 仅作为输入证据，不作为当前状态来源。

用户界面展示“当前进行到哪个设备/动作”，后端从 `session.awaiting_command_id -> command -> device` 投影，不新增可写状态字段。

## Key Changes

- 数据模型：用 Alembic generator 新建迁移，将 `workline_sessions.step_code` 重命名为权威当前态 `plugin_state`；将 `device_commands.step_code` 重命名为不可变快照 `issued_plugin_state`，表示命令生成时的 Session 业务阶段；同步更新 SQLModel 字段、索引和查询过滤；`context_json` 不再存 `plugin_state`。
- Runtime：扩展 `TransitionValidator` 为“校验 + 解析目标状态”，对有状态机的插件执行 `.transition(trigger)` 后由 runtime 调用状态机计算 `dest`，统一写入 `session.plugin_state`；插件禁止通过 `context_patch` 写 `plugin_state`。
- 插件 SDK：保留 `.transition("业务事件")`；把 `@step(expected, target)` 收敛为只做前置保护的 `@requires_state(...)` 或等价单参 `@step(...)`，目标状态只能来自状态机。
- 状态机语义：移除插件状态里的 `COMPLETED/ERROR/MANUAL_HOLD` 这类生命周期状态；完成、失败、人工介入只由 `Session.status` 表达。状态机中这类 trigger 使用 `dest=None`，只校验业务事件合法性，不覆盖最后业务阶段。
- 插件改造：更新 SMT 和 inbound_tote_qc 插件，删除 context model 里的 `plugin_state` 字段；handler 通过 `ctx.plugin_state` 读取当前业务阶段，只在 `.context(...)` 中写业务数据。
- API/UI：Runtime/Trace 响应统一暴露 `plugin_state`，移除 `step_code`；新增或明确 `current_device_*` / `current_action` 投影字段，前端 runtime store 和页面不再聚合 `step_code`，改用 `plugin_state` 和当前设备投影。

## Test Plan

- Backend unit tests:
  - transition 合法时返回目标 `plugin_state`，非法时返回 `PLUGIN_TRANSITION_INVALID`。
  - `context_patch` 含 `plugin_state` 时直接失败，防止插件绕过状态机。
  - failure/complete/manual trigger 使用 `dest=None` 时不改变 `plugin_state`，但正确更新 `Session.status`。
  - Payload `result/status` 只更新命令事实，不被任何查询当成 Session 当前状态。
- Plugin tests:
  - SMT 全链路：scan ok/ng、measurement、pick、conveyor、output 的 `plugin_state` 都由状态机推导。
  - inbound_tote_qc 全链路：weight ok/ng 都进入同一业务阶段，但保留不同 transition 语义和 business decision。
- API/frontend tests:
  - Runtime trace list/detail 返回 `plugin_state`、当前设备和当前动作。
  - 前端 type check 通过，runtime 页面的“主导阶段/失败阶段/当前设备”不再读取 `step_code`。
- Regression checks:
  - `rg "step_code" backend/src backend/tests backend/docs frontend/src` 必须只剩明确白名单项；正常目标是运行时代码、测试、前端源码、生成 API 类型中都没有 `step_code`。
  - `pnpm contract:verify` 必须通过，避免 OpenAPI 生成物和手写 API 类型继续携带旧字段。
- Verification commands:
  - Backend: `cd backend && uv run pytest tests/workline_runtime tests/workline_plugins`
  - Frontend: `cd frontend && pnpm type:check`

## Assumptions

- 项目未发布，不保留 `step_code` API/字段兼容；数据库迁移可以破坏性重命名。
- 新 Alembic 文件必须用 `uv run alembic revision -m "..."` 生成 revision ID，再编辑生成文件。
- `plugin_state` 表示业务进度，不表示完成、失败、人工介入；这些生命周期事实只看 `Session.status`。

---

## GSTACK AUTOPLAN REVIEW REPORT

### Phase 0：Intake

Plan file: `backend/docs/superpowers/plans/2026-05-08-workline-plugin-state-ssot.md`

Review base:

- Backend repo: `workline-sandbox-runtime-flow` against `develop`
- Frontend repo: `workline-sandbox-runtime-flow` against `develop`
- UI scope: yes. The plan changes Runtime/Trace response fields and frontend runtime display semantics.
- DX scope: yes. The plan changes plugin SDK authoring rules, state machine contracts, migrations, generated API types, and plugin docs/templates.
- Design doc: none found for this branch. User chose to skip `/office-hours` and continue standard review.

### Phase 1：CEO Review - Premise Challenge

#### 0A. Premises

| Premise | Evaluation | Risk if wrong |
|---|---|---|
| `Device.status`、`Command.status`、`Session.status`、`plugin_state` are separate domain facts, not duplicates. | Valid. Code already treats device health, command lifecycle, session lifecycle, and plugin progress as different concepts, but current names make them look interchangeable. | If collapsed into one status field, runtime loses the ability to explain whether a line is blocked by equipment, command delivery, business progress, or lifecycle state. |
| `plugin_state` should be an authoritative Session column, not buried inside `context_json`. | Valid and important. Current code reads/writes `context_json["plugin_state"]`, then projects it to `step_code`; that is a control field hiding in plugin-owned data. | If left in context, plugins can keep bypassing runtime rules by writing state in arbitrary patches. |
| `.transition("business_event")` should remain because business event semantics matter independently from destination state. | Valid. `weight_ok` and `weight_ng` can share a destination while carrying different business meaning. | If removed, timeline/debug loses why a transition happened, not just where it landed. |
| `Session.status` alone should own `COMPLETED`、`FAILED`、`MANUAL_HOLD` lifecycle semantics. | Mostly valid. It removes duplicated terminal states, but the plan must define how terminal transitions are recorded without mutating `plugin_state`. | If underspecified, handlers may reintroduce `plugin_state=COMPLETED/ERROR/MANUAL_HOLD` as a convenience fallback. |
| `step_code` can be broken because the system has no historical release. | Valid by project rule, but still requires full API/frontend/generated-type cleanup in both repos. | If only backend storage changes, frontend/runtime traces will still display or filter by stale `step_code`. |
| Runtime can infer current device/action from `awaiting_command_id -> DeviceCommand -> Device`. | Valid for waiting states. The plan needs a fallback for non-waiting phases and terminal cases, where no command is awaited. | If no fallback is defined, UI will show blank current device for active-but-not-waiting decisions and completed traces. |

#### 0B. Existing Code Leverage Map

| Sub-problem | Existing code to reuse |
|---|---|
| Current transition validation | `src/workline_runtime/transition_validator.py`, `src/workline_runtime/orchestrator.py` |
| Builder intent collection | `src/workline_runtime/plugin_base.py::PluginResultBuilder` |
| State projection helper | `src/workline_runtime/plugin_state.py` should be replaced or narrowed to column access/projection helpers |
| Session persistence and wait pointers | `src/celery_app/tasks/workline.py::_apply_orchestrator_effects` and `WorklineSession.awaiting_command_id` |
| Device path projection | `src/app/workline/services/runtime_query_service.py::_build_trace_path` already marks `current_blocking_device_id` |
| Plugin examples | `src/workline_plugins/smt_classifier` and `src/workline_plugins/inbound_tote_qc` cover both complex and minimal flows |
| Frontend runtime aggregation | `frontend/src/stores/workline-runtime.ts`, runtime components currently aggregate `step_code` and can be redirected to `plugin_state` |

#### 0C. Dream State

```text
CURRENT
  Plugin writes transition + context_json.plugin_state + step_code projection.
  API and UI display step_code.
  Lifecycle terminal states leak into plugin state.

THIS PLAN
  Plugin emits transition + business context only.
  Runtime validates transition, derives destination plugin_state, writes Session.plugin_state.
  Session.status owns lifecycle, command/device tables own execution and equipment facts.
  API/UI display plugin_state plus projected current device/action.

12-MONTH IDEAL
  Plugin authors define manifest, typed event/result/command contracts, and a small state machine.
  Runtime provides a visual trace of why state changed, what device is blocking, and what action is next.
  New plugins copy a template without learning callback envelopes, command projection, or state plumbing.
```

#### 0C-bis. Alternatives

| Approach | Effort | Risk | Pros | Cons | Decision |
|---|---:|---|---|---|---|
| Minimal consistency check: keep context `plugin_state`, reject mismatched transition/context target. | S | Medium | Fastest patch; catches some bad writes. | Leaves control state in plugin context and keeps duplicate write paths. | Rejected by DRY/SSOT principle. |
| Runtime-owned `Session.plugin_state` column with transition-derived destination. | M | Medium | Single authoritative business progress field; plugin cannot bypass runtime state. | Requires migration, SDK, tests, API, frontend, docs. | Recommended. |
| Full workflow DSL / visual process engine. | XL | High | Could eventually power visual design and plugin generation. | Too much new platform for the concrete problem; violates KISS/YAGNI. | Deferred. |

#### 0D. Mode-Specific Analysis

Mode: SELECTIVE EXPANSION.

Approved expansions:

- Include frontend runtime display updates, because stale `step_code` rendering is in direct blast radius.
- Include generated OpenAPI/types/zod sync, because API field renames otherwise fail type checks.
- Include plugin docs/templates, because the main user is the next plugin author.

Deferred:

- Visual workflow designer.
- Long-lived topology snapshot cache.
- Full plugin marketplace or hot-load system.

#### 0E. Temporal Interrogation

| Time | What should be true |
|---|---|
| Hour 1 after implementation | Existing SMT/inbound plugin tests fail first on expected `step_code`/context assumptions, then pass after runtime state ownership is moved. |
| Day 1 | Runtime pages display `plugin_state` and current device/action without reading legacy `step_code`. |
| Week 1 | A new plugin author cannot accidentally write `plugin_state` in `.context(...)`; tests catch it. |
| Month 1 | Trace/debug explains lifecycle status, business progress, command state, and device health as separate facts. |
| Six months | Plugin state remains small and business-oriented; terminal and equipment states have not leaked back into plugin context. |

#### 0F. Phase 1 Premise Gate

Premises requiring human confirmation before autoplan continues:

1. Break `step_code` compatibility entirely in storage and API, because WES has no released historical contract.
2. Promote plugin progress to `WorklineSession.plugin_state`, not `context_json.plugin_state`.
3. Treat `plugin_state` as progress only; terminal and manual lifecycle facts stay in `Session.status`.
4. Keep `.transition("business_event")` as the plugin authoring primitive, but make runtime derive the destination state.

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | Intake | Skip `/office-hours` and proceed with standard review after user selected A. | User-confirmed | Bias toward action | The plan was produced from the current architecture discussion and has concrete repo evidence. | Running an extra discovery interview before review. |
| 2 | CEO | Use SELECTIVE EXPANSION mode. | Mechanical | Boil lakes + pragmatic | The state ownership change touches backend runtime, API, frontend runtime display, and plugin docs directly. | Full platform rewrite or narrow backend-only patch. |
| 3 | CEO | Rename command snapshot to `issued_plugin_state`, not `plugin_state`. | Auto-decided | SSOT + name facts by ownership | `WorklineSession.plugin_state` must be the only current business progress field; a command row only records the state at command issue time. | Renaming both Session and Command `step_code` to `plugin_state`. |
| 4 | Eng | Replace two-argument `@step(expected, target)` with `@requires_state(...)`. | Auto-decided | KISS + no legacy compatibility | Target state must come only from the state machine; keeping the second argument preserves the old duplicate destination path. | Keeping `@step(expected, target)` as an alias. |
| 5 | Eng | Make transition resolution an explicit `TransitionDecision` consumed by effect application. | Auto-decided | SSOT + avoid recomputation | The destination state must be calculated once by runtime and carried forward, not recalculated from context or command payloads. | Returning only `(bool, error)` from `TransitionValidator` and deriving state later. |
| 6 | Eng | Define `issued_plugin_state` as the post-transition Session business phase at command creation time. | Auto-decided | Name temporal facts precisely | A command created by `scan_ok -> WAITING_MEASUREMENT` belongs to `WAITING_MEASUREMENT`, not the prior `IDLE` phase. | Source-state snapshot or ambiguous command snapshot semantics. |

### Phase 2：CEO Review - Product / Scope Lock

#### Findings

| Severity | Finding | Plan Change |
|---|---|---|
| High | The plan solves the right problem: the current architecture writes the same business progress through `PluginResult.transition`, plugin `context_patch["plugin_state"]`, `WorklineSession.step_code`, and frontend `step_code` displays. | Keep the rewrite broad enough to include runtime, plugins, API, frontend, templates, and docs. |
| High | `DeviceCommand.step_code -> plugin_state` would create another apparent current-state owner. In code, `src/app/device/models/command.py` describes it as “命令生成时的步骤语义编码”; that is a snapshot, not live state. | Use `DeviceCommand.issued_plugin_state` and API `TraceCommandItem.issued_plugin_state`. |
| Medium | Current device/action projection is valid only when `WorklineSession.awaiting_command_id` exists. Active decision phases, just-completed sessions, and failure/manual states need deterministic fallback labels. | Add read-only projection rules for `current_device_*`, `current_action`, and `current_blocking_reason`. |
| Medium | Existing `docs/business/workline_plugin_refactor_next_phase_plan.md` already identified PR0 state-field breakage but still treated `context_json.plugin_state` as the source. | Mark this plan as superseding the PR0 state-source portion; do not keep two competing plugin-state plans. |

#### Scope Decision

This plan is approved as a selective architecture expansion, not a platform rewrite.

In scope:

- Backend runtime ownership of business progress.
- Alembic migration and SQLModel/API field renames.
- SMT and inbound_tote_qc plugin rewrites.
- Frontend runtime/trace display and generated type sync.
- Plugin docs/templates/tests.

Out of scope:

- Visual workflow designer.
- New workflow DSL beyond the existing `transitions` state machine.
- Device topology cache redesign.
- Legacy `step_code` compatibility adapters.

### Phase 3：Design Review - Runtime UI Semantics

This is not a visual redesign, but the field rename changes what operators see and what frontend code aggregates.

#### UI Vocabulary

| Current | New | Reason |
|---|---|---|
| Step / 当前 Step | 业务阶段 / `plugin_state` | Step implies UI sequence or command step; plugin_state is business progress. |
| 高频失败 Step | 高频失败业务阶段 | Failed sessions should aggregate business phase, not command status or device health. |
| 主要等待 Step | 主要等待业务阶段 | Waiting is lifecycle; the phase is the business context of that wait. |
| 当前设备 | 当前等待设备 / 最近执行设备 | Avoid implying every business phase always has a blocking device. |

#### Projection Rules

The backend should expose these as response fields only; none of them are writable facts:

| Field | Source of Truth | Rule |
|---|---|---|
| `plugin_state` | `WorklineSession.plugin_state` | Current business progress. |
| `current_device_id/code/name` | `awaiting_command_id -> DeviceCommand.device_id -> Device` | Primary when session is waiting for a command result. |
| `current_action` | awaiting command `task_type` / `command_code` | Human-readable action for the waiting command. |
| `current_action_source` | projection enum | `AWAITING_COMMAND`, `LATEST_COMMAND`, `TIMELINE`, or `NONE`; lets the UI label “当前等待” vs “最近执行” correctly. |
| `current_blocking_reason` | wait fields + command status + failure fields | Explain why progress is blocked. |
| `last_device_id/code/name` | latest command/timeline in trace | Fallback when no command is currently awaited. |

Projection priority:

1. Waiting lifecycle with `awaiting_command_id`: `current_action_source=AWAITING_COMMAND`, device/action from that command.
2. Active session without awaited command: `current_action_source=LATEST_COMMAND` if a command exists, otherwise `TIMELINE`.
3. Completed/failed/manual/cancelled session: no “current waiting device”; expose latest device/action as historical fallback.
4. New session before first transition: `plugin_state` may be empty and `current_action_source=NONE`.

Frontend empty-state copy should distinguish:

- `无等待设备`: active session exists but no awaited command is pending.
- `最近设备`: terminal or non-waiting session, shown from the latest command/timeline.
- `未进入业务阶段`: `plugin_state` is null/empty during creation before the first transition.

#### Affected Frontend Areas

- `src/types/runtime.ts`: replace `step_code` with `plugin_state` for Session/List/Summary; use `issued_plugin_state` for command snapshots.
- `src/stores/workline-runtime.ts`: rename `mostFailedStep` / `dominantActiveStep` to business-stage semantics.
- Runtime/trace components currently rendering `step_code`: `SandboxCycleStatus.vue`, `TraceExplorerPage.vue`, `WorklineTaskQueue.vue`, `TraceHealthPipeline.vue`, `SandboxActionList.vue`, `DecisionStrip.vue`, `TraceCaseHero.vue`, `TraceRelatedSidebar.vue`, `SessionBoard.vue`.
- Generated OpenAPI metadata/zod files must be regenerated or intentionally updated after backend schema changes.

### Phase 4：Engineering Review - Execution Plan

#### Critical Implementation Rules

1. `WorklineSession.plugin_state` is the only current business progress source.
2. `DeviceCommand.issued_plugin_state` is a historical snapshot and must never be used to answer “session 当前在哪个业务阶段”.
3. `context_json` is plugin-owned data. Runtime-owned keys such as `plugin_state` are forbidden in `PluginResult.context_patch`.
4. `.transition(trigger)` is business intent. Runtime validates it and resolves the destination from the plugin state machine.
5. `Session.status` owns lifecycle states such as completed, failed, waiting, manual hold, and cancellation.
6. Payload `status/result` is raw device evidence; it can update command result/status and feed plugin decisions, but it is not a session-state source.

#### Backend Work Packages

| Package | Files / Modules | Required Changes |
|---|---|---|
| Data model | `src/app/workline/models/session.py`, `src/app/device/models/command.py` | Rename `WorklineSession.step_code -> plugin_state`; rename `DeviceCommand.step_code -> issued_plugin_state`; update descriptions to state source vs snapshot. |
| Migration | `migrations/versions/*` via `uv run alembic revision -m "rename workline plugin state"` | Generated revision only. Rename indexes; backfill from existing `step_code`; remove `plugin_state` from `context_json` for existing rows if the DB supports JSON mutation, otherwise document destructive reset. |
| Runtime state helper | `src/workline_runtime/plugin_state.py` | Replace context read/write helpers with session-column read helpers and reserved-key checks. Delete `set_plugin_state` for plugin context patches. |
| Plugin context | `src/workline_runtime/plugin_context.py` | Add `ctx.plugin_state` sourced from `session.plugin_state`, defaulting to plugin initial state only at runtime boundary. |
| Transition resolution | `src/workline_runtime/transition_validator.py` | Return a structured `TransitionDecision(valid, error, from_plugin_state, to_plugin_state)`. Execute trigger on a temporary model to resolve `dest`; support no-op `dest=None`. |
| Orchestrator | `src/workline_runtime/orchestrator.py` | Use `session.plugin_state` for current state; reject `context_patch["plugin_state"]`; emit one `TransitionDecision` / `next_plugin_state` in `OrchestratorResult`. |
| Effect application | `src/celery_app/tasks/workline.py` | Consume the orchestrator's decision without recomputing state; apply destination to `session.plugin_state` before command creation; write post-transition `issued_plugin_state` snapshots to commands/outbox trace payloads; terminal effects update only `Session.status`. |
| Trace/query API | `src/app/workline/models/runtime.py`, `runtime_query_service.py`, `trace_response_builder.py`, repositories | Rename query field to `plugin_state`; expose current-device/action projections; ensure list queries bulk-load related commands/devices rather than per-row DB gets. |
| Plugins | `src/workline_plugins/smt_classifier`, `src/workline_plugins/inbound_tote_qc` | Remove `plugin_state` from context models; update state machines to remove `COMPLETED/ERROR/MANUAL_HOLD` from plugin states; use `@requires_state(...)`; read `ctx.plugin_state`. |
| Docs/templates | `docs/plugin_development_guide.md`, `docs/templates/workline_plugin/*`, `docs/plugin_validation_quickstart.md` | Rewrite examples so new plugins cannot copy old context-state writes. |

#### State Machine Contract

The state machine must expose enough information for runtime to resolve destination deterministically. A valid implementation path is:

```python
decision = validator.resolve(
    current_state=session.plugin_state or plugin.initial_state,
    transition=result.transition,
    state_machine_class=workline.state_machine_class,
)
```

Resolution semantics:

- no state machine: accept transition and keep `plugin_state` unchanged unless this is initial creation;
- missing transition: no business progress change;
- valid transition with `dest="WAITING_MEASUREMENT"`: set `session.plugin_state`;
- valid lifecycle transition with `dest=None`: keep `session.plugin_state`, update only lifecycle effect;
- invalid transition: return `PLUGIN_TRANSITION_INVALID` and do not apply context, commands, waits, or lifecycle effects.

The destination is calculated exactly once. Effect application must read `OrchestratorResult.transition_decision.to_plugin_state`; it must not inspect `context_json`, command payloads, or plugin context models to infer progress.

`issued_plugin_state` semantics:

- It is the Session `plugin_state` after the transition decision has been applied and immediately before the command row is persisted.
- It is immutable historical evidence for “this command was issued while the Session business phase was X”.
- It is never a fallback source for the current Session business phase.

#### Migration Detail

Use Alembic's generator. Do not hand-write revision IDs.

Minimum migration behavior:

1. Rename `wes_biz.workline_sessions.step_code` to `plugin_state`.
2. Rename `ix_wes_biz_workline_sessions_step_code` to `ix_wes_biz_workline_sessions_plugin_state`.
3. Rename `wes_biz.device_commands.step_code` to `issued_plugin_state`.
4. Rename `ix_wes_biz_device_commands_step_code` to `ix_wes_biz_device_commands_issued_plugin_state`.
5. If existing rows still have `context_json.plugin_state`, migrate it into `workline_sessions.plugin_state` only when the column is null.
6. Remove or ignore `context_json.plugin_state` after migration because context is no longer authoritative.
7. `device_event_logs` is not an active model after `20260330_1030_b9e1c2d3f4a5_drop_device_event_logs.py`; do not revive it for this rename.

#### Backend Tests

| Area | Tests |
|---|---|
| Transition resolver | valid transition returns destination; invalid transition rejects; `dest=None` keeps current state. |
| Orchestrator guard | `context_patch` containing `plugin_state` fails before any side effect. |
| Decorator guard | `@step(expected, target)` is removed or rejected; `@requires_state(...)` only checks source state and never writes target state. |
| Effect ordering | session `plugin_state` updates before command creation; command receives `issued_plugin_state` snapshot. |
| Lifecycle split | complete/fail/manual-hold changes `Session.status` and leaves business `plugin_state` at the last meaningful phase. |
| API query | `TraceQueryRequest.plugin_state` filters session column; no `step_code` fields remain in runtime API models. |
| Current device projection | waiting command projects current device/action; non-waiting and terminal sessions use latest-device fallback. |
| Migration | alembic upgrade succeeds and indexes/columns match SQLModel metadata. |
| SSOT grep | `rg "step_code" backend/src backend/tests backend/docs frontend/src` returns only approved historical migration/doc exceptions, ideally none in runtime code and generated API artifacts. |

### Phase 5：DX Review - Plugin Author Experience

#### Target Authoring Model

A plugin author should learn one rule:

```text
Read ctx.plugin_state.
Emit builder.transition("business_event").
Never write plugin_state into context.
```

Recommended handler shape:

```python
@on_event("SCAN_COMPLETED")
@requires_state(SmtClassifierState.IDLE)
async def handle_scan(self, ctx: PluginContext, event: ScanPayload) -> PluginResult:
    return (
        PluginResultBuilder(ctx)
        .transition("scan_ok")
        .context({"barcode": event.barcode})
        .command(...)
        .build()
    )
```

#### DX Requirements

| Requirement | Acceptance |
|---|---|
| Runtime-owned-state error | If `.context({"plugin_state": ...})` is used, error says: `plugin_state is runtime-owned; use .transition(...) and the plugin state machine`. |
| Template safety | `docs/templates/workline_plugin/context.py.tmpl` contains no `plugin_state` field. |
| State-machine examples | Docs show same-destination transitions such as `weight_ok` and `weight_ng`, and no-op lifecycle transitions with `dest=None`. |
| Debuggability | Trace timeline records `transition`, `from_plugin_state`, `to_plugin_state`, and lifecycle `from_status/to_status` separately. |
| New plugin checklist | Checklist includes “no `plugin_state` in context model/patch”, “all transitions in state machine”, “terminal states stay in `Session.status`”. |

#### Documentation Updates

- `docs/plugin_development_guide.md`: rewrite state section around `ctx.plugin_state` and `.transition(...)`.
- `docs/templates/workline_plugin/plugin.py.tmpl`: remove `plugin_state=...` patches and replace `@step(expected, target)`.
- `docs/templates/workline_plugin/tests.py.tmpl`: assert emitted transition and runtime-resolved state, not context patch state.
- `docs/plugin_validation_quickstart.md`: update SQL snippets from `context_json->>'step_code'` / `step_code` to `workline_sessions.plugin_state` and command `issued_plugin_state`.
- `docs/business/workline_plugin_refactor_next_phase_plan.md`: add note that this plan supersedes the old PR0 state-source assumption.

### Final Execution Checklist

1. Generate backend migration with Alembic revision generator.
2. Rename backend model fields and runtime API models.
3. Implement transition resolution and reserved context key rejection.
4. Move effect application to write `session.plugin_state` and command `issued_plugin_state`.
5. Refactor SMT and inbound_tote_qc plugins/state machines/tests.
6. Update runtime query/trace builders and current-device/action projections.
7. Regenerate or update frontend OpenAPI/types/zod artifacts.
8. Rename frontend `step_code` usages to `plugin_state` or `issued_plugin_state` according to ownership.
9. Update plugin docs/templates/validation quickstart.
10. Run `pnpm contract:verify` and frontend type/lint checks.
11. Run backend runtime/plugin tests plus migration/schema checks.
12. Run the `rg "step_code"` cleanup gate and document any intentional historical exceptions.

### Final Gate

Recommended path: proceed with implementation after confirming the refined command snapshot naming:

- Current session business progress: `WorklineSession.plugin_state`.
- Command historical snapshot: `DeviceCommand.issued_plugin_state`.
- API command snapshot field: `issued_plugin_state`.
- No surviving `step_code` compatibility field.
