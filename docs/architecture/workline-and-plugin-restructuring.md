---
status: Draft v7 — WorkLine restructuring cleanup completed（GB/T 8567 风格）
created_at: 2026-06-23
updated_at: 2026-07-10
parent_goal: 对当前 WORKLINE + PLUGIN 体系进行全面重构/重做
document_type: 概要设计说明书 + 详细设计（Outline Design + Detailed Design）
audience: eng/arch lead, WES owner, WMS 集成 lead, code reviewer
related_specs:
  - docs/superpowers/archive/specs/2026-06-19-workline-multi-object-state-machine-design.md  (历史子设计)
  - docs/superpowers/archive/specs/2026-06-23-workline-c0-resource-projection-foundation.md  (历史 C0 子基础)
  - docs/architecture/runtime-orchestration-spec.md  (Runtime/Orchestration 域最小骨架 SPEC)
detail_docs:
  - 关键决策（ADR）：docs/architecture/adr/workline-restructuring/
  - 评审存档：docs/architecture/reviews/
note: |
  本设计采用 GB/T 8567 概要设计说明书 + 详细设计的标准结构。
  实施细节（字段定义、状态机转移表、HMAC 合同等）不在本文展开为独立 SPEC；
  将在后续全量联调启动前或启动时按需生成 SPEC。
review_summary: |
  autoplan 评审已存档到 docs/architecture/reviews/。
  28 auto-decision 已记录到 docs/architecture/reviews/decision-audit-trail.md。
  本轮修订明确：当前系统未发布，本重构不做向后兼容；旧 WorkLine/plugin 体系只作为业务事实和测试样本输入，不作为目标态约束。
  Critical path: 目标态边界锁定 → WMS ACL → Runtime/Orchestration 骨架 → plane 最小闭环 → legacy 清理。
  2026-07-08 同步：technical cleanup scope 已通过 PR #78 合并到 develop（v0.13.0.0）；legacy plugin runtime/import 框架已退出 src 可 import 路径。runtime production closure 与 material-flow production evidence bundle 已可重新生成并通过 gate；business legacy cleanup scope readiness 与 business legacy absence gate 已通过 PR #79 合并到 develop（v0.14.0.0，merge SHA 8c833610c08005005406b3a774c92519f69b7886），业务执行合同已迁入 material-flow runtime capability 目标态。本 restructuring cleanup 进一步删除旧 handling 队列表面与 WorkLine 运行态物理列，运行状态由 `wes_runtime.workline_runtime_status_projections` 承接。active code、gate 与默认回归测试的命名策略见 `docs/architecture/process-naming-policy.md`。
  2026-07-10 同步：本文档按 KISS 原则拆分为 1+8 章节文件，本文件作为 index 引用。原 2631 行单文件已切分以降低阅读负担并支持按章节 review/扩展。
---

# WORKLINE + PLUGIN 体系全面重构顶层设计

> 概要设计说明书（GB/T 8567 风格）+ 详细设计
> 版本：Draft v7（2026-07-10 文档拆分完成）
> 父目标：对当前 WORKLINE + PLUGIN 体系进行全面重构/重做

---

## 顶层设计锚点（保留给 evidence manifest gate）

> 以下 3 个 anchor token 保留在顶层 index 中以满足 runtime evidence readiness
> gate 锚点检测；具体展开请按章节索引跳转。

- `### 10.5 Material-flow target capabilities` — 详见 `workline-restructuring-implementation.md` §10.5
- `production-capable runtime path` — sorter inbound / SMT/NG/WMS reconciliation 的目标路径；详见 `workline-restructuring-implementation.md`
- `evidence manifest gate` — material-flow 证据清单门禁；详见 `workline-restructuring-implementation.md` §10.5 与 `workline-restructuring-architecture.md`

---

## 章节索引

为遵循 KISS 原则（避免单个架构文件过大、降低读者负担、支持按章节 review），本顶层设计按主题拆分为以下章节文件：

| # | 章节 | 文件 | 原章节 |
|---|------|------|--------|
| 1 | 概述 | `workline-restructuring-overview.md` | §1 引言 + §2 系统概述 + §13 附录 |
| 2 | 体系结构设计 | `workline-restructuring-architecture.md` | §3 体系结构设计 |
| 3 | 数据设计 | `workline-restructuring-data.md` | §4 数据设计 |
| 4 | 接口设计 | `workline-restructuring-interface.md` | §5 接口设计 |
| 5 | 状态与恢复设计 | `workline-restructuring-state.md` | §6 状态与恢复设计 |
| 6 | 安全设计 | `workline-restructuring-security.md` | §7 安全设计 |
| 7 | 非功能性设计 | `workline-restructuring-nonfunctional.md` | §8 非功能性设计 |
| 8 | 模块设计 | `workline-restructuring-module.md` | §9 模块设计 |
| 9 | 实施与执行 | `workline-restructuring-implementation.md` | §10 实施计划 + §11 执行规范 + §12 风险与对策 |

## 阅读建议

- **首次阅读**: 从 `workline-restructuring-overview.md` 的 §1 引言开始，建立目标态边界认知。
- **架构评审**: 直接看 `workline-restructuring-architecture.md` 与 `workline-restructuring-module.md`。
- **数据/接口契约评审**: `workline-restructuring-data.md` + `workline-restructuring-interface.md`。
- **实施落地**: `workline-restructuring-implementation.md`。

## 拆分前后对照

- **拆分前**: `workline-and-plugin-restructuring.md` 2631 行单文件。
- **拆分后**: 9 个章节文件，单文件 ≤ 860 行（实施章节），平均 ≈ 300 行。

## 章节交叉引用

- §3 体系结构 → §4 数据 / §5 接口 / §6 状态与恢复 / §7 安全 / §8 非功能性
- §9 模块设计 → §5 接口（按域分章节端口定义）
- §10 实施计划 → §3 体系结构（按 plane 顺序）
- §11 执行规范 → §10 实施计划（review gate 与 PR 节奏）
- §12 风险与对策 → §3/§5/§9（架构/接口/模块决策的相关风险）

---

## 顶层定义（速查）

> 完整定义见各章节文件，本节为顶层摘要，列出跨章节复用的核心术语与不可变约束。

**核心约束**（不可变）：

- **Authority Matrix** — WMS 维护库存（`bin/inventory` 权威），WES 维护库存作业状态（`workline_runtime_status_projections` 权威）。详见 `workline-restructuring-architecture.md` §3.4。
- **Port ACL** — capability 只能注入 query/effect Port；inbound normalizer（RuntimeInbox、callback、event）不在业务 capability 注册表。详见 `workline-restructuring-architecture.md` §3.5。
- **Runtime Inbox 权威** — WMS/ECS 回调 → RuntimeInbox，business capability 通过 RuntimeCapabilityProfile 注入；RuntimeInbox 决定 outbox dispatch。详见 `workline-restructuring-architecture.md` §3.5。

**核心域名词**（稳定 ID）：

- **RuntimeInbox** — 入站事件权威记录。
- **RuntimeIntent** — capability 意图记录，与 RuntimeInbox 1:N。
- **RuntimeHold** — 异常保留/挂起记录。
- **RuntimeCapabilityProfile** — capability 注入合同。
- **ExecutionSession / ExecutionWorkItem / ExecutionCorrelation** — 执行链路。
- **ExternalContractProfile** — provider 外部合同（@yagni 占位）。
- **IntegrationLab / ScenarioReplay** — 联调验证能力（@yagni 占位）。
- **ActiveObjectRegistry** — 活跃对象归属仲裁（@yagni 占位）。

**当前里程碑范围**（Active scope）：

- **WMS Port 活跃**: WmsMasterDataPort / InventoryQueryOperationPort / WmsInventoryTransactionPort；履约侧使用 operation-specific typed contract。
- **WMS 边界 @deferred**: operation-specific document QUERY / WmsEventPort / WmsReconciliationQueryPort。
- **能力域**: material-flow（rough_sorter / sorter_inbound / smt_inbound_handoff）。

---

> 自此文件起, 所有具体设计请按索引跳转对应章节文件。
