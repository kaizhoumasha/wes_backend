# Transport 料箱回架逐箱 Evidence 修复实施计划

Date: 2026-09-04
Backend branch: `codex/fix-transport-bin-return-evidence`
Frontend branch: `codex/fix-transport-bin-return-evidence`
Risk: LARGE / HIGH-RISK

## Goal

修复目标为 `RACK_BIN_SLOT` 的料箱回架任务被聚合 `transport.task.resulted@v1`
提前判定成功的问题。每个成功回架的料箱必须先有已应用、且最终位置与冻结目标完全一致的
`transport.task.member_position_changed@v1` / `TARGET_PLACED` Evidence；否则 WES 不接受聚合结果，
不结束 TransportTask、不释放资源绑定，也不允许自动联调推进到下一货架面或 `CTU03`。

## Global Constraints

- 复用现有两个 Transport callback operation；不新增 v2、兼容层、动态注册表或伪造 Evidence。
- 只约束聚合结果中 `status=SUCCEEDED` 且冻结目标 `kind=RACK_BIN_SLOT` 的 BIN 成员。
- 提前到达的聚合结果返回 HTTP `409`、`code=CONFLICT`、
  `reason_code=MEMBER_POSITION_EVIDENCE_PENDING`。
- 上述临时 409 不持久化 callback receipt，也不创建 TransportEvidence；WMS 使用完全相同的
  `operation_id`、`timestamp`、`outcome_revision` 和消息体重试。
- 身份冲突、revision 冲突、错误目标、`POSITION_UNKNOWN` 继续使用现有确定性冲突或对账规则。
- 不缓存、不轮询提前到达的聚合结果，不新增数据库状态、列或迁移。
- 自动联调复用步骤级 `observed_bin_ids`：`WAIT_SCAN12` 表示已扫码，`BINS_TO_RACK`
  表示已确认回架。
- 所有选中料箱回架并且聚合结果成功后，才允许下一货架面或 `CTU03`。
- 保留后端已有无关修改，不格式化或提交无关文件。
- 本轮不 commit、不 push、不 merge、不 deploy；这些动作需要单独授权。

## Task 1 — Backend callback contract and core gate

1. RED: extend existing callback/outcome tests to prove an early successful rack-slot result returns
   `409 CONFLICT + MEMBER_POSITION_EVIDENCE_PENDING`, creates neither receipt nor evidence, leaves the
   task non-terminal, and retains resource bindings.
2. RED: prove the identical aggregate envelope is accepted after all required exact-target
   `TARGET_PLACED` callbacks are applied; duplicate and permanent conflict semantics remain unchanged.
3. GREEN: add the smallest synchronous precondition in the Transport service/repository ownership path.
4. Update Transport callback OpenAPI response schema for the new retryable reason code.
5. Run focused backend callback, outcome, OpenAPI, and receipt tests plus touched-file Ruff checks.

## Task 2 — Debug run progress and CTU03 gate

1. RED: extend automatic-debug advancement tests for partial multi-bin return progress, wrong target,
   position unknown, aggregate-before-member rejection, and no next step/CTU03 before full closure.
2. RED: update the real-loop integration test so `BINS_TO_RACK` sends per-bin `TARGET_PLACED`
   callbacks before the aggregate result and proves the early-result regression.
3. GREEN: derive confirmed rack-return bins from existing Transport member facts and write them into the
   step's existing `observed_bins_json`; do not add response fields or database columns.
4. Run focused runtime and integration tests plus touched-file Ruff checks.

## Task 3 — Frontend progress presentation

1. RED: extend `TransportDebugRunDialog` tests to require `BINS_TO_RACK` to display confirmed and pending
   bins while preserving the existing `WAIT_SCAN12` wording.
2. GREEN: add the minimal phase-specific line using existing `observed_bin_ids` and `stepPendingBins()`.
3. Run the focused Vitest file, type check, and touched frontend lint/format checks as available.

## Task 4 — Formal documentation and contract synchronization

1. Replace the stale claim that current CTU/RCS does not emit per-container position events.
2. Document the mandatory rack-return ordering, retryable 409 semantics, and exact-identity retry rule.
3. Run backend documentation/contract checks applicable to the touched files.
4. Freeze the canonical backend contract into the frontend only from a clean eligible backend checkout;
   regenerate required types/metadata and run contract tests/verification. Do not manufacture a dirty
   develop snapshot merely to satisfy this step.

## Verification

Primary QA artifact:
`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_frontend/kaizhou-develop-eng-review-test-plan-20260904-175540.md`

Focused success requires:

- Core callback RED was observed before production changes.
- All focused backend callback/outcome/OpenAPI/receipt tests pass.
- Automatic-debug runtime and real-loop integration tests pass.
- Frontend component test and type check pass.
- Contract/document checks pass or are explicitly deferred at the clean-develop freeze boundary.
- A read-only final review finds no unresolved Critical or Important issue.
