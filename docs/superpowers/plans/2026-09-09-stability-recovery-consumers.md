# 诊断消费与领域恢复入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让联调人员在现有页面看清阻断事实，并准确使用已有领域恢复动作。

**Architecture:** 前端组合基础查询结果和当前联调 run 快照，不持久化第二套状态、不裁决物理完成。业务动作仍由原领域 Service 校验；具体插件消费单独验收，诊断 run 不成为业务引擎。

**Tech Stack:** Vue 3、TypeScript、现有 OpenAPI/Zod/权限生成流程、Vitest、现有 SSE 与 FastAPI API。

**Spec:** [总计划 R1/R2/R7/R8](2026-09-09-stability-recovery-master.md)；[A2/A3 查询接口](2026-09-09-stability-recovery-foundation.md)；当前 Transport、DeviceCommand、PickingTask 合同。

## Global Constraints

- 总计划红线适用。本文件只拥有消费和操作展示，不复制基础幂等、HTTP、claim、deadline 或物理状态机。
- 前端根目录为 `/Users/kaizhou/codeDev/wes_frontend`；实施前读取其 AGENTS.md 并冻结独立 HEAD/dirty 指纹。
- 不创建新运营首页、统一告警平台或跨领域批量恢复按钮；复用现有页面和明确身份的详情入口。
- SSE 仅触发刷新/辅助观察；以基础查询为可靠事实。浏览器断线、刷新、跳页不改变服务端 run。
- 用户当前未要求实现新插件业务；B3 只闭合当前通用联调消费者，正式工作线验收不混入核心测试。

## 文件与所有权

B1 修改 frontend 的 `src/api/modules/transport.ts`、`src/api/modules/wmsDiagnostics.ts`、`src/views/ops/transport-diagnostics/TransportTaskDetail.vue`、`src/views/ops/transport-diagnostics/useTransportDiagnostics.ts`、`src/views/ops/wms-diagnostics/DiagnosticsDetailPanel.vue`、`src/views/ops/wms-diagnostics/useWmsDiagnostics.ts`。B2 使用现有设备诊断与手工出库界面。后端只读查询归 A；业务规则或校验缺口必须回对应 owner 建立复现，不在 Vue 增加权威判定。

### Task B1：在原详情中关联可靠事实

**Files:** 上述 B1 文件；按现有生成器刷新 canonical/types/Zod/permissions。

**Test:** frontend 的 `tests/unit/views/ops/transport-diagnostics/useTransportDiagnostics.test.ts`、`tests/unit/views/ops/transport-diagnostics/TransportDiagnosticsPage.test.ts`、`tests/unit/views/ops/wms-diagnostics/useWmsDiagnostics.test.ts`、`tests/unit/views/ops/wms-diagnostics/WmsDiagnosticsPage.test.ts`。

**Interfaces:** 消费 A2 原 Transport GET、A2 callback-receipts GET 和 A3 两条 execution GET。前端不另定同名 DTO。页面呈现“实际状态、最后记录时间、等待对象、原 identity、详情链接”；没有数据时明确显示“未找到对应可靠记录”，不显示“从未接收”。

- [ ] 冻结 A2/A3 canonical，按前端既有流程生成合同；禁止手改 snapshot SHA、generated types 或本地复制 DTO 绕过校验。
- [ ] 为 Transport 表现建立测试：显示冻结 deadline；UNKNOWN 显示“等待权威结果”；outcome_version 大于 published_outcome_version 显示“待发布”；已发布不能显示“业务已推进”。
- [ ] 为 WMS 表现建立测试：HTTP 202 与 Evidence PENDING 并列；可靠义务 COMPLETED 不冒充 PickingTask 完成；Redis 近期记录不可用时仍可按 operation/operation_id 独立查询；404/503 保留不确定性。
- [ ] 将新增字段放入现有详情；复用已有 identity 筛选/链接。WMS 页面保留独立 identity 查询输入，即使没有缓存 exchange 也可查询可靠对象；不要求先找到一条 Redis 记录。
- [ ] Transport 详情提供回调 identity 精确查收据入口；拒绝收据与任务 Evidence 分开显示。收据只保存宽泛错误码时如实展示，不伪造缺失字段名，不按时间猜测其所属任务。
- [ ] 复用已有订阅生命周期；SSE 重连后重新获取所选对象，响应晚于选择变化时丢弃旧请求结果；不重复建立第二条 SSE 管线。无可靠推送覆盖时使用现有手动刷新入口。
- [ ] 在前端运行 `pnpm exec vitest run tests/unit/views/ops/transport-diagnostics tests/unit/views/ops/wms-diagnostics`；执行原合同/权限验证，再用浏览器验证断线、恢复、刷新和任务切换。

**验收：** 工程师不用数据库权限即可识别是接口接纳、内部应用、发布还是外部事实等待。页面只陈述已有数据，错误/缺失的关联不被强行拼接。

### Task B2：明确现有操作语义，提供单一恢复手册

**Files:**
- Inspect/reuse backend: `src/app/device/v1/reconciliation.py`、`src/app/device/services/device_evidence_service.py`、`src/app/execution/services/wms_confirmation_service.py`、`src/app/workline_integration_debug/service.py`、`src/app/workline_integration_debug/v1/runs.py`。
- Conditional modify backend（仅按下述缺口接管流程触发）：设备领域为 `src/app/device/v1/reconciliation.py`、`src/app/device/services/device_evidence_service.py`、`src/app/device/services/device_command_service.py`；可靠义务领域为 `src/app/execution/services/wms_confirmation_service.py`；prepare 联调领域为 `src/app/workline_integration_debug/service.py`、`src/app/workline_integration_debug/v1/runs.py`。仅修复已有操作缺失的合同要求，不预先修改全部文件。
- Modify frontend where labels/details differ: `src/views/ops/device-diagnostics/DeviceDiagnosticsPage.vue`、`src/views/ops/manual-outbound-integration/useManualOutboundIntegration.ts`；相关模板以该 composable 的直接消费者为固定传播面。
- Create docs: backend `docs/devops/execution-recovery.md`。
- Reuse tests: backend `tests/api/test_device_reconciliation_api.py`、`tests/runtime/device_command/test_manual_reconciliation_service.py`、`tests/runtime/execution/test_wms_confirmation_service.py`；frontend `tests/unit/views/ops/manual-outbound-integration/useManualOutboundIntegration.test.ts`、`tests/unit/views/ops/device-diagnostics/DeviceDiagnosticsPage.test.ts`。

**Interfaces:** 原 `get_event_command_block`、`reprocess_blocked_event`、`reconcile_delivery_unknown_as_device_idle` 不改签名；原 `retry_wms_action` 的 `expected_version`、`client_request_id`、`wms_original_prepare_voided_confirmed` 不被泛化。`requeue_reconciling` 仅为已有基础方法，本任务不把它直接公开为万能重发 API。

**条件修复责任：** 主 Agent 在 B2 变更清单中为每个已复现缺口指定一名对应领域的实施 owner，该 owner 独占相关生产路径及审计/并发测试；B2 前端 owner 只消费结果。若同一路径已由 A4 owner 处理，缺口直接交由该 owner 在原切片闭合，B2 记录依赖，不再建立第二个修改者或重复实现。

- [ ] 将现有按钮逐项对照总计划恢复表；固定准入、操作员权限、原身份、block_id/expected_version、外部确认要求和审计 owner。
- [ ] 对文案/展示不足建立前端交互测试：prepare 操作明确标为“作废确认后重新准备”，显示将产生新 identity；禁止称为单纯原请求重试。设备界面展示原 blocker，操作成功后刷新，版本冲突时重新读取而非自动重试。
- [ ] 已有操作符合合同则原样复用；操作失败展示具体拒绝原因，不能在客户端强制允许。若复现后端审计或并发保护缺口，先记录合同条款、触发场景及受影响操作，并暂停依赖该缺口的验收步骤；其余独立步骤继续。
- [ ] **缺口接管：** 主 Agent 按上述领域分配唯一 owner，将实际生产符号、调用者、直接/间接测试、HEAVY mapping 与其他切片依赖补入冻结清单，执行 upstream impact；出现清单外 HIGH/CRITICAL 影响时取得范围授权后再修改。领域 owner 建立 RED 并做最小修复，按原门禁闭合后交回 B2 验收。不改变现有准入、物理退出事实或恢复语义；需要改变这些合同或触及所列范围以外文件时先报告并重新冻结范围，不由前端实现替代规则。
- [ ] 手册按四个入口组织：Transport 等待匹配终态；设备 blocker 查询与精确重处理；WMS 可靠义务查询；prepare 作废后重新求值。每节写明前提、查看字段、允许动作、恢复后复核和停止条件。
- [ ] 写清 Transport 纠正结果通过原 WMS Event ingress 提交权威事实，消息纠正遵循当前幂等合同；WES 页面不提供“填成功状态”按钮。数据清理脚本不属于恢复步骤。
- [ ] 运行所改前端聚焦测试及真实浏览器操作。未改后端行为则只复用现有证据；后端若改动，刷新对应测试和 selector HEAVY，设备人工对账还需真实 PostgreSQL 并发/审计测试。
- [ ] 请未参与实现的工程师按手册处理固定测试案例，记录定位时间、所需信息和人工操作次数，回填到项目外验收记录。

**验收：** 原事实、原身份及实际操作结果可核对；不能将“清理本轮数据”“重复搬运”“恢复证据处理”混为一项操作。

### Task B3：独立验收当前联调消费者，不替代插件业务验收

**Files:**
- Inspect: `src/app/transport/debug_run_service.py`、`src/app/workline_integration_debug/service.py`。
- Reuse tests: `tests/runtime/transport/test_transport_debug_run_advancement.py`、`tests/integration/transport/test_transport_debug_run_recovery.py`。
- Docs: `docs/integration/transport-joint-acceptance.md`、`docs/devops/execution-recovery.md`；测试新增时同步 HEAVY mapping。

**Interfaces:** 原 `TransportDebugRunService.advance_run` 消费同一 task 的单调 outcome；不新建通用 resume 接口。其返回值表示持久状态是否变化，不等同于“进入下一阶段”。

- [ ] 核对已有迟到成功、部分成员成功、重复结果和缺失位置事实案例；已覆盖的行为不重复创建测试。
- [ ] 补缺失的服务重启恢复案例：已完成成员不重复下发；缺失成员仍等待；同一任务更高版本权威结果可推进；重复版本不产生新动作；转面/返库仍等待合同要求的全部物理闭合。
- [ ] 使用独立 PostgreSQL 验证 run 快照从数据库恢复，不能用浏览器状态重建任务；若失败仅修改对应联调 consumer，基础状态机不承担“该不该转面”的业务决定。
- [ ] 运行聚焦领域测试和该集成 owner；报告“通用联调 consumer 恢复已验证”。正式插件的 FIFO、NG、任务切换和因果恢复仍由该插件包测试，不在此添加实际工作线用例。
- [ ] 在联合验收文档分别列出基础可靠性证据、联调 consumer 证据、供应商终态合同证据、正式业务尚未验收项。不以这一轮成功宣称全业务恢复完成。

## 完成门禁

- [ ] B1 生成物与 A 合同一致；B2 操作通过既有服务器准入；B3 不越过物理队头或未闭合成员。
- [ ] 前端聚焦测试、合同/权限验证和浏览器 QA 通过；后端仅执行被实际改动影响的门禁。
- [ ] 文档写入及本轮规划不编写 pytest；实现后纯文案变化不重复完整 QUALITY/HEAVY。
