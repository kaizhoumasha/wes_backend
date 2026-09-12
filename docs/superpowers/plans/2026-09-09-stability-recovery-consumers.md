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

**2026-09-12 修订：** 本任务原先依赖的设备 blocker 查询/重处理、device-idle 人工对账和计划人工纠正接口，已由
[WES 无阻塞执行设计](../specs/2026-09-11-wes-nonblocking-execution-design.md) ENG-D4/ENG-D5 取代并从后端删除。ECS 独立处理急停、
复位与恢复；WES 不接收急停 Event，不允许操作员用人工结果关闭原 DeviceCommand。计划修正只走普通 `plan_delta` record/replay。

**当前范围：**

- 后端只保留 DeviceCommand、TransportTask、Evidence 与 WmsConfirmation 的只读诊断，以及各自既有的权威结果/可靠重试路径。
- 恢复手册说明原身份、匹配 Evidence、等待状态和停止条件；不提供填成功状态、重处理 blocker、伪造设备空闲或人工应用计划修正的按钮。
- 前端必须从 clean backend `develop` 冻结 canonical contract 后删除已退役操作；当前未满足该门禁，不手改 snapshot、generated types 或 DTO。
- Transport 结果仍经原 WMS Event ingress 进入并只应用于匹配任务；WES 不换身份重发已接纳物理动作。prepare 重新求值继续遵守其独立合同。

**验收：** 原事实、原身份及实际结果可核对；页面不暴露已退役人工续行路径，也不把清理测试数据、重复搬运和证据恢复混为一项操作。

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

- [ ] B1 生成物与 A 合同一致；B2 不暴露已退役人工续行操作；B3 不越过物理队头或未闭合成员。
- [ ] 前端聚焦测试、合同/权限验证和浏览器 QA 通过；后端仅执行被实际改动影响的门禁。
- [ ] 文档写入及本轮规划不编写 pytest；实现后纯文案变化不重复完整 QUALITY/HEAVY。
