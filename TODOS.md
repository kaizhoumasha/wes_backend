# TODOS

> 2026-06-24 清理说明：WorkLine/plugin 目标态重构已成为设计真源。被
> `docs/architecture/workline-and-plugin-restructuring.md` 吸收的旧 WorkLine、plugin、
> queue、WMS ACL、reconciliation、monitoring、benchmark 和 DeviceCommand 待办不再在
> active TODO 中重复维护。active TODO 只保留独立于本次重构主线、仍需要后续排期的事项。

## P1 - TimescaleDB audit_logs hypertable 落地

**What**: 将 `wes_sys.audit_logs` 按 `opera_time` 转为 TimescaleDB hypertable，并补齐审计日志的时间分区、索引和保留策略。

**Why**: `audit_logs` 是持续追加的历史审计事实表，主要按时间、用户、操作类型、对象类型和状态检索。当前 TimescaleDB 已启用但没有任何 hypertable，系统承担扩展成本却未使用核心能力；先从低耦合审计日志表试点，风险最低。

**Context**: 2026-06-11 TimescaleDB 审计确认：`wes_db` 中 `timescaledb_information.hypertables = 0`，`audit_logs` 无外键引用，现有索引包括 `opera_time`、`trace_id`、`username`、`(object_type, opera_time)`、`(action, opera_time)`、`(status, opera_time)`。TimescaleDB 要求 hypertable 上的 `PRIMARY KEY` / `UNIQUE` 索引必须包含分区列，因此不能直接对当前 `PRIMARY KEY(id)` 表执行 `create_hypertable`。

**Scope**:
- 新增 Alembic 迁移，将 `wes_sys.audit_logs` 按 `opera_time` 创建 hypertable，建议初始 `chunk_time_interval = 7 days`
- 迁移前处理 `pk_audit_logs` 和 `ix_wes_sys_audit_logs_id` 的唯一约束冲突：删除 `PRIMARY KEY(id)` / `UNIQUE(id)`，保留普通 `id` 索引用于现有按 ID 查询
- 保留现有时间检索索引：`opera_time`、`trace_id`、`username`、`(object_type, opera_time)`、`(action, opera_time)`、`(status, opera_time)`
- 评估并补充 `username + opera_time DESC` 复合索引，优化审计后台按用户和时间范围检索
- 明确 `id` 唯一性取舍：数据库不再单独强制 `id` 唯一，依赖现有自增或雪花 ID 生成；如需数据库强唯一，必须重构为包含 `opera_time` 的复合主键
- 在生产保留周期明确后添加 retention policy，例如 `add_retention_policy('wes_sys.audit_logs', drop_after => INTERVAL '365 days')`
- 增加验证脚本或迁移测试，确认 `audit_logs` 出现在 `timescaledb_information.hypertables`，并确认原有审计查询仍可用
- 修复本地 dev DB `alembic_version = fb02178f9772` 但仓库缺少该 revision 的阻碍，否则无法完整执行 `alembic current/upgrade head`

**Dependencies**: TimescaleDB worker 配置已落地；本地/测试库 Alembic revision 需先对齐；生产审计日志 retention 周期需确认。

**Effort**: S-M (human: 0.5-1 day / CC: ~30-60 min after Alembic version is aligned)

**Priority**: P1

---

## P1 - WorkLine 域模型 / 仓库物理迁移到 runtime/orchestration 域 (Phase 2 burn-down F-1)

**What**: 把 `src/app/workline/models/` 下 16 个运行态 model 与 `src/app/workline/repositories/` 下 11 个 repository 物理迁入 `src/app/runtime/orchestration/{models,repositories}/`。前置条件:53+ 处 `from src.app.workline.models.{inbox,session,timeline,...}` 跨子包 import 改写到 runtime/orchestration 路径(部分见 F-2)。

**Why**: `feature/phase2-burndown-stage5-6` PR (`v0.10.2.1`) 完成阶段 5+6 主体(workline 域大幅瘦身 + facade 删除 + device_command_gateway 迁出),但 `WorkLine 不再拥有运行状态` 门禁的"models / repositories 物理删除"子门禁 xfail 在 `tests/architecture/test_workline_service_shim_contract.py` 2 处。`safety.py` 与 `safety_incident_repository.py` 例外保留(承载 `WorkLineRuntimeStatus` 跨域 enum 与配置域审计表)。

**Scope**:
- 改写 `src/app/runtime/orchestration/` 与 `src/celery_app/tasks/` 下 28+ 处 `from src.app.workline.{models,repositories}` import
- 物理删除 workline 域 16 个运行态 model(保留 `workline.py` + `safety.py` + `domain/`)
- 物理删除 workline 域 11 个运行态 repository(保留 `workline_repository.py` + `safety_incident_repository.py`)
- 同步 `tests/architecture/test_workline_service_shim_contract.py` 2 个 xfail 转为硬绿
- 同步 `docs/architecture/legacy-cleanup-matrix.{md,csv}` 与 `scripts/architecture-guardrails.allowlist`

**Dependencies**: F-2 跨域 import 改写先行(本条目覆盖的是 model 物理迁出,但 import 改写是 F-2 的 scope)。

**Effort**: M

**Priority**: P1

**Completed:** v0.10.3.0 (2026-06-30) — 14 model + 10 repository 物理迁入 `src/app/runtime/orchestration/{models,repositories}/`,2 个 xfail 契约转硬绿,随 `cbdfcfe8` 提交。

---

## P2 - 28 处 workline 域 import 跨域改写 (Phase 2 burn-down F-2)

**What**: 为 WorkLine 配置台补充按 manifest 设备角色、required/optional 标记、能力缺口和 SafetyZone 影响范围组织的设备绑定向导。

**Why**: 目标态后端会把设备角色、能力、共享设备影响范围和启停门禁固化在 manifest/validator 中；前端仍需要一个低噪声入口帮助运维按缺口补齐配置。

**Scope**:
- 按 manifest required roles 展示待绑定设备、能力要求和 SafetyZone 归属
- 支持从未绑定设备或当前 WorkLine 设备中选择/调整角色
- active / DRAINING / VALIDATING WorkLine 只读展示，提示停线或 drain 后调整
- 复用后端 manifest validator / configuration status 的 blocker、warning 和修复入口

**Dependencies**: WorkLine manifest schema、SafetyZone/shared-device validator 和配置页基础合同稳定。

**Effort**: M

**Priority**: P2

**Completed:** v0.10.3.0 (2026-06-30) — 262 条跨域 import 批量改写(81 文件,`workline.{models,repositories}.<待迁>` → `runtime.orchestration.{models,repositories}.<待迁>`),随 `feature/phase2-burndown-f1-f2` PR 提交。注:TODOS 标题为 import 改写,What 段描述与标题不一致属既有数据漂移,本 Completed 以标题 scope 为准。

---

## P2 - WorkLine manifest 角色优先设备绑定向导

**What**: 为 WorkLine 配置台补充按 manifest 设备角色、required/optional 标记、能力缺口和 SafetyZone 影响范围组织的设备绑定向导。

**Why**: 目标态后端会把设备角色、能力、共享设备影响范围和启停门禁固化在 manifest/validator 中；前端仍需要一个低噪声入口帮助运维按缺口补齐配置。

**Scope**:
- 按 manifest required roles 展示待绑定设备、能力要求和 SafetyZone 归属
- 支持从未绑定设备或当前 WorkLine 设备中选择/调整角色
- active / DRAINING / VALIDATING WorkLine 只读展示，提示停线或 drain 后调整
- 复用后端 manifest validator / configuration status 的 blocker、warning 和修复入口

**Dependencies**: WorkLine manifest schema、SafetyZone/shared-device validator 和配置页基础合同稳定。

**Effort**: M

**Priority**: P2

---

## P2 - 统一运营看板、告警与 Runbook

**What**: 在目标态 runtime、reconciliation、device 和 wms_integration 指标稳定后，建设统一运营看板、告警阈值和现场 Runbook。

**Why**: 旧 TODO 中的 SMT Handoff 看板、RuntimeHold 看板、急停看板、WMS breaker 告警和粗分机监控本质上是同一套运营观测能力。按目标态应合并设计，避免每条业务线重复建看板和告警口径。

**Scope**:
- RuntimeInbox backlog、dead-letter、RESOURCE_WAIT、Outbox BLOCKED_RESOURCE
- DeviceCommand ACK age、dispatch deadline、ECS status probe 失败、设备 ERROR/OFFLINE/MAINTENANCE
- Reconciliation active 数、MTTR、reason、late callback、manual resolve
- WMS breaker OPEN/HALF_OPEN/CLOSED、timeout/5xx/business reject、evidence 写入失败
- Safety incident / ESTOP evidence / shared-device 影响范围
- 现场 Runbook：WMS/RCS 拒绝、Inbox dead-letter、command evidence 缺失、对账 evidence 缺失、设备状态不一致

**Dependencies**: 目标态 observability 指标落地，并产生真实或接近真实的试运行数据。

**Effort**: M-L

**Priority**: P2

---

## P2 - 分拣机/粗分机供应商联调操作手册

**What**: 在目标态 DeviceCommand、callback、RuntimeInbox、WMS fulfillment 和入库能力重建稳定后，编写分拣机/粗分机供应商联调手册。

**Why**: 顶层设计只定义 WES/ECS/WMS 边界和业务合同；供应商联调还需要可执行的 payload 样例、回调样例、异常码、测试步骤和恢复流程。

**Scope**:
- 设备角色、动作 payload、callback result/event 样例
- 正常入库、NG、满箱/换架、设备失败、WMS/RCS 拒绝五类联调场景
- command_code / event_id / trace_id / idempotency_key 使用约定
- ECS 只 ACK Event_Push、WES 通过 Receive Command 下发后续动作的联调步骤

**Dependencies**: 目标态 device command contract、external callback auth、入库能力重建和 WMS fulfillment contract 稳定。

**Effort**: M

**Priority**: P2

---

## P3 - Phase 2 burn-down 阶段 6 评审 follow-ups (F-3..F-7)

**What**: 处理 `feature/phase2-burndown-stage5-6` PR (`v0.10.2.1`) 评审中识别的 5 项 MINOR / 文档 follow-up。

**Why**: 这些项在 PR 内未处理(避免范围扩张),但作为工作线 plugin / RuntimeOrchestration 代码清洁度的小项应该单独排期清账,防止遗留在 backlog。

**Scope**:
- **F-3** `src/app/workline/services/diagnosis_verdict_builder.py` 改名为 `diagnosis_verdict_builder_service.py`,对齐目录内 `_service` 命名约定
- **F-4** `tests/architecture/test_workline_service_shim_contract.py::test_workline_service_config_only_after_stage6` 改 `hasattr` 存在性守卫为行为验证(检查方法可调用 + 返回值类型)
- **F-5** 新增 `tests/architecture/test_cleanup_matrix_guardrail.py` 强制 `docs/architecture/legacy-cleanup-matrix.csv` audit trace 一致性(目前是文档 only)
- **F-6** `src/app/workline/services/__init__.py` `_LAZY_SHIM_MAP` docstring 改写:从"live caller 死引用"夸饰改为"未初始化 service 属性的 fallback"准确描述
- **F-7** 新增 `tests/runtime/orchestration/test_device_command_gateway.py` per plan commit-3 step 3.2.6 锁定 device_command_gateway 迁入后的 runtime 行为

**Dependencies**: 无

**Effort**: S

**Priority**: P3

---

## Completed

## P0 - 修复 test_start_admission_service 预存失败

**What**: `tests/workline_runtime/test_start_admission_service.py` 19 个测试在 develop base 即失败（`START_ADMISSION_CONFIGURATION_INVALID` 等断言过时），与料盘根域分支无关。

**Why**: /ship 第五轮验收发现，pre-existing 失败需单独排查，避免污染后续 PR 的测试信号。

**Context**: 2026-06-22 在 `feature/material-unit-root-domain` 分支 `/ship` 时确认 develop base `ee1f3b67` 同样失败，非本分支回归。

**Scope**:
- 排查 `start_admission_service` 启动准入逻辑与测试 fixture 的 contract version / 配置漂移
- 修复或同步测试断言

**Completed:** v0.8.0.0 (2026-06-22) — fixture `rough_sorter.v1` → `v2` (合同版本同步迁移)，随 `df97828` 提交。

## P1 - Runtime scene 结构化运行资源证据契约

**What**: 为 Rack、Bin、PKG、Slot、Part SN、Magazine 等现场资源证据提供稳定结构化运行字段。

**Why**: `/runtime/monitor` 现场态势图需要显示执行证据，但 WES 不是 WMS 库存事实源。没有结构化契约时，前端只能从 `context_json`、`payload_json`、`event_payload` 猜测资源含义，容易把插件专用 JSON 推断误展示成库存真相。

**Context**: `docs/superpowers/specs/2026-06-05-runtime-workline-scene-monitor-design.md` 工程评审最初接受为后续项；2026-06-08 前端 eng review 后，用户选择本 PR 直接落地逐项 `RuntimeResourceEvidenceItem[]`，不再推迟到 P3。v1 仍明确禁止前端 raw JSON resource badge inference。

**Scope**:
- 定义资源证据字段的来源、命名、生命周期和权限边界
- 区分执行证据、WMS 回调证据和库存授权/库存真相
- 在 runtime detail 增加稳定资源证据 view
- 补 contract/schema tests，防止前端回到 raw JSON 推断

**Dependencies**: 与 `docs/superpowers/archive/plans/2026-06-06-wes-single-layer-rack-orchestration-boundary-plan.md` 的 runtime detail 合同同步实施。

**Effort**: M (human: ~1-2 days / CC: ~2-3 hours)

**Priority**: P1

**Completed:** 2026-06-08

---

## P1 - 第一个真实 WMS 同步 caller integration + end-to-end RuntimeHold/diagnostic validation

**What**: 选择第一个真实 WMS 同步调用方接入 `wms_integration` typed ports，并做端到端 RuntimeHold/diagnostic validation。

**Why**: 当前 caller contract 通过 fake caller 保护 timeout/5xx/circuit-open 的处理边界；仍需要真实业务调用方验证 evidence_key 传播、RuntimeHold 或诊断创建、用户可见错误和恢复路径。

**Context**: `docs/superpowers/plans/2026-05-26-wms-integration-domain.md` 的 Deferred / TODO Decisions 已接受该后续项，`docs/integration/wms_caller_checklist.md` 是接入检查清单。

**Scope**:
- 选定首个真实 WMS 同步查询或写入场景
- 调用方只依赖 typed ports，不直接 import WMS HTTP client
- timeout/5xx/circuit-open 按 checklist 创建 RuntimeHold 或诊断
- 验证 evidence_key、trace_id、request_id 在业务错误和运维视图中可追踪
- 增加端到端或集成测试覆盖成功、WMS business reject、WMS unavailable 和 breaker open

**Dependencies**: 首个业务接入场景确认，RuntimeHold/diagnostic 入口可用。

**Effort**: M-L

**Priority**: P1

**Completed:** 2026-05-27

## P2 - Runtime reconciliation 系统级处理

**What**: 按 `docs/superpowers/plans/2026-05-08-workline-timeout-system-handling.md` 实现系统级 runtime reconciliation，移除插件 `on_timeout()` 默认 failure 路径。

**Why**: execution Callback timeout 和 dispatch ACK exhausted 都是物理状态未知/通信接受状态未知，不能由插件默认 failure 处理，也不能自动重发物理命令。

**Context**: 已由 `2026-05-08-workline-timeout-system-handling.md` 取代原“Timeout 默认 failure 独立实现”方向。

**Scope**:
- 删除 `@on_timeout()` / `WorklinePlugin.on_timeout()` 兼容路径
- `WorklineRuntimeReconciliationService`
- ACK 后激活 execution deadline
- dispatch ACK exhausted reconciliation
- `BLOCKED_RESOURCE` parked outbox release
- focused runtime reconciliation tests

**Dependencies**: WorkLine 软件侧急停冻结计划完成或独立排期。

**Effort**: M

**Priority**: P2

**Completed:** v0.4.0.0 (2026-05-12)
