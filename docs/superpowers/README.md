# 项目文档生命周期与外部归档索引

`plans/` 与 `specs/` 只保留仍承担当前执行、验收或机器门禁职责的文档。项目其他目录同样只保留当前
架构真源、有效业务/硬件合同和仍承担实际运行职责的文档。已完成、被后续设计取代或仅用于历史决策追溯的文档统一移出项目，归档到
`../archive_docs/wes_backend/`；项目内不保留副本、占位文件、软链接或转发文档。

## 当前保留文档

| 文档 | 保留原因 | 当前状态 |
| --- | --- | --- |
| `../architecture/SRS.md` | 产品范围、参与方职责和功能/非功能需求真源 | Current Requirements Baseline |
| `specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` | WES 最小执行架构主真源 | Approved |
| `specs/2026-08-06-wes-outbound-operation-top-level-design.md` | 自动出库 `PickingTask` 与双面货架执行业务真源 | ReviewRequired |
| `plans/2026-07-31-wes-test-semantics-and-weight-convergence.md` | 测试所有权与重量治理 | 分阶段执行 |
| `plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 十一阶段收敛总控 | In progress；Phase 2 已完成，Phase 3 Task 1 三项跨能力门禁阻断 |
| `plans/2026-08-05-wes-wms-thin-access-convergence.md` | Phase 3 无状态 WMS 业务 ACL 暗构建 | inbound `NONE` 与单项批准模板已冻结；每个获批 operation 使用独立小 PR 实施 |

## 项目外历史归档

以下文档已完成或已由当前设计取代，仅在项目外保留完整
历史内容：

- `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`
- `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-03-wes-wms-thin-access-convergence.md`
- `../archive_docs/wes_backend/docs/architecture/adr/2026-05-26-wms-integration-domain.md`
- `../archive_docs/wes_backend/docs/business/wms_full_factory_operation_blueprint.md`
- `../archive_docs/wes_backend/docs/business/wms_rcs_interface_requirements.md`

- `../archive_docs/wes_backend/2026-07-03-phase4-design-with-residuals.md`
- `../archive_docs/wes_backend/2026-07-04-runtime-evidence-readiness.md`
- `../archive_docs/wes_backend/2026-07-15-workline-plugin-system-capability-platform-design.md`
- `../archive_docs/wes_backend/2026-07-24-northbound-capability-simplification.md`
- `../archive_docs/wes_backend/2026-07-24-northbound-interaction-simplification.md`
- `../archive_docs/wes_backend/2026-07-27-remove-legacy-unbound-runtime.md`
- `../archive_docs/wes_backend/2026-07-28-wms-full-factory-integration-design.md`
- `../archive_docs/wes_backend/external-contract-profile.md`
- `../archive_docs/wes_backend/integration-lab-and-simulator.md`
- `../archive_docs/wes_backend/docs/integration/wms_rcs_interface_requirements.md`
- `../archive_docs/wes_backend/session-correlation-matrix.md`
- `../archive_docs/wes_backend/sorter-inbound-capability-spec.md`
- `../archive_docs/wes_backend/target-state-contract.md`
- `../archive_docs/wes_backend/wms-mock-northbound-capability-requirements.md`
- `../archive_docs/wes_backend/wms-northbound-acceptance-and-cutover.md`
- `../archive_docs/wes_backend/wms-northbound-feasibility-report.md`
- `../archive_docs/wes_backend/workline-restructuring-implementation.md`
- `../archive_docs/wes_backend/docs/architecture/workline-and-plugin-restructuring.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-overview.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-architecture.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-data.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-interface.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-state.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-security.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-nonfunctional.md`
- `../archive_docs/wes_backend/docs/architecture/workline-restructuring-module.md`
- `../archive_docs/wes_backend/docs/architecture/adr/0001-phase2-runtime-ownership.md`
- `../archive_docs/wes_backend/docs/architecture/adr/2026-07-21-wms-operation-identity.md`
- `../archive_docs/wes_backend/docs/architecture/ARCHITECTURE_EVOLUTION_ROADMAP.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0001-b方案选择与capability-freeze.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0002-外部履约-11态机加timeout.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0003-typed-external-reference-evidence.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0004-plane-rbac-bounded-snapshot.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0005-idempotency-composite-key.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0006-wms-callback-hmac.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0007-execution-correlation-key.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0008-authority-matrix.md`
- `../archive_docs/wes_backend/docs/architecture/adr/workline-restructuring/0009-shared-contracts-package.md`
- `../archive_docs/wes_backend/docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`
- `../archive_docs/wes_backend/docs/architecture/reviews/decision-audit-trail.md`
- `../archive_docs/wes_backend/docs/architecture/reviews/phase2-coverage-baseline-2026-06-28.md`
- `../archive_docs/wes_backend/docs/architecture/reviews/workline-restructuring-v4-review-2026-06-23.md`
- `../archive_docs/wes_backend/docs/architecture/legacy-runtime-migration-spec.md`
- `../archive_docs/wes_backend/docs/architecture/legacy-cleanup-execution-plan.md`
- `../archive_docs/wes_backend/docs/architecture/phase3-phase4-production-evidence-bundle.md`
- `../archive_docs/wes_backend/docs/architecture/runtime-orchestration-spec.md`
- `../archive_docs/wes_backend/docs/architecture/runtime-ownership-map.md`
- `../archive_docs/wes_backend/docs/architecture/workline-active-objects-spec.md`
- `../archive_docs/wes_backend/docs/architecture/cell-reservation-spec.md`
- `../archive_docs/wes_backend/docs/architecture/material-location-query-spec.md`
- `../archive_docs/wes_backend/docs/architecture/smt-ng-wms-reconciliation-spec.md`
- `../archive_docs/wes_backend/docs/business/e2e_conveyor_plan.md`
- `../archive_docs/wes_backend/docs/business/workline_business_data_event_flow_spec.md`
- `../archive_docs/wes_backend/docs/business/workline_material_flow_runtime.md`
- `../archive_docs/wes_backend/docs/business/workline_plugin_architecture_design.md`
- `../archive_docs/wes_backend/docs/business/workline_runtime_workflow_guide.md`
- `../archive_docs/wes_backend/docs/business/rough_sorter_runtime_flow.md`
- `../archive_docs/wes_backend/docs/business/smt_sorter_inbound_workflow_guide.md`
- `../archive_docs/wes_backend/docs/integration/interact_backend.md`
- `../archive_docs/wes_backend/docs/workline_flow_diagram.md`
- `../archive_docs/wes_backend/docs/workline/plugin_manifest_contract.md`
- `../archive_docs/wes_backend/docs/系统架构图.eddx`
- `../archive_docs/wes_backend/docs/superpowers/specs/2026-07-26-code-quality-incremental-optimization-design.md`
- `../archive_docs/wes_backend/docs/superpowers/plans/2026-07-26-code-quality-incremental-optimization.md`

原项目内 `docs/archive/` 与 `docs/superpowers/archive/` 的既有资料已按原目录层级完整迁移到：

- `../archive_docs/wes_backend/docs/archive/`
- `../archive_docs/wes_backend/docs/superpowers/archive/`

这些路径只用于历史追溯，不属于当前架构、合同、实施或机器门禁真源。

## 本轮审计保留项

以下文档即使包含旧 Runtime 名称，也暂不归档，因为它们仍直接承担现有实现的排障、发布、观测或机器门禁职责：

- `docs/workline_diagnostics_quickstart.md`
- `docs/workline_runtime_hold_quickstart.md`
- `docs/operations/workline-plugin-migration-inventory.md`
- `docs/contracts/observability-contract.md`
- `docs/contracts/runtime-toggle-governance.md`
- `docs/integration/wms_caller_checklist.md`
- `docs/integration/workline_device_error_code_standardization.md`
- `docs/operations/northbound-operation-slo-catalog.md`
- `docs/runbooks/northbound-operation-observability.md`
- `docs/devops/prod-release-deploy.md`
- `docs/contracts/evidence-catalog.md`

这些文档只能说明收敛前的 `implementation_baseline`，不得作为新架构设计输入。对应脚本、API、观测信号或发布门禁在收敛计划中删除或替换后，应在同一任务中将文档一并归档。

## 当前核心合同

以下文档只定义 WES 核心基础能力、共享外部边界或机器门禁：

- `docs/architecture/authority-matrix.md`
- `docs/architecture/device-command-contract.md`
- `docs/architecture/architecture-guardrails-spec.md`
- `docs/architecture/legacy-cleanup-matrix.csv`（机器可读清理清单）
- `docs/contracts/wms-northbound-interaction-contract.md`
- `docs/integration/callback_event_validation_principles.md`

核心合同不得定义具体厂商命令、WMS 业务规则、工作线执行映射或客户流程。核心测试只验证共享传输、持久化、幂等、可靠性和
最小执行对象，不得以具体业务流程作为基础能力验收。

## 当前业务与外部输入

以下资料只作为对应 Adapter 或 WorkLine 插件的外部输入，不是 WES 核心架构真源，也不得进入核心测试：

- `docs/hardware/wms_rcs_interface_requirements.md`
- `docs/hardware/CTU&AGV对接流程（完成80%）.pdf`
- `docs/hardware/SMT分拣机ECS接口调用说明书V1-20260318.md`
- `docs/hardware/SMT分拣机ECS接口调用说明书V1-20260318.pdf`
- `docs/hardware/SMT粗分机接口调用说明书20260321-v1.md`
- `docs/hardware/SMT流水线接口调用说明书20260320-v1.md`
- `docs/hardware/粗分机硬件供应商联调操作手册.md`

`docs/hardware/` 以硬件厂商原始 PDF/资料为保留主体；同目录 Markdown 包含便于检索的人工转写及面向供应商的
联调说明，属于派生资料，不得覆盖原始文件，也不得被视为当前 Adapter 合同或 WES 架构真源。派生资料中的字段
归一化、插件名称、Session/Outbox/Inbox/Hold 等当前实现描述只用于偏差识别，不能反向约束最终架构。差异由当前
架构合同及具体 Adapter 文档说明。具体 Adapter 包拥有厂商实现、合同测试和 fixture；具体插件包只拥有业务
Decision、对象推进及其测试和 fixture。基础、Adapter 和业务能力不得互相替代验收。

## 本轮追加归档

以下历史设计已移至项目外 `../archive_docs/wes_backend/`，项目内不保留副本或转发文件：

- `../archive_docs/wes_backend/docs/business/inbound_acceptance_steps.md`
- `../archive_docs/wes_backend/docs/business/rough_sorter_scan_decision_contract.md`
- `../archive_docs/wes_backend/docs/integration/third_party_integration_whitepaper.md`
- `../archive_docs/wes_backend/wms_rcs_interface_requirements.pdf`（旧 WMS/RCS 接口汇编，不属于硬件厂商原始资料）
- `../archive_docs/wes_backend/docs/architecture/legacy-cleanup-matrix.md`（Phase 0 历史分析；机器清单继续保留为 CSV）
- `../archive_docs/wes_backend/.serena/memories/learnings/code-reviews/` 下五份 2026-03-07/16 历史评审
- `../archive_docs/wes_backend/.serena/memories/learnings/tdd-refactor-success-2026-03-16.md`
- `../archive_docs/wes_backend/.superpowers/sdd/` 下旧 WMS 全工厂实施简报、进度、修复报告和评审差异
- `../archive_docs/wes_backend/.learnings/e2e_testing_smt_classifier.md`
- `../archive_docs/wes_backend/.learnings/smt_classifier_workflow_debug.md`
- `../archive_docs/wes_backend/.learnings/LEARNINGS.md`（含旧 Runtime、Session、Inbox、NullPlugin 等历史实现经验；完整原文仅供追溯）
- `../archive_docs/wes_backend/.learnings/ERRORS.md`（历史工具错误日志，不作为当前架构或运行约束）
- `../archive_docs/wes_backend/docs/auth/api_authentication_design.md`
- `../archive_docs/wes_backend/docs/auth/api_authentication_summary.md`
- `../archive_docs/wes_backend/docs/architecture/business-legacy-absence-ledger.md`（一次性审计快照；机器门禁继续由 CSV 真源承担）
- `../archive_docs/wes_backend/.claude/skills/wes-module-creator-1.0.0/OPTIMIZATION_SUMMARY.md`

忽略规则曾隐藏的本地 Wiki、会话计划、QA/部署报告、备份、旧 PR 正文、`claudedocs/`、每日项目日志、根目录
`SESSION-STATE.md` / `MEMORY.md`、`memory/.learnings/LEARNINGS.md` 和六份 `.env.backup.*` 已经按原目录层级移至
`../archive_docs/wes_backend/local-artifacts/`。这些资料只承担历史追溯，不属于当前项目搜索范围。

旧 Understand Anything 生成缓存及其 63 MB trash 已移至
`../archive_docs/wes_backend/local-artifacts/ua-stale-2026-08-04/`；项目内只保留本地工具配置，不保留引用已归档设计的搜索索引。

本轮同时逐文件比对所有被删除并归档的资产与当前 `HEAD` 原始 blob。规范归档目标均保持原文一致；此前被
目标架构措辞改写的副本保存在
`../archive_docs/wes_backend/_quarantine/2026-08-04-rewritten-before-original-restore/`，明确不属于历史原文或当前真源。

## 归档判定规则

满足任一条件即可归档：

1. 对应实现和验收已合入，且没有剩余执行门禁。
2. 核心决策已被后续 ADR、规格或实现取代。
3. 文档只承担历史决策或发布证据，不再是当前执行入口。

即使实现已完成，只要自动化门禁仍直接读取文档，或文档仍承载明确未完成范围，就继续留在活跃目录。
