# Superpowers 计划与规格文档生命周期

`plans/` 与 `specs/` 只保留仍承担当前执行、验收或机器门禁职责的文档。已完成、被后续设计取代、
仅用于历史决策追溯的文档统一移动到 `archive/plans/` 与 `archive/specs/`。

## 2026-07-26 代码现状验收

验收基线：`develop`，HEAD `38fd6d23`（`v0.20.2.0`）。

本轮按以下证据交叉验收：

- Git 历史：计划/规格最后修改对应的发布 PR 和 merge commit。
- GitNexus：RuntimeInbox replay 流、Workline Plugin/System Capability、WMS EFFECT 状态查询的当前符号和调用关系。
- 当前代码：目标模块存在性、legacy/shadow 模块缺失、架构门禁和状态字段。
- 自动化测试：测试拓扑、Phase 4 文档合同、RuntimeInbox 文档合同、插件平台与 WMS 状态查询合同。

### 当前保留文档

| 文档 | 保留原因 | 当前状态 |
| --- | --- | --- |
| `plans/2026-07-04-runtime-evidence-readiness.md` | `check_runtime_evidence_readiness_gate.py` 的机器合同输入 | 开发/测试与 evidence gate 已闭合；现场发布仍需 production evidence/canary |
| `plans/2026-07-10-runtime-inbox-single-source-of-truth.md` | RuntimeInbox 文档一致性测试的验收合同 | T1–T10 已完成，继续作为当前事实验收证据 |
| `plans/2026-07-24-northbound-capability-simplification.md` | WMS 北向剩余切换门禁的执行入口 | T1–T8 已落地；T9 真实 WMS 联调与 forward-only 切换未完成 |
| `specs/2026-07-03-phase4-design-with-residuals.md` | Phase 4 文档合同测试的 umbrella 输入 | 开发/测试闭环已实现；生产现场门禁仍有效 |
| `specs/2026-07-12-runtime-inbox-acceptance-closure-design.md` | RuntimeInbox T1–T10 验收合同 | 已完成，继续被自动化一致性测试读取 |
| `specs/2026-07-15-workline-plugin-system-capability-platform-design.md` | 完整平台目标与剩余任务的权威规格 | T3–T7 最小切片已交付；T1 Remaining、其他 Workline、T8、T9 未完成 |

### 本轮归档文档

| 文档 | 验收结论 |
| --- | --- |
| `archive/plans/2026-06-27-test-suite-slimming.md` | PR #65 已落地测试拓扑 guardrail、默认快速回归边界和测试归位 |
| `archive/plans/2026-06-27-test-suite-slimming-phase2.md` | Phase 2 目标已并入 PR #65 的最终测试治理结果 |
| `archive/plans/2026-06-27-workline-phase-1-packet-d.md` | Packet D 已交付，后续北向 typed capability 设计已取代其执行入口 |
| `archive/plans/2026-07-06-phase1-phase4-residuals.md` | 开发/测试 residual 已关闭，后续 Phase 5 与 RuntimeInbox 计划已完成剩余闭环 |
| `archive/plans/2026-07-07-phase3-production-artifacts.md` | 文档明确 DONE，Phase 3/4 evidence bundle 已生成并通过门禁 |
| `archive/plans/2026-07-07-phase5-business-lane.md` | PR #79 已合入并通过 business readiness gate |
| `archive/plans/2026-07-07-phase5-business-legacy-destructive-cleanup.md` | PR #79 已完成业务 legacy 清理；保留为 absence ledger 历史证据 |
| `archive/plans/2026-07-07-workline-restructuring-final-cleanup.md` | PR #80 已完成最终清理 |
| `archive/plans/2026-07-08-guardrail-shorthand-process-naming-cleanup.md` | PR #82 已完成过程命名护栏稳定化 |
| `archive/plans/2026-07-08-process-naming-debt-cleanup.md` | PR #81 已完成命名债务收敛 |
| `archive/plans/2026-07-09-design-principle-fixes.md` | PR #84 已落地有效修复，其余旧 WMS/文档拆分设想已被后续架构取代 |
| `archive/plans/2026-07-09-stale-test-cleanup.md` | 文档已有 Final Archive Note，PR #83 已合入 |
| `archive/plans/2026-07-13-celery-single-async-runtime.md` | PR #85 已完成单异步运行时与连接容量修复 |
| `archive/plans/2026-07-14-agent-log-output-convergence.md` | PR #85 已完成日志输出收敛 |
| `archive/plans/2026-07-15-workline-active-inventory-foundation.md` | PR #86 已完成 foundation；扩容触发条件已保留在 `TODOS.md` |
| `archive/plans/2026-07-16-rough-sorter-scan-decision-contract.md` | PR #87 已批准并固化 13-case 合同 |
| `archive/plans/2026-07-16-workline-plugin-system-capability-minimum-slice.md` | PR #88 已交付 T3–T7 最小切片；完整剩余范围由平台规格继续承载 |
| `archive/plans/2026-07-22-t8c-typed-external-http-transport.md` | PR #90 已交付 typed transport |
| `archive/plans/2026-07-23-confirm-inbound-typed-effect-cutover.md` | PR #90 已完成 `confirm_inbound` typed EFFECT 切换 |
| `archive/plans/2026-07-23-notify-package-binding-typed-effect-cutover.md` | PR #90 已完成 `notify_pkg_binding` typed EFFECT 切换 |
| `archive/plans/2026-07-23-system-outbox-dispatch-concurrency.md` | PR #90 已完成 Outbox 并发合同 |
| `archive/plans/2026-07-25-wms-mock-northbound-capability.md` | PR #92/#93 已完成 Mock WMS 北向合同和验收修复 |
| `archive/specs/2026-06-18-workline-command-result-manifest-cleanup-spec.md` | PR #38 已完成 COMMAND_RESULT manifest 清理 |
| `archive/specs/2026-06-19-workline-multi-object-state-machine-design.md` | 后续 Workline restructuring 与 Plugin Runtime 已吸收其目标态 |
| `archive/specs/2026-06-23-workline-c0-resource-projection-foundation.md` | PR #40 已完成 C0 resource projection 基座 |
| `archive/specs/2026-06-25-workline-restructuring-phase-0-spec.md` | Phase 0 护栏与目标态锁定已完成 |
| `archive/specs/2026-06-26-workline-restructuring-phase-1-spec.md` | Phase 1 已交付并被后续 RuntimeInbox/Plugin 平台演进取代 |
| `archive/specs/2026-06-27-workline-phase-1-packet-d-design.md` | Packet D 已实施，当前北向合同由新 ADR 和状态查询模型承载 |
| `archive/specs/2026-07-21-northbound-capability-extraction-design.md` | PR #90 已实施，动态多 Provider/shadow 部分又被北向简化 ADR 取代 |

## 归档判定规则

满足任一条件即可归档：

1. 对应实现和验收已合入，且没有剩余执行门禁。
2. 核心决策已被后续 ADR、规格或实现取代。
3. 文档只承担历史决策或发布证据，不再是当前执行入口。

即使实现已完成，只要自动化门禁仍直接读取文档，或文档仍承载明确未完成范围，就继续留在活跃目录。
