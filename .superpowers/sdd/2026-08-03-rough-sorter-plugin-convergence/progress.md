# Phase 8 SDD ledger

- Branch: `codex/phase8-rough-sorter`
- Active worktree: `/Users/kaizhou/codeDev/wes_backend-worktrees/codex-phase8-rough-sorter`
- Main workspace is externally occupied at `develop@23ec7559`; do not touch its dirty `TODOS.md` or FAST-budget files.
- Push is not authorized. User requires every Task to be committed separately.
- External staged dirty shield remains stash object `aebef25a90cabedc14cc9ff51c0bf230cda73712`; restore staged only after all Task 8 work is complete.

## Task 8A complete

- Range: `85ee87dc..12d8cbb4` (separate implementation and review-fix commits; no Task 8B code).
- Behavior: persistent single-execution `DeferExecution`, claim without attempt increment, real-failure-only attempt count, fair ACTIVE-Epoch claim, real prefork child startup fence, declared/actual queue fail-closed validation, fulfillment exclusion.
- Final focused evidence: selector contracts `266 passed`; commit hook QUALITY passed on each code snapshot.
- Final HEAVY evidence: `./scripts/run_selected_heavy_local.sh --base 85ee87dc` -> `96 passed in 101.96s` after one isolated diagnostic of an unrelated QUIT teardown timing failure.
- Review: original 2 Critical + 1 Important closed; fresh review found one HEAVY over-selection; round2 confirmed closure with 0 Critical / 0 Important.

## Task 8B implementation complete

- BASE: `12d8cbb4`.
- Scope: Transport result evidence and causal fence; single-execution `recovery_decided` direct replacement; WMS business-WAIT follow-up with a new operation identity and independent fulfillment dispatcher.
- Excluded: post-commit wake and static rough-sorter deployment composition remain Task 8C.
- Migration revision: `5695afa99545`（direct cutover；未在实施 worktree 运行真实 PostgreSQL migration）。
- Focused evidence: core/WMS/API/deployment `398 passed`；rough_sorter private `68 passed`；selector contract `267 passed`；staged selector
  精确选择 10 个 HEAVY 文件，`76 collected`；范围 Ruff、basedpyright（`0 errors, 0 warnings`）、Alembic heads 和 diff-check 通过。
- Main-Agent pending gates: 干净 PostgreSQL base→head/metadata、selector 选中的真实 HEAVY、独立最终 Review。Task 8C 负责插件 factory、
  dispatcher runtime/static deployment composition 与 post-commit wake。
