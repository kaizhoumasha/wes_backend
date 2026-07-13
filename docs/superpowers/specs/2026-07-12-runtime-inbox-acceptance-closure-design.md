# RuntimeInbox 验收闭环修复设计

## 背景与目标

`docs/superpowers/plans/2026-07-10-runtime-inbox-single-source-of-truth.md` 的主运行链路已经基本收敛，但逐项验收仍发现数据库约束、模块归属、重放语义、运维脚本、当前文档和真实 PostgreSQL 门禁未完全闭合。

本轮目标是消除这些验收阻断项，使原计划 Task 1–9 的完成状态能够由当前代码、迁移、运行文档和可重复测试证据共同证明，而不是依赖历史执行记录。

## 范围

本轮包含：

- 补齐 RuntimeInbox 数据库约束和 pre-cutover audit-only 迁移语义。
- 将 `RuntimeInboxService` 迁入锁定的 service 模块并删除 consumers 兼容入口。
- 固化人工重放的 `REPLAY_REQUEST` 合同。
- 修复运行数据 reset 脚本并扩大旧入口零引用门禁。
- 更新仍被视为当前事实源的业务、架构和运行文档。
- 让 PostgreSQL migration、integration、resilience 和 benchmark 验收可以安全、重复地执行。
- 重新验收并同步原实施计划状态。

本轮不建设运营 UI、完整告警平台或现场 Runbook，不改变 RuntimeInbox 五态状态机，也不恢复任何 WorklineInbox 兼容表面。

## 架构决策

### 1. 数据库合同

`RuntimeInbox.kind` 与 `RuntimeInbox.status` 使用命名 CHECK constraint，合法集合分别与六类 ingress 和五态状态机一致。新写入保持 canonical envelope 必填语义。

Revision A 负责识别切换前缺少 canonical payload 的旧行，将其转换为明确的 audit-only 终态证据：`status=DEAD_LETTER`、`last_error_code=PRE_CUTOVER_AUDIT_ONLY`，并补齐明确说明与终态时间。此类记录不计入可行动 dead-letter 指标，不得进入 claim、retry 或人工 replay。

除上述 audit-only 例外外，命名的 conditional envelope CHECK 强制行动型记录具备 canonical identity、content、claim 和 time 必填字段。Repository 的 claim 条件仍加入同等的防御性 envelope 条件，避免异常或手工数据绕过迁移约束后进入执行链路。

迁移必须兼容 fresh database、从 Revision A 父版本升级、Revision A/B 回环，以及包含真实毫秒值和 pre-cutover 行的数据集。

### 2. Service 模块归属

`RuntimeInboxService`、领域异常、接受/重放结果类型和单例统一迁入 `src/app/runtime/orchestration/services/runtime_inbox/`。该目录的 `__init__.py` 是正式导出边界。

`consumers/` 只保留协议 adapter，例如 callback writer。原 consumers service 文件物理删除，不提供 import shim。所有生产代码和测试一次性切换到新入口。

Service 继续遵守 Service → Repository → Database，不在迁移过程中引入直接 SQL 查询。

导入方向锁定为“包内具体模块、包外正式导出”：同包的 bridge、writeback 等模块直接依赖具体 service module；包外生产调用方从 `services.runtime_inbox` 导入。基础 RuntimeInboxService 不反向依赖 Processor，避免 `__init__.py → bridge → __init__.py` 循环初始化。

### 3. 人工重放合同

人工重放创建新的 `REPLAY_REQUEST` RuntimeInbox，而不是复制原 `kind`。原始业务类型、source inbox、原 source identity、actor 和 reason 作为 canonical payload 与审计证据保存。

Replay API 强制接收长度受控的 `request_id`，source identity 由 source inbox 与 request ID 规范化生成；重复同一 request ID 且同 hash 返回既有 ACK，同 ID 不同内容走现有冲突合同。actor 只取认证上下文，客户端不再提交 `operator_id`。原 DEAD_LETTER 记录保持终态，不被改写。

Replay 使用单层扁平 envelope，固定保存原始业务 kind/payload、直接 source inbox、根 source inbox、request ID、actor 和 reason。再次重放失败的 REPLAY_REQUEST 时复用根业务语义，只更新直接来源和审计信息，禁止递归嵌套 payload。Processor 对 `REPLAY_REQUEST` 只解包一层并路由到合法原业务语义，仍受 claim、FIFO、token fencing 和 effect 幂等约束。

WorklineOperationService 只负责工作线锁和 reconciliation 安全前置；RuntimeInboxService 是重放状态验证、消息构造、幂等、审计和 typed domain error 的唯一所有者。至少区分 not found 与 replay not allowed，并为 audit-only、非 DEAD_LETTER 和非法 replay envelope 提供稳定 reason code，API 不解析错误文案。

### 4. 运维与零引用边界

运行数据 reset 脚本的表清单改为显式 schema-qualified identity，不再假设所有运行表都位于 `wes_biz`。删除 `wes_biz.workline_inbox`，增加 `wes_runtime.runtime_inbox`，并保持 dry-run、主数据保护和显式 `--yes` 安全边界。

默认启用 Mock WMS reset 时采用 fail closed：Mock reset 失败必须在任何数据库 mutation 前中止并返回非零；只有操作者显式指定 `--no-reset-mocks` 才允许跳过外部状态重置。

WorklineInbox 退役 guardrail 使用一个显式多文件类型策略：active source/tests 扫描 Python，scripts 扫描 Python/Shell，current docs 使用明确文件清单。历史迁移、归档文档、负向测试和 downgrade DDL 使用精确到文件的窄化 allowlist，不允许目录级豁免；scanner 自测必须证明 `.py/.sh/.md`、current/archive 和邻近文件边界。

### 5. 当前文档口径

以下类型文档必须描述 RuntimeInbox 当前链路：业务 SSOT、runtime workflow guide、当前 E2E 操作指南、file index、runtime ownership、observability 和当前 ADR。

仍有参考价值但描述旧架构的文档移动到归档区或在标题和开头明确标注历史状态。不能让标记为“当前实现”“SSOT”或“唯一权威合同”的文档继续使用 WorklineInbox、旧 task 或旧表名。

`TODOS.md` 仅保留真实未完成且仍在范围内的后续工作；已完成条目删除或移动到历史记录。本轮不强制删除与其他领域相关的有效 TODO。

### 6. PostgreSQL 与性能门禁

Heavy tests 继续要求显式 `INTEGRATION_DATABASE_URL`，只允许创建安全前缀的随机临时数据库，并 patch 真实任务队列 gateway。统一 preflight 在创建临时库前区分配置缺失、不安全目标、admin 连接失败和数据库容量不足；scenario 或 cleanup 失败使用稳定诊断，并保证异常路径不遗留临时数据库。

Benchmark 固化以下硬门禁：

- 1000 条混合 backlog、4 worker。
- claim p95 不高于 150ms。
- 吞吐不低于 1000 条/秒。
- duplicate claim 为 0。
- 隔离临时库内 `max_waiting_locks=0` 且 `waiting_lock_samples=0`。
- query plan 直接 EXPLAIN Repository 生成的生产 claim statement，不维护手写 SQL 副本。
- 1000 条 workload 负责吞吐；独立的大表高选择性 fixture 在 `ANALYZE` 后要求命中目标 partial/composite index，并仅在该 fixture 中拒绝 RuntimeInbox Seq Scan。

调试 benchmark 可以不生成正式证据。正式 evidence 模式必须在 clean worktree 上运行并强制输出 artifact，记录 schema version、UTC 时间、完整 commit SHA、dirty=false、PostgreSQL version/关键 settings、样本、指标、阈值、query plan 和 gate verdict；验收拒绝 SHA 不匹配、字段缺失或仓库 fixture 冒充实测。

本分支同时建设严格 PostgreSQL 验收 CI：使用具备临时库创建权限和足够连接容量的隔离 runner，执行 migration、processing integration、两个 crash window、benchmark 和 evidence 校验，并归档 commit-bound artifact。CI 不连接共享开发数据库。

## 错误处理与安全边界

- 迁移遇到不合法 kind/status 或无法安全分类的旧行时必须显式失败，不静默丢弃。
- audit-only 行的 replay 请求返回稳定领域错误，不产生新消息。
- reset 脚本在目标表不存在、schema 不匹配或包含主数据表时拒绝 apply。
- Mock WMS reset 失败且未显式跳过时，reset 脚本不得执行任何 DB mutation。
- Heavy runner 缺少显式数据库 URL、连接容量不足或目标不安全时在创建临时库前失败。
- 文档和零引用门禁失败直接阻止最终验收，不以代码测试通过代替。

## 测试与验收策略

实施按依赖顺序进行：数据库合同 → Service 迁移 → replay → 运维门禁 → 文档 → heavy gate → 全量验收。

每个任务使用失败测试锁定缺口，再做最小实现，并运行受影响领域回归。数据库迁移拆分为 fresh、parent→A audit-only、合法/非法约束、毫秒保值和 A/B 回环场景；重放按 API、Service、Processor 和真实 PostgreSQL effect-once 分层覆盖。模块迁移必须包含 import/零引用 guardrail；reset 脚本同时覆盖纯逻辑矩阵和临时 PostgreSQL apply。

最终验收至少包括：

- 默认快速测试、测试拓扑、Ruff、Bandit 和项目质量门禁。
- PostgreSQL migration round-trip。
- RuntimeInbox 完整处理链路和两个 crash window。
- 真实 benchmark 与 evidence artifact 校验。
- 隔离 PostgreSQL CI 运行与 artifact 归档。
- current docs 和 active code/scripts 的旧入口零引用检查。

只有上述门禁全部取得当前 commit 的新证据后，才能把原计划对应 Task 恢复为 100%。

## 实施边界与提交策略

本轮在 `feature/runtime-inbox-single-source-of-truth` 分支和当前主目录继续，不新建 worktree。每个任务独立提交，修改符号前运行 GitNexus upstream impact，提交前运行 GitNexus detect changes，并显式 stage 当前任务文件。

## What already exists

- RuntimeInbox 五态模型、唯一 Repository、claim/fencing、三阶段 Processor 和 Celery 任务已经存在，本轮扩展现有链路，不建设第二套队列。
- Revision A/B、临时 PostgreSQL database harness、processing integration 与两个 crash-window resilience 测试已经存在，本轮拆分并补齐缺失场景。
- 现有 benchmark 已锁定 1000 backlog、4 worker、p95、吞吐和 duplicate claim，本轮复用 workload 并补齐生产 statement、锁和计划门禁。
- WorklineInbox 退役 guardrail 已覆盖 Python source/tests，本轮扩展同一策略到 scripts/current docs，不并行新增多套 token 清单。
- reset 脚本已有 dry-run、`--yes`、主数据白名单和 Mock WMS reset，本轮修正 schema identity 与 fail-closed 行为。
- `TODOS.md` 已有“统一运营看板、告警与 Runbook”，覆盖本设计明确延期的运营能力，不重复建项。

## 数据流与依赖图

```text
Replay HTTP
  │ request_id + reason + authenticated user
  ▼
WorklineOperationService
  │ workline lock + reconciliation safety only
  ▼
RuntimeInboxService ──→ RuntimeInboxRepository ──→ PostgreSQL
  │ typed ACK/conflict/rejection       │
  │                                    ▼
  └──────── audit evidence       RECEIVED/FAILED claim
                                           │
                                           ▼
                             RuntimeInboxProcessorService
                             validate → unwrap replay → orchestrate → writeback
                                           │
                                           ▼
                                fenced terminal + effect once
```

```text
strict acceptance runner / CI
  ├── preflight URL + safety + capacity
  ├── temporary DB (safe prefix)
  ├── migration matrix
  ├── processing integration
  ├── crash window A + B
  ├── throughput + selective query-plan benchmark
  ├── commit-bound evidence validation
  └── forced cleanup on success or failure
```

实现时应在以下位置保留简短 ASCII 注释：Revision A 的 audit-only 分类决策、Repository claim statement 构造与 FIFO anti-join、RuntimeInboxService 的扁平 replay envelope、heavy harness 的 preflight/create/yield/drop 生命周期。

## 测试覆盖目标

```text
Migration: parent legacy ─→ audit-only ─→ named checks ─→ A/B roundtrip
Replay:    API ─→ safety ─→ idempotent accept ─→ unwrap ─→ fenced effect once
Reset:     mock reset ─→ validated targets ─→ dry/apply ─→ master data survives
Heavy:     preflight ─→ temp DB ─→ integration/resilience/benchmark ─→ evidence
Guardrail: .py + .sh + current .md ─→ exact allowlist ─→ zero active references
```

目标为所有新增和修改分支 100% 行为覆盖；尤其以下回归测试为阻断项：parent payload-less 行转换为 audit-only、API 不得重放 PROCESSED、Mock WMS 失败时零 DB mutation、生产 claim statement 的高选择性索引计划。

## Failure modes

| Codepath | 生产失败方式 | 测试 | 错误处理 / 可见性 |
|---|---|---|---|
| parent→A migration | 旧行无法安全分类 | PostgreSQL migration failure case | 迁移失败并指出行/原因，不静默继续 |
| canonical write | kind/status/envelope 非法 | named CHECK negative cases | 写入立即失败，不产生 ACK 黑洞 |
| audit-only claim | 历史空 payload 被 worker 取走 | repository + migration test | claim 双重排除，指标按非行动型记录展示 |
| Service import | package 循环初始化 | import/collection guardrail | 测试收集即失败，禁止 lazy fallback |
| Replay API retry | 网络重试或双击 | same request ID + concurrent test | 同 hash 返回既有 ACK |
| Replay conflict | 同 request ID 不同内容 | conflict test | typed conflict 与安全审计 |
| Replay actor | 客户端伪造 operator | API auth test | actor 只取认证上下文 |
| Replay source | PROCESSED/audit-only 被重放 | critical regression tests | typed replay-not-allowed reason |
| Replay chain | payload 递归膨胀 | replay-of-replay test | 单层扁平化并校验 root/immediate source |
| Replay processing | effect 后 worker 崩溃 | PostgreSQL crash/fencing test | 事务回滚、租约恢复、effect once |
| Reset | Mock WMS 不可用 | zero-mutation test | fail closed，非零退出 |
| Reset target | 缺表/schema mismatch/主数据误入 | unit + PostgreSQL smoke | apply 前拒绝并列出目标 |
| Heavy preflight | URL 缺失、容量不足 | harness unit matrix | 创建临时库前分类失败 |
| Heavy cleanup | scenario/drop 异常 | cleanup smoke | 强制清理并保留主错误与 cleanup 诊断 |
| Benchmark | 小表计划误判 | throughput/selective split tests | 只在高选择性 fixture 执行索引门禁 |
| Evidence | 旧 artifact 或 dirty tree | provenance gate tests | 正式验收拒绝并提示 SHA/字段差异 |
| CI | runner 连接共享库或容量不足 | CI config guardrail | job 在 preflight 失败，不执行 destructive step |
| Docs/guardrail | `.sh`/`.md` 漏扫 | scanner self-test matrix | 质量门禁列出精确 offender |

所有高影响失败均有测试与显式错误；不得留下“无测试、无处理且静默”的路径。

## NOT in scope

- RuntimeInbox 运营 UI：由现有“统一运营看板、告警与 Runbook”TODO 承接，本轮只保证后端合同与证据。
- 完整告警平台和现场 Runbook：等待真实试运行指标，不阻塞本轮单一事实源验收。
- RuntimeInbox 五态之外的新状态：audit-only 使用稳定错误码表达，不扩展状态机。
- WorklineInbox compatibility shim、旧表或旧 task：项目未发布，明确不恢复兼容表面。
- 与 RuntimeInbox 验收无关的 Runtime/WorkLine 大规模重构：另行设计，不借本轮扩张。

## TODOS.md 决策

- 运营 UI、告警与 Runbook 已由现有 P2 条目覆盖，不重复新增。
- 严格 PostgreSQL 验收 CI 经工程评审决定在当前分支直接实施，不写入 TODO。
- 本轮同步清理 `TODOS.md` 中已完成但仍位于 active 区域的条目；其他领域有效 TODO 保持不变。

## 实施依赖与执行策略

| Step | Modules touched | Depends on |
|---|---|---|
| 数据库合同与 migration matrix | runtime model、repository、migrations、integration tests | — |
| Service 物理迁移 | runtime services/consumers、imports、architecture tests | 数据库合同 |
| Replay 合同闭环 | workline API、runtime intent/service/processor、API/runtime/integration tests | Service 物理迁移 |
| Reset 与零引用 | scripts、architecture tests | — |
| Current docs 与 TODO 清理 | docs、TODOS.md | Service/Replay 最终名称 |
| Heavy harness 与 benchmark | test support、integration/resilience/load | 数据库合同、Replay Processor |
| 严格 CI | Jenkins/CI config、deployment/architecture tests | heavy runner、evidence schema |
| 最终验收与计划状态 | 全量门禁、evidence、原计划 | 全部步骤 |

逻辑上数据库与 Reset/guardrail 可形成独立 lane，文档盘点也可并行准备；但用户已锁定当前主目录和单一 feature branch、不新建 worktree，因此实际写入按上表依赖顺序串行提交。只允许只读分析或互不修改文件的测试并行执行。

## Implementation Tasks

- [x] **T1 (P1, human: ~1 day / CC: ~1–2h)** — Database — 固化 audit-only 与 conditional envelope 数据库合同
  - Surfaced by: Architecture D2/D6、Test D12
  - Files: RuntimeInbox model、Revision A、Repository claim、migration/schema tests
  - Verify: PostgreSQL fresh/parent/A/B matrix 与 focused repository tests
- [x] **T2 (P1, human: ~0.5 day / CC: ~30–45min)** — Service ownership — 机械迁移 RuntimeInboxService 并锁定导入方向
  - Surfaced by: Architecture D7
  - Files: runtime services/runtime_inbox、consumers、所有生产/测试 imports、architecture guardrail
  - Verify: import/collection、零旧路径引用与 focused service regression
- [x] **T3 (P1, human: ~1–2 days / CC: ~2h)** — Replay — 收敛 request identity、可信 actor、扁平 envelope 与 typed errors
  - Surfaced by: Architecture D3–D5、Code Quality D8/D9、Test D13
  - Files: workline operation API/model、RuntimeInboxService、operation service、processor/context、API/runtime/integration tests
  - Verify: Replay 分层矩阵与 PostgreSQL effect-once/fencing 闭环
- [x] **T4 (P1, human: ~1 day / CC: ~1h)** — Operations — 修复 reset schema identity 与 Mock WMS fail-closed
  - Surfaced by: Code Quality D11、Test D14
  - Files: reset_runtime_data.py、unit/integration reset tests
  - Verify: dry-run/apply、Mock 失败零 mutation、临时 PostgreSQL 主数据保留
- [x] **T5 (P2, human: ~0.5 day / CC: ~30min)** — Guardrails — 统一多文件类型旧入口扫描策略
  - Surfaced by: Code Quality D10、Test D15
  - Files: WorklineInbox retirement guardrail、architecture script、scanner self-tests
  - Verify: `.py/.sh/current .md` offender 与精确 allowlist matrix
- [x] **T6 (P1, human: ~1 day / CC: ~1h)** — Heavy harness — 增加安全、容量、失败分类与强制 cleanup
  - Surfaced by: Test D16
  - Files: PostgreSQL test support、integration/resilience runner tests
  - Verify: preflight unit matrix 与 scenario-error cleanup smoke
- [x] **T7 (P1, human: ~1–2 days / CC: ~2h)** — Performance — 让 benchmark 验证生产 statement、零锁等待和选择性计划
  - Surfaced by: Performance D17–D20
  - Files: RuntimeInbox Repository、load benchmark、benchmark tests/evidence gate
  - Verify: p95≤150ms、吞吐≥1000/s、duplicate=0、lock wait=0、selective plan index gate
- [x] **T8 (P2, human: ~1–2 days / CC: ~2–3h)** — CI — 接入隔离 PostgreSQL 严格验收与 artifact 归档
  - Surfaced by: TODO decision D21-C
  - Files: Jenkins/CI configuration、CI architecture/deployment tests、acceptance runner
  - Verify: CI config guardrail、失败 preflight、成功 artifact retention
- [x] **T9 (P2, human: ~0.5–1 day / CC: ~45min)** — Documentation — 收束 current SSOT、file index、TODO 与旧入口口径
  - Surfaced by: original acceptance Task 1/9 gaps
  - Files: current business/architecture/runtime docs、file index、TODOS.md、original plan
  - Verify: current docs legacy-reference guardrail
- [x] **T10 (P1, human: ~1 day / CC: ~1–2h + test runtime)** — Acceptance — 运行全量门禁并生成当前 commit 新证据
  - Surfaced by: original acceptance Task 8/9 gaps
  - Files: evidence artifact、原实施计划状态
  - Verify: default suite、topology、Ruff、Bandit、quality gate、heavy PostgreSQL、benchmark、CI evidence

T10 最终全量验收已完成。默认全量、topology、collect-only、Ruff、Bandit、quality gate、architecture
guardrail 与 legacy scanner 全部通过。隔离 PostgreSQL 17 runner 完成 migration matrix、processing
integration、两个 crash window、1000 条 backlog / 4 worker benchmark 和正式 evidence validator；benchmark
满足 p95 ≤150ms、吞吐 ≥1000 条/秒、duplicate claim 与 waiting lock 均为 0，选择性 query plan 门禁通过。

正式 artifact 位于
`/Users/kaizhou/codeDev/wes_backend/reports/runtime-inbox-acceptance/runtime-inbox-claim-benchmark.json`，
同目录保留 diagnostic、JUnit 与日志。artifact 不提交；最终 HEAD 验收会覆盖生成该文件，并以其中
`repository.commit_sha`、`repository.dirty=false` 和 `verdict.passed=true` 作为 commit-bound 证据。

## T1–T10 提交与证据摘要

| Task | 主要提交 | 当前证据 |
|---|---|---|
| T1 | `08dff017` 及后续数据库边界修复 | Revision A named CHECK、audit-only、conditional envelope、migration matrix |
| T2 | `0dc327fc`、`41dfe1b7`、`ca591cb0`、`2836e74e` | 正式 `services/runtime_inbox` 导出、旧 consumers service 零引用 |
| T3 | `6aa18ce7` 至 `3a62b48d` | `REPLAY_REQUEST`、request/actor/reason、扁平 source chain、typed errors 与 effect-once |
| T4 | `c7cf6ed3`、`e8106f91` | schema-qualified reset、Mock fail-closed、主数据保护 |
| T5 | `4debb9d0` 至 `e8aa1c98` | `.py/.sh/current .md` scanner、路径/symlink fail-closed、自测矩阵 |
| T6 | `2c74382f` 至 `1dc6e319` | 显式 URL/safe host/capacity preflight、临时库强制 cleanup |
| T7 | `aa89b90f`、`3d98b37f`、`ac7a05b3` | 生产 statement fingerprint、零锁等待、selective plan、commit-bound evidence validator |
| T8 | `06b955e4`、`004d135b` | 隔离 PG17 CI、严格 runner、clean checkout、JUnit/log/diagnostic/evidence 归档 |
| T9 | 本次文档收束提交 | Current docs legacy scanner、文档一致性与本地链接测试、file index/TODO/计划同步 |
| T10 | 本次最终验收收束提交 | 默认全量、静态与 quality 门禁、隔离 PG17 migration/processing/crash/benchmark、commit-bound artifact |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 未运行，本轮为后端验收闭环 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 未运行 outside voice |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 19 issues, 0 critical gaps, all folded |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端范围，无 UI 变更 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

**VERDICT:** ENG CLEARED — ready to implement

NO UNRESOLVED DECISIONS
